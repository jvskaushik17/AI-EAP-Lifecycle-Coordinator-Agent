"""
Persistent EAP state management using SQLite.
Stores customer records, agent activity log, and the approval queue.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = "eap_agent.db"


class EAPState:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS customers (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    eap_name     TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    data         TEXT NOT NULL,
                    updated_at   TEXT NOT NULL,
                    UNIQUE(eap_name, customer_name)
                );

                CREATE TABLE IF NOT EXISTS approval_queue (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at     TEXT NOT NULL,
                    priority       TEXT NOT NULL,
                    action_type    TEXT NOT NULL,
                    customer_name  TEXT,
                    eap_name       TEXT,
                    description    TEXT,
                    draft_content  TEXT,
                    to_address     TEXT DEFAULT '',
                    evidence       TEXT DEFAULT '',
                    status         TEXT DEFAULT 'pending'
                );

                CREATE TABLE IF NOT EXISTS agent_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT NOT NULL,
                    event_type    TEXT NOT NULL,
                    customer_name TEXT DEFAULT '',
                    eap_name      TEXT DEFAULT '',
                    description   TEXT,
                    evidence      TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at        TEXT NOT NULL,
                    completed_at      TEXT,
                    emails_processed  INTEGER DEFAULT 0,
                    teams_processed   INTEGER DEFAULT 0,
                    actions_queued    INTEGER DEFAULT 0,
                    status            TEXT DEFAULT 'running',
                    error             TEXT DEFAULT ''
                );
            """)

    # ── Customer records ─────────────────────────────────────────

    def upsert_customer(self, eap_name: str, customer_name: str, data: dict):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO customers (eap_name, customer_name, data, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (eap_name, customer_name, json.dumps(data), datetime.now().isoformat()),
            )

    def get_customer(self, eap_name: str, customer_name: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT data FROM customers WHERE eap_name=? AND customer_name=?",
                (eap_name, customer_name),
            ).fetchone()
            return json.loads(row["data"]) if row else None

    def get_all_customers(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT eap_name, customer_name, data, updated_at FROM customers"
            ).fetchall()
            result = []
            for r in rows:
                d = json.loads(r["data"])
                d["_eap_name"]    = r["eap_name"]
                d["_customer"]    = r["customer_name"]
                d["_updated_at"]  = r["updated_at"]
                result.append(d)
            return result

    def update_customer_field(self, eap_name: str, customer_name: str, field: str, value: str) -> bool:
        data = self.get_customer(eap_name, customer_name)
        if data is None:
            return False
        data[field] = value
        data["_last_agent_touch"] = datetime.now().isoformat()
        self.upsert_customer(eap_name, customer_name, data)
        return True

    def load_from_csv(self, csv_path: str):
        """Seed the DB from a CSV file (initial import)."""
        import pandas as pd
        df = pd.read_csv(csv_path)
        for _, row in df.iterrows():
            data = {col: ("" if (val != val) else str(val)) for col, val in row.items()}
            eap  = data.get("EAP Name", "Unknown")
            cust = data.get("Customer Name", "Unknown")
            self.upsert_customer(eap, cust, data)
        return len(df)

    # ── Approval queue ───────────────────────────────────────────

    def queue_action(
        self,
        priority: str,
        action_type: str,
        customer_name: str,
        eap_name: str,
        description: str,
        draft_content: str = "",
        to_address: str = "",
        evidence: str = "",
    ):
        with self._conn() as c:
            c.execute(
                "INSERT INTO approval_queue "
                "(created_at, priority, action_type, customer_name, eap_name, "
                " description, draft_content, to_address, evidence) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now().isoformat(), priority, action_type,
                    customer_name, eap_name, description,
                    draft_content, to_address, evidence,
                ),
            )

    def get_pending_actions(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM approval_queue WHERE status='pending' "
                "ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, created_at"
            ).fetchall()
            return [dict(r) for r in rows]

    def update_action_status(self, action_id: int, status: str):
        with self._conn() as c:
            c.execute("UPDATE approval_queue SET status=? WHERE id=?", (status, action_id))

    def count_pending(self) -> dict:
        with self._conn() as c:
            rows = c.execute(
                "SELECT priority, COUNT(*) as n FROM approval_queue "
                "WHERE status='pending' GROUP BY priority"
            ).fetchall()
            return {r["priority"]: r["n"] for r in rows}

    # ── Agent log ────────────────────────────────────────────────

    def log(self, event_type: str, description: str,
            customer_name: str = "", eap_name: str = "", evidence: str = ""):
        with self._conn() as c:
            c.execute(
                "INSERT INTO agent_log "
                "(timestamp, event_type, customer_name, eap_name, description, evidence) "
                "VALUES (?,?,?,?,?,?)",
                (datetime.now().isoformat(), event_type,
                 customer_name, eap_name, description, evidence),
            )

    def get_log(self, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM agent_log ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Run tracking ─────────────────────────────────────────────

    def start_run(self) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO agent_runs (started_at, status) VALUES (?, 'running')",
                (datetime.now().isoformat(),),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, emails: int, teams: int, actions: int, error: str = ""):
        with self._conn() as c:
            c.execute(
                "UPDATE agent_runs SET completed_at=?, emails_processed=?, "
                "teams_processed=?, actions_queued=?, status=?, error=? WHERE id=?",
                (
                    datetime.now().isoformat(), emails, teams, actions,
                    "error" if error else "completed", error, run_id,
                ),
            )

    def last_run(self) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM agent_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
