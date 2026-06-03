"""
Background agent runner — polls Outlook + Teams on a schedule.
Run this in a separate terminal alongside the dashboard:

    python run_agent.py

It reads config from environment variables:
    ANTHROPIC_API_KEY   — required
    MS_TENANT_ID        — required for Outlook/Teams
    MS_CLIENT_ID        — required for Outlook/Teams
    POLL_INTERVAL_MIN   — minutes between cycles (default: 15)
    HOURS_BACK          — how far back to look per cycle (default: 1)
"""

import os
import time
from datetime import datetime

from agent import EAPAgent
from state import EAPState

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TENANT_ID     = os.getenv("MS_TENANT_ID", "")
CLIENT_ID     = os.getenv("MS_CLIENT_ID", "")
INTERVAL_MIN  = int(os.getenv("POLL_INTERVAL_MIN", "15"))
HOURS_BACK    = int(os.getenv("HOURS_BACK", "1"))


def main():
    if not ANTHROPIC_KEY:
        print("ERROR: Set ANTHROPIC_API_KEY environment variable.")
        return

    state = EAPState()

    graph = None
    if TENANT_ID and CLIENT_ID:
        from graph_client import GraphClient
        graph = GraphClient(CLIENT_ID, TENANT_ID)
        if not graph.is_authenticated():
            print("Microsoft Graph not authenticated.")
            print("Run agent_app.py and connect via the sidebar first.")
            print("Continuing without Outlook/Teams — paste-and-analyze still works.\n")

    agent = EAPAgent(ANTHROPIC_KEY, state, graph)

    print(f"EAP Agent started — polling every {INTERVAL_MIN} minutes")
    print(f"Dashboard: streamlit run agent_app.py\n")

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] Running agent cycle…")

        result = agent.run_cycle(hours_back=HOURS_BACK)

        if result["status"] == "completed":
            print(
                f"  ✓ {result['emails']} emails · {result['teams']} Teams msgs · "
                f"{result['actions_queued']} actions queued"
            )
        else:
            print(f"  ✗ Error: {result.get('error','unknown')}")

        print(f"  Next run in {INTERVAL_MIN} min…\n")
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
