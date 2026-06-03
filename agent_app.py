"""
AI EAP Agent Dashboard
Streamlit UI for the Claude-powered EAP lifecycle agent.

Run alongside the agent:
    Terminal 1:  streamlit run agent_app.py
    Terminal 2:  python run_agent.py          (optional — for automated polling)
"""

import time
import threading
from datetime import datetime

import pandas as pd
import streamlit as st

from state import EAPState

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="EAP Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
.block-container { padding-top: 1rem; }

.banner {
    background: linear-gradient(90deg, #0d1b4b 0%, #1565c0 60%, #1976d2 100%);
    color: white; padding: 18px 28px 14px; border-radius: 12px; margin-bottom: 18px;
}
.banner h1 { margin: 0 0 2px; font-size: 1.6em; font-weight: 700; }
.banner p  { margin: 0; opacity: .8; font-size: .9em; }

.pulse { display:inline-block; width:10px; height:10px; border-radius:50%;
         background:#4caf50; animation: pulse 1.5s infinite; margin-right:6px; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

.metric-box { background:white; border:1px solid #e3e8f0; border-radius:10px;
              padding:14px; text-align:center; box-shadow:0 1px 3px rgba(0,0,0,.05); }
.metric-box .num { font-size:2em; font-weight:800; line-height:1.1; }
.metric-box .lbl { font-size:.72em; text-transform:uppercase; color:#6b7a99;
                   letter-spacing:.6px; margin-top:2px; }

.badge { display:inline-block; padding:2px 10px; border-radius:12px;
         font-size:.75em; font-weight:700; margin-right:4px; }
.b-high   { background:#fde8e8; color:#b71c1c; border:1px solid #ef9a9a; }
.b-medium { background:#fff8e1; color:#e65100; border:1px solid #ffd54f; }
.b-low    { background:#e8f5e9; color:#1b5e20; border:1px solid #a5d6a7; }

.log-row { font-size:.82em; color:#546; border-bottom:1px solid #f0f0f0; padding:4px 0; }
.log-ts   { color:#90a4ae; font-family:monospace; }
.log-type { font-weight:700; color:#1a237e; }

.signal-card {
    background:#f8faff; border-left:4px solid #1976d2;
    border-radius:6px; padding:10px 14px; margin:6px 0;
}
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

state = EAPState()

def badge(priority: str) -> str:
    cls = {"High": "b-high", "Medium": "b-medium", "Low": "b-low"}.get(priority, "b-low")
    return f'<span class="badge {cls}">{priority.upper()}</span>'

def icon(event_type: str) -> str:
    return {
        "field_update":  "✏️",
        "email_queued":  "✉️",
        "action_flagged":"🚩",
        "insight":       "💡",
        "cycle":         "🔄",
        "error":         "❌",
    }.get(event_type, "•")

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Agent Setup")

    # ── Anthropic API key ─────────────────────────────────────────
    st.markdown("### Claude API")
    api_key = st.text_input(
        "Anthropic API Key",
        value=st.session_state.get("api_key", ""),
        type="password",
        placeholder="sk-ant-...",
    )
    if api_key:
        st.session_state["api_key"] = api_key
        st.success("API key saved ✓")

    st.divider()

    # ── Microsoft Graph ───────────────────────────────────────────
    st.markdown("### Microsoft 365")
    st.caption("Register a free Azure AD app to connect Outlook + Teams → [guide](https://github.com/jvskaushik17/ai-eap-lifecycle-coordinator-agent/blob/main/SETUP_AGENT.md)")

    tenant_id = st.text_input(
        "Tenant ID",
        value=st.session_state.get("tenant_id", ""),
        placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    )
    client_id = st.text_input(
        "Client ID (App ID)",
        value=st.session_state.get("client_id", ""),
        placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    )
    if tenant_id:
        st.session_state["tenant_id"] = tenant_id
    if client_id:
        st.session_state["client_id"] = client_id

    graph_ready = bool(tenant_id and client_id)

    if graph_ready:
        try:
            from graph_client import GraphClient
            if "graph" not in st.session_state:
                st.session_state["graph"] = GraphClient(client_id, tenant_id)
            gc = st.session_state["graph"]

            if gc.is_authenticated():
                st.success("🟢 Connected to Microsoft 365")
            else:
                if st.button("🔑 Connect Outlook + Teams", use_container_width=True):
                    auth_info = gc.start_auth()
                    st.session_state["device_code"]  = auth_info["device_code"]
                    st.session_state["auth_pending"]  = True
                    st.info(
                        f"**Step 1:** Go to **{auth_info['verification_uri']}**\n\n"
                        f"**Step 2:** Enter code: `{auth_info['user_code']}`\n\n"
                        f"**Step 3:** Click the button below once done."
                    )

                if st.session_state.get("auth_pending"):
                    if st.button("✅ I've authenticated — complete connection"):
                        device_code = st.session_state.get("device_code", "")
                        for _ in range(10):
                            if gc.poll_auth(device_code):
                                st.success("Connected! ✓")
                                st.session_state["auth_pending"] = False
                                st.rerun()
                            time.sleep(3)
                        st.error("Authentication timed out. Try again.")
        except ImportError:
            st.warning("Install `requests` to enable Graph API.")
    else:
        st.caption("Enter Tenant ID and Client ID above to connect.")

    st.divider()

    # ── EAP data seed ─────────────────────────────────────────────
    st.markdown("### EAP Data")
    uploaded = st.file_uploader("Import CSV (seeds the database)", type=["csv","xlsx"])
    if uploaded:
        import io
        df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
        df.to_csv("/tmp/_eap_upload.csv", index=False)
        n = state.load_from_csv("/tmp/_eap_upload.csv")
        st.success(f"Imported {n} customers ✓")

    if st.button("Load sample data", use_container_width=True):
        n = state.load_from_csv("sample_eap_data.csv")
        st.success(f"Loaded {n} sample customers ✓")

    st.divider()

    # ── Agent settings ─────────────────────────────────────────────
    st.markdown("### Polling")
    interval = st.selectbox("Check interval", ["5 min","15 min","30 min","1 hour"], index=1)
    hours_back = st.slider("Look-back window (hours)", 1, 48, 4)
    st.session_state["hours_back"] = hours_back
    st.caption("Set interval and click Run Now or use run_agent.py for automatic polling.")

    st.divider()
    st.caption("EAP Agent v2.0 · Powered by Claude claude-sonnet-4-6")

# ── Banner ────────────────────────────────────────────────────────────────────

last = state.last_run()
last_time = last["completed_at"][:16].replace("T"," ") if last and last.get("completed_at") else "Never"
pending_n  = sum(state.count_pending().values())

st.markdown(f"""
<div class="banner">
  <h1>🤖 EAP Lifecycle Agent</h1>
  <p>Last scan: {last_time} &nbsp;·&nbsp; {pending_n} action{'s' if pending_n != 1 else ''} awaiting approval</p>
</div>
""", unsafe_allow_html=True)

# ── Run Now button ────────────────────────────────────────────────────────────

rc1, rc2, rc3, _ = st.columns([2, 2, 2, 4])

api_key_ok = bool(st.session_state.get("api_key"))
gc_ok      = "graph" in st.session_state and st.session_state["graph"].is_authenticated()

with rc1:
    if st.button("▶️ Run Agent Now", type="primary", use_container_width=True,
                 disabled=not api_key_ok,
                 help="Scans Outlook + Teams and updates the queue."):
        from agent import EAPAgent
        graph = st.session_state.get("graph") if gc_ok else None
        agent = EAPAgent(st.session_state["api_key"], state, graph)

        with st.spinner(f"Agent scanning last {st.session_state.get('hours_back',4)} hours…"):
            result = agent.run_cycle(hours_back=st.session_state.get("hours_back", 4))

        if result["status"] == "completed":
            st.success(
                f"✅ Done — {result['emails']} emails · {result['teams']} Teams msgs · "
                f"{result['actions_queued']} new actions queued"
            )
        else:
            st.error(f"Agent error: {result.get('error','unknown')}")
        st.rerun()

with rc2:
    st.caption("Paste text" if not api_key_ok else "")

with rc3:
    if st.button("🔄 Refresh Dashboard", use_container_width=True):
        st.rerun()

if not api_key_ok:
    st.warning("⚠️  Add your Anthropic API key in the sidebar to run the agent.")

# ── Paste-and-analyze (no Graph required) ─────────────────────────────────────

with st.expander("📋 Paste email or Teams message for instant analysis (no Graph API needed)", expanded=False):
    pasted_text = st.text_area(
        "Paste email body, Teams message, or any EAP-related communication:",
        height=180,
        placeholder="Paste the raw text of an email or Teams message here…",
    )
    source_label = st.text_input("Source label", placeholder="e.g., Email from Acme Corp 2026-06-03")
    if st.button("🤖 Analyze this text", disabled=not (api_key_ok and pasted_text.strip())):
        from agent import EAPAgent
        agent = EAPAgent(st.session_state["api_key"], state)
        with st.spinner("Analyzing…"):
            n = agent.analyze_text(pasted_text, source_label or "Pasted text")
        st.success(f"Analysis complete — {n} tool calls made. Check the tabs below.")
        st.rerun()

st.markdown("---")

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_queue, tab_status, tab_log = st.tabs(
    [f"✅ Approval Queue ({pending_n})", "📊 EAP Status", "📜 Agent Log"]
)

# ── TAB 1: Approval Queue ─────────────────────────────────────────────────────

with tab_queue:
    pending = state.get_pending_actions()

    if not pending:
        st.info("No pending actions. Run the agent or paste a message above to get started.")
    else:
        # Summary metrics
        counts = state.count_pending()
        m1, m2, m3 = st.columns(3)
        for col, pri, color in [
            (m1, "High",   "#b71c1c"),
            (m2, "Medium", "#e65100"),
            (m3, "Low",    "#1b5e20"),
        ]:
            with col:
                st.markdown(
                    f'<div class="metric-box">'
                    f'<div class="num" style="color:{color}">{counts.get(pri,0)}</div>'
                    f'<div class="lbl">{pri}</div></div>',
                    unsafe_allow_html=True,
                )

        st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

        # Action cards
        for action in pending:
            pri         = action["priority"]
            cust        = action["customer_name"] or "—"
            eap         = action["eap_name"] or "—"
            desc        = action["description"] or ""
            draft       = action["draft_content"] or ""
            to_addr     = action["to_address"] or ""
            evidence    = action["evidence"] or ""
            atype       = action["action_type"]
            action_id   = action["id"]
            created     = action["created_at"][:16].replace("T"," ")
            is_email    = atype == "send_email" and draft.strip()

            label = f"{pri.upper()}  |  {cust}  ({eap})  —  {desc[:80]}"

            with st.expander(label, expanded=(pri == "High")):
                st.markdown(
                    f'{badge(pri)} <strong>{cust}</strong> '
                    f'<span style="color:#90a4ae"> · {eap} · {created}</span>',
                    unsafe_allow_html=True,
                )

                if evidence:
                    st.markdown(f"**Evidence from communication:**")
                    st.info(f'"{evidence}"')

                if draft.strip():
                    st.markdown("**Draft communication — review before approving:**")
                    edited = st.text_area(
                        "edit_draft",
                        value=draft,
                        height=320,
                        key=f"draft_{action_id}",
                        label_visibility="collapsed",
                    )
                else:
                    st.markdown(f"**Action needed:** {desc}")
                    edited = ""

                # ── Approve / Reject ──────────────────────────────────
                col_ap, col_rj, col_skip, _ = st.columns([2, 2, 2, 4])

                with col_ap:
                    approve_label = "📧 Approve & Send" if is_email else "✅ Mark Done"
                    if st.button(approve_label, key=f"ap_{action_id}", use_container_width=True, type="primary"):
                        if is_email and to_addr:
                            # Try Graph API first, fall back to SMTP
                            sent = False
                            if gc_ok:
                                try:
                                    lines = (edited or draft).splitlines()
                                    subject = next(
                                        (l[8:].strip() for l in lines if l.lower().startswith("subject:")),
                                        "EAP Update"
                                    )
                                    body = "\n".join(l for l in lines if not l.lower().startswith("subject:")).strip()
                                    sent = st.session_state["graph"].send_email(to_addr, subject, body)
                                except Exception as exc:
                                    st.error(f"Graph send failed: {exc}")

                            if not sent and st.session_state.get("outlook_email"):
                                try:
                                    import smtplib
                                    from email.mime.multipart import MIMEMultipart
                                    from email.mime.text import MIMEText
                                    lines = (edited or draft).splitlines()
                                    subject = next(
                                        (l[8:].strip() for l in lines if l.lower().startswith("subject:")),
                                        "EAP Update"
                                    )
                                    body = "\n".join(l for l in lines if not l.lower().startswith("subject:")).strip()
                                    msg = MIMEMultipart()
                                    msg["From"]    = st.session_state["outlook_email"]
                                    msg["To"]      = to_addr
                                    msg["Subject"] = subject
                                    msg.attach(MIMEText(body, "plain"))
                                    with smtplib.SMTP("smtp.office365.com", 587) as srv:
                                        srv.ehlo(); srv.starttls()
                                        srv.login(st.session_state["outlook_email"],
                                                  st.session_state.get("outlook_password",""))
                                        srv.send_message(msg)
                                    sent = True
                                except Exception as exc:
                                    st.error(f"SMTP send failed: {exc}")

                            if sent:
                                st.success(f"Sent to {to_addr} ✓")
                            elif is_email:
                                st.warning("Email saved as approved (no send method configured).")

                        state.update_action_status(action_id, "approved")
                        state.log("approved", f"Approved: {desc}", cust, eap)
                        st.rerun()

                with col_rj:
                    if st.button("❌ Reject", key=f"rj_{action_id}", use_container_width=True):
                        state.update_action_status(action_id, "rejected")
                        state.log("rejected", f"Rejected: {desc}", cust, eap)
                        st.rerun()

                with col_skip:
                    if st.button("⏭ Skip", key=f"sk_{action_id}", use_container_width=True):
                        state.update_action_status(action_id, "skipped")
                        st.rerun()

# ── TAB 2: EAP Status ─────────────────────────────────────────────────────────

with tab_status:
    customers = state.get_all_customers()

    if not customers:
        st.info("No customer data yet. Load the sample data or upload a CSV in the sidebar.")
    else:
        # Build display DataFrame
        rows = []
        for c in customers:
            rows.append({
                "EAP":             c.get("EAP Name") or c.get("_eap_name",""),
                "Customer":        c.get("Customer Name") or c.get("_customer",""),
                "Stage":           c.get("Current Stage",""),
                "Interest":        c.get("Customer Interest Status",""),
                "Testing":         c.get("Product Actively Testing",""),
                "Feedback":        c.get("Feedback Received",""),
                "Jira":            c.get("Jira Ticket ID",""),
                "Last Touchpoint": c.get("Last Customer Touchpoint Date",""),
                "Updated":         (c.get("_updated_at","") or "")[:16].replace("T"," "),
            })

        df = pd.DataFrame(rows)

        # Filter
        eap_filter = st.multiselect("Filter by EAP", sorted(df["EAP"].unique()), default=sorted(df["EAP"].unique()))
        df = df[df["EAP"].isin(eap_filter)]

        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{len(df)} customers · Last updated by agent shown in 'Updated' column")

# ── TAB 3: Agent Log ──────────────────────────────────────────────────────────

with tab_log:
    logs = state.get_log(limit=150)

    if not logs:
        st.info("No agent activity yet.")
    else:
        search = st.text_input("Filter log", placeholder="customer name, event type…")
        for entry in logs:
            ts    = entry["timestamp"][:16].replace("T"," ")
            etype = entry["event_type"]
            cust  = entry["customer_name"] or ""
            desc  = entry["description"] or ""
            evid  = entry["evidence"] or ""

            if search and search.lower() not in (cust + desc + etype).lower():
                continue

            st.markdown(
                f'<div class="log-row">'
                f'<span class="log-ts">{ts}</span> &nbsp; '
                f'{icon(etype)} <span class="log-type">{etype}</span>'
                f'{f" · {cust}" if cust else ""} — {desc}'
                f'{f"<br><small style=color:#90a4ae>Evidence: {evid[:120]}</small>" if evid else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )
