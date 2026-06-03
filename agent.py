"""
Claude-powered EAP agent.

Uses Claude claude-sonnet-4-6 with tool use to analyze Outlook emails and Teams messages,
detect EAP lifecycle signals, update customer state, and queue draft actions
for human approval.
"""

import json
import re
import time
from datetime import datetime

import anthropic

from state import EAPState

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are an intelligent EAP (Early Access Program) Lifecycle Coordinator Agent for Infoblox.

Your job is to silently monitor business emails and Microsoft Teams messages and:
1. Detect when a customer's EAP status has changed based on what they say
2. Update the customer's EAP record in the database when you detect a clear signal
3. Identify customers that need follow-up and queue draft emails for human approval
4. Flag urgent situations immediately

EAP Lifecycle Stages (in order):
  Recruitment → Kickoff Scheduling → Kickoff Completed → Registration Follow-Up
  → Onboarding Scheduling → Onboarding Completed → Build Installation Follow-Up
  → Active Testing → Feedback Scheduling → Feedback Collection → Jira Creation
  → PM/Engineering Follow-Up → Customer Update → Close-Out → Completed

Field names in the database (exact spelling matters):
  "Kickoff Completed", "Registration Completed", "Onboarding Completed",
  "Build Shared", "Build Downloaded / Installed", "Product Actively Testing",
  "Feedback Received", "Jira Ticket Created", "Customer Updated",
  "Close-Out Email Sent", "Survey Sent", "Customer Interest Status",
  "Current Stage", "Notes / Evidence"

Signal detection examples:
  Email: "we finally got the build installed yesterday"
    → update_customer_field(field="Build Downloaded / Installed", value="Yes")

  Teams: "quick question — any progress on our jira ticket?"
    → flag_action(priority="High", action="PM/Engineering follow-up overdue — customer asking")

  Email: "we've been actively testing for the past week"
    → update_customer_field(field="Product Actively Testing", value="Yes")

  Email: "we're not going to be able to participate this quarter"
    → update_customer_field(field="Customer Interest Status", value="Dropped")
    → flag_action(priority="High", action="Customer dropped — EAP record needs update")

  Teams: "can we schedule some time to give you our feedback?"
    → queue_draft_email for feedback session scheduling

Rules:
- Always call get_all_eap_customers first to understand current state
- Only update a field when you have CLEAR explicit evidence from the message
- Never assume — if unclear, log_insight instead of updating
- Queue draft emails only when communication clearly warrants follow-up
- Prioritize: anything where customer is waiting or asking for response = High
- Do not queue duplicate drafts for the same customer/action already pending
- Be concise in evidence quotes — use the actual words from the message
"""

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_all_eap_customers",
        "description": "Retrieve current state of all EAP customers from the database. Always call this first.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "update_customer_field",
        "description": "Update a field in a customer's EAP record when you detect a clear signal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "eap_name":      {"type": "string", "description": "EAP program name"},
                "customer_name": {"type": "string", "description": "Customer company name"},
                "field":         {"type": "string", "description": "Exact field name to update"},
                "value":         {"type": "string", "description": "New value"},
                "evidence":      {"type": "string", "description": "Direct quote from the email/message that supports this update"},
            },
            "required": ["eap_name", "customer_name", "field", "value", "evidence"],
        },
    },
    {
        "name": "queue_draft_email",
        "description": "Queue a draft email for human review and approval. Write it ready-to-send.",
        "input_schema": {
            "type": "object",
            "properties": {
                "priority":      {"type": "string", "enum": ["High", "Medium", "Low"]},
                "customer_name": {"type": "string"},
                "eap_name":      {"type": "string"},
                "to_address":    {"type": "string", "description": "Recipient email address"},
                "subject":       {"type": "string"},
                "body":          {"type": "string", "description": "Complete, professional email body ready to send"},
                "reason":        {"type": "string", "description": "Why this email is needed"},
                "evidence":      {"type": "string", "description": "What in the communication triggered this"},
            },
            "required": ["priority", "customer_name", "eap_name", "to_address", "subject", "body", "reason", "evidence"],
        },
    },
    {
        "name": "flag_action",
        "description": "Flag an important action or situation for the user's attention without drafting an email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "priority":      {"type": "string", "enum": ["High", "Medium", "Low"]},
                "customer_name": {"type": "string"},
                "eap_name":      {"type": "string"},
                "action_type":   {"type": "string", "description": "Short label for the action type"},
                "description":   {"type": "string", "description": "What needs to happen and why"},
                "evidence":      {"type": "string", "description": "Exact quote or reference from the communication"},
            },
            "required": ["priority", "customer_name", "eap_name", "action_type", "description", "evidence"],
        },
    },
    {
        "name": "log_insight",
        "description": "Log an observation that is EAP-related but doesn't require immediate action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "eap_name":      {"type": "string"},
                "insight":       {"type": "string"},
            },
            "required": ["customer_name", "eap_name", "insight"],
        },
    },
]


# ── Agent class ───────────────────────────────────────────────────────────────

class EAPAgent:
    def __init__(self, anthropic_api_key: str, state: EAPState, graph_client=None):
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.state  = state
        self.graph  = graph_client
        self.model  = "claude-sonnet-4-6"

    # ── Tool execution ────────────────────────────────────────────

    def _execute_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "get_all_eap_customers":
                customers = self.state.get_all_customers()
                # Summarise to save tokens
                summary = [
                    {
                        "customer":   c.get("Customer Name") or c.get("_customer"),
                        "eap":        c.get("EAP Name") or c.get("_eap_name"),
                        "stage":      c.get("Current Stage", ""),
                        "interest":   c.get("Customer Interest Status", ""),
                        "testing":    c.get("Product Actively Testing", ""),
                        "feedback":   c.get("Feedback Received", ""),
                        "jira":       c.get("Jira Ticket ID", ""),
                        "touchpoint": c.get("Last Customer Touchpoint Date", ""),
                    }
                    for c in customers
                ]
                return json.dumps(summary)

            elif name == "update_customer_field":
                ok = self.state.update_customer_field(
                    inp["eap_name"], inp["customer_name"], inp["field"], inp["value"]
                )
                if ok:
                    self.state.log(
                        "field_update",
                        f"Updated '{inp['field']}' → '{inp['value']}'",
                        customer_name=inp["customer_name"],
                        eap_name=inp["eap_name"],
                        evidence=inp.get("evidence", ""),
                    )
                return json.dumps({"success": ok})

            elif name == "queue_draft_email":
                draft = f"Subject: {inp['subject']}\n\n{inp['body']}"
                self.state.queue_action(
                    priority=inp["priority"],
                    action_type="send_email",
                    customer_name=inp["customer_name"],
                    eap_name=inp["eap_name"],
                    description=inp["reason"],
                    draft_content=draft,
                    to_address=inp["to_address"],
                    evidence=inp["evidence"],
                )
                self.state.log(
                    "email_queued",
                    f"{inp['priority']} email queued → {inp['to_address']}: {inp['subject']}",
                    customer_name=inp["customer_name"],
                    eap_name=inp["eap_name"],
                )
                return json.dumps({"queued": True})

            elif name == "flag_action":
                self.state.queue_action(
                    priority=inp["priority"],
                    action_type=inp["action_type"],
                    customer_name=inp["customer_name"],
                    eap_name=inp["eap_name"],
                    description=inp["description"],
                    draft_content="",
                    evidence=inp["evidence"],
                )
                self.state.log(
                    "action_flagged",
                    f"{inp['priority']}: {inp['description']}",
                    customer_name=inp["customer_name"],
                    eap_name=inp["eap_name"],
                )
                return json.dumps({"flagged": True})

            elif name == "log_insight":
                self.state.log(
                    "insight",
                    inp["insight"],
                    customer_name=inp["customer_name"],
                    eap_name=inp["eap_name"],
                )
                return json.dumps({"logged": True})

        except Exception as exc:
            return json.dumps({"error": str(exc)})

        return json.dumps({"error": f"Unknown tool: {name}"})

    # ── Agentic loop ──────────────────────────────────────────────

    def _run_loop(self, messages: list) -> int:
        """Run Claude with tool use until it stops. Returns count of tool calls made."""
        tool_calls = 0
        for _ in range(20):  # safety cap
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
            if resp.stop_reason == "end_turn":
                break
            if resp.stop_reason != "tool_use":
                break

            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    out = self._execute_tool(block.name, block.input)
                    results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     out,
                    })
                    tool_calls += 1

            messages = messages + [
                {"role": "assistant", "content": resp.content},
                {"role": "user",      "content": results},
            ]

        return tool_calls

    # ── Communication formatting ──────────────────────────────────

    @staticmethod
    def _fmt_emails(emails: list) -> str:
        if not emails:
            return "None"
        lines = []
        for i, e in enumerate(emails[:25], 1):
            sender = (
                e.get("from", {}).get("emailAddress", {}).get("address", "")
                or e.get("from", {}).get("emailAddress", {}).get("name", "unknown")
            )
            lines.append(
                f"[EMAIL {i}]\n"
                f"From: {sender}\n"
                f"Subject: {e.get('subject','')}\n"
                f"Date: {e.get('receivedDateTime','')}\n"
                f"Preview: {e.get('bodyPreview','')[:400]}\n"
            )
        return "\n".join(lines)

    @staticmethod
    def _fmt_teams(messages: list) -> str:
        if not messages:
            return "None"
        lines = []
        for i, m in enumerate(messages[:25], 1):
            sender = (
                (m.get("from") or {}).get("user", {}).get("displayName", "unknown")
            )
            body = re.sub(r"<[^>]+>", " ", m.get("body", {}).get("content", ""))[:400]
            lines.append(
                f"[TEAMS {i}] Chat: {m.get('_chat_topic','')}\n"
                f"From: {sender}\n"
                f"Time: {m.get('createdDateTime','')}\n"
                f"Message: {body}\n"
            )
        return "\n".join(lines)

    # ── Public API ────────────────────────────────────────────────

    def analyze(self, emails: list, teams_messages: list) -> int:
        """
        Analyze a batch of communications. Returns count of actions taken.
        """
        if not emails and not teams_messages:
            self.state.log("cycle", "No new communications.")
            return 0

        prompt = f"""
Analyze these new communications for EAP lifecycle signals.

=== OUTLOOK EMAILS ({len(emails)}) ===
{self._fmt_emails(emails)}

=== TEAMS MESSAGES ({len(teams_messages)}) ===
{self._fmt_teams(teams_messages)}

Instructions:
1. Call get_all_eap_customers to understand current state.
2. For each relevant signal, call update_customer_field with clear evidence.
3. Queue draft emails where a response is clearly needed.
4. Flag any urgent situations.
5. Log minor insights.
Ignore emails clearly unrelated to EAP programs.
"""
        n = self._run_loop([{"role": "user", "content": prompt}])
        self.state.log("cycle", f"Processed {len(emails)} emails, {len(teams_messages)} Teams msgs — {n} tool calls")
        return n

    def run_cycle(self, hours_back: int = 1) -> dict:
        """
        Full agent cycle: fetch → analyze → return summary.
        Call this from the scheduler or the UI "Run Now" button.
        """
        run_id = self.state.start_run()
        emails, teams_msgs = [], []

        try:
            if self.graph and self.graph.is_authenticated():
                emails = self.graph.get_recent_emails(hours=hours_back)
                try:
                    teams_msgs = self.graph.get_chat_messages(hours=hours_back)
                except Exception:
                    pass  # Teams chat may need additional admin consent

            n_actions = self.analyze(emails, teams_msgs)
            self.state.finish_run(run_id, len(emails), len(teams_msgs), n_actions)
            return {
                "status":         "completed",
                "emails":         len(emails),
                "teams":          len(teams_msgs),
                "actions_queued": n_actions,
            }

        except Exception as exc:
            self.state.finish_run(run_id, len(emails), len(teams_msgs), 0, str(exc))
            return {"status": "error", "error": str(exc)}

    def analyze_text(self, raw_text: str, source_label: str = "Manual input") -> int:
        """
        Analyze arbitrary pasted text (email body, Teams transcript, etc.)
        Useful when Graph API is not connected.
        """
        prompt = f"""
Analyze the following communication for EAP lifecycle signals.
Source: {source_label}

=== CONTENT ===
{raw_text[:6000]}

Instructions:
1. Call get_all_eap_customers to understand current state.
2. Extract any EAP-related signals and take appropriate actions.
"""
        return self._run_loop([{"role": "user", "content": prompt}])
