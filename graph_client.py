"""
Microsoft Graph API client.
Handles OAuth (device code flow — user authenticates once in browser),
Outlook email reading, and Teams message reading.

Setup: Register an Azure AD app at portal.azure.com
  → App Registrations → New → note Client ID and Tenant ID
  → API Permissions → add: Mail.Read, Chat.Read, ChannelMessage.Read.All,
                            User.Read, offline_access
  → Authentication → Mobile/desktop → enable "Allow public client flows"
"""

import json
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRAPH_BASE  = "https://graph.microsoft.com/v1.0"
TOKEN_FILE  = ".graph_token.json"
AUTH_BASE   = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
SCOPES      = "Mail.Read Chat.Read ChannelMessage.Read.All User.Read offline_access"


class GraphClient:
    def __init__(self, client_id: str, tenant_id: str):
        self.client_id  = client_id
        self.tenant_id  = tenant_id
        self._token     = None
        self._expiry    = 0.0
        self._refresh   = None
        self._load_saved_token()

    # ── Token management ─────────────────────────────────────────

    def _load_saved_token(self):
        try:
            data = json.loads(Path(TOKEN_FILE).read_text())
            self._token   = data.get("access_token")
            self._refresh = data.get("refresh_token")
            self._expiry  = data.get("expiry", 0.0)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_token(self, data: dict):
        data["expiry"] = time.time() + data.get("expires_in", 3600) - 120
        Path(TOKEN_FILE).write_text(json.dumps(data))
        self._token   = data["access_token"]
        self._refresh = data.get("refresh_token", self._refresh)
        self._expiry  = data["expiry"]

    def _refresh_token(self):
        r = requests.post(
            AUTH_BASE.format(tenant=self.tenant_id) + "/token",
            data={
                "client_id":     self.client_id,
                "grant_type":    "refresh_token",
                "refresh_token": self._refresh,
                "scope":         SCOPES,
            },
            timeout=15,
        )
        r.raise_for_status()
        self._save_token(r.json())

    def _headers(self) -> dict:
        if time.time() >= self._expiry and self._refresh:
            self._refresh_token()
        return {"Authorization": f"Bearer {self._token}"}

    def is_authenticated(self) -> bool:
        return bool(self._token or self._refresh)

    # ── Device-code OAuth (called from UI) ───────────────────────

    def start_auth(self) -> dict:
        """
        Step 1: returns {"user_code", "verification_uri", "device_code", "expires_in"}.
        Display user_code and verification_uri to the user.
        """
        r = requests.post(
            AUTH_BASE.format(tenant=self.tenant_id) + "/devicecode",
            data={"client_id": self.client_id, "scope": SCOPES},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def poll_auth(self, device_code: str) -> bool:
        """
        Step 2: poll until the user has completed browser authentication.
        Returns True once tokens are saved.
        """
        r = requests.post(
            AUTH_BASE.format(tenant=self.tenant_id) + "/token",
            data={
                "client_id":   self.client_id,
                "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
            timeout=15,
        )
        data = r.json()
        if "access_token" in data:
            self._save_token(data)
            return True
        return False  # still waiting (authorization_pending)

    # ── Outlook ──────────────────────────────────────────────────

    def get_recent_emails(self, hours: int = 24, max_results: int = 50) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        r = requests.get(
            f"{GRAPH_BASE}/me/messages",
            headers=self._headers(),
            params={
                "$filter":  f"receivedDateTime ge {since}",
                "$orderby": "receivedDateTime desc",
                "$top":     max_results,
                "$select":  "id,subject,from,toRecipients,ccRecipients,"
                            "receivedDateTime,bodyPreview,importance",
            },
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("value", [])

    # ── Teams ────────────────────────────────────────────────────

    def get_chat_messages(self, hours: int = 24) -> list[dict]:
        """Read recent 1:1 and group chat messages."""
        messages = []
        r = requests.get(
            f"{GRAPH_BASE}/me/chats",
            headers=self._headers(),
            params={"$top": 30, "$select": "id,topic,chatType"},
            timeout=20,
        )
        if r.status_code != 200:
            return []

        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        for chat in r.json().get("value", []):
            chat_id = chat["id"]
            r2 = requests.get(
                f"{GRAPH_BASE}/me/chats/{chat_id}/messages",
                headers=self._headers(),
                params={"$top": 30},
                timeout=20,
            )
            if r2.status_code != 200:
                continue
            for msg in r2.json().get("value", []):
                created = msg.get("createdDateTime", "")
                try:
                    msg_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if msg_dt < since:
                        continue
                except ValueError:
                    pass
                msg["_chat_topic"] = chat.get("topic") or "Direct Message"
                msg["_chat_type"]  = chat.get("chatType", "")
                messages.append(msg)

        return messages

    def get_channel_messages(self, team_id: str, channel_id: str, hours: int = 24) -> list[dict]:
        """Read messages from a specific Teams channel."""
        r = requests.get(
            f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages",
            headers=self._headers(),
            params={"$top": 50},
            timeout=20,
        )
        if r.status_code != 200:
            return []
        return r.json().get("value", [])

    # ── Email sending ─────────────────────────────────────────────

    def send_email(self, to: str, subject: str, body: str) -> bool:
        payload = {
            "message": {
                "subject": subject,
                "body":    {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            }
        }
        r = requests.post(
            f"{GRAPH_BASE}/me/sendMail",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        return r.status_code == 202

    @staticmethod
    def strip_html(html: str) -> str:
        return re.sub(r"<[^>]+>", " ", html).strip()
