"""
Meeting Notes App (Streamlit)
------------------------------
Enter the path to a folder of meeting transcripts and click Run.
Claude processes every .txt file, saves a .md file next to each one,
and shows the results here.

Run:
    streamlit run meeting_notes_app.py
"""

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from meeting_notes_agent import extract_meeting_notes, format_markdown

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Meeting Notes Agent",
    page_icon="📝",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        color: white;
        margin-bottom: 1.5rem;
    }
    .main-header h1 { margin: 0; font-size: 1.8rem; }
    .main-header p  { margin: 0.3rem 0 0; opacity: 0.85; font-size: 0.95rem; }

    .metric-card {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem 1.2rem;
        text-align: center;
    }
    .metric-card .value { font-size: 2rem; font-weight: 700; color: #4c1d95; }
    .metric-card .label { font-size: 0.85rem; color: #64748b; margin-top: 0.2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown(
    """
    <div class="main-header">
        <h1>📝 Meeting Notes Agent</h1>
        <p>Point to a folder of .txt transcripts — Claude extracts notes, decisions,
           and action items and saves a .md file next to each transcript.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Configuration")

    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        value=os.environ.get("ANTHROPIC_API_KEY", ""),
        help="Your sk-ant-… key from console.anthropic.com",
    )
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key

    st.divider()
    st.markdown(
        """
### How it works
1. Enter the **folder path** that contains your `.txt` transcript files.
2. Click **Run Agent**.
3. Claude processes each transcript and extracts:
   - Meeting title & date/time
   - Attendees
   - Discussion notes by topic
   - Decisions made
   - Action items (owner · due date · priority)
   - Next steps
4. A **Markdown file** is saved next to each transcript with the same name.
        """
    )
    st.divider()
    st.caption("Powered by Claude claude-sonnet-4-6 · Anthropic")

# ---------------------------------------------------------------------------
# Main input
# ---------------------------------------------------------------------------

if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
    st.warning("Enter your Anthropic API key in the sidebar to continue.")

folder_input = st.text_input(
    "Transcripts folder path",
    placeholder="/path/to/transcripts",
    help="Folder containing .txt meeting transcript files.",
)

run_btn = st.button(
    "Run Agent",
    type="primary",
    disabled=not (
        folder_input.strip() and os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ),
)

# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

if run_btn and folder_input.strip():
    folder = Path(folder_input.strip())

    if not folder.is_dir():
        st.error(f"Directory not found: `{folder}`")
        st.stop()

    files = sorted(folder.glob("*.txt"))
    if not files:
        st.warning(f"No .txt files found in `{folder}`")
        st.stop()

    st.info(f"Found **{len(files)}** transcript(s) in `{folder.resolve()}`")

    progress = st.progress(0, text="Starting…")
    all_results = []   # list of (filename, data_dict | None, error | None)

    for idx, src in enumerate(files):
        progress.progress(
            int(idx / len(files) * 100),
            text=f"Processing {src.name} ({idx + 1}/{len(files)})…",
        )

        try:
            text = src.read_text(encoding="utf-8").strip()
            if not text:
                all_results.append((src, None, "File is empty."))
                continue

            data = extract_meeting_notes(text)
            if not data:
                all_results.append((src, None, "Claude returned no structured data."))
                continue

            md = format_markdown(data)
            out = src.parent / f"{src.stem}.md"
            out.write_text(md, encoding="utf-8")
            all_results.append((src, data, None))

        except Exception as exc:
            all_results.append((src, None, str(exc)))

    progress.progress(100, text="Done!")

    # -----------------------------------------------------------------------
    # Summary metrics
    # -----------------------------------------------------------------------

    ok_results = [(src, d) for src, d, err in all_results if d]
    fail_results = [(src, err) for src, d, err in all_results if err]

    total_actions = sum(
        len(d.get("action_items") or []) for _, d in ok_results
    )
    total_high = sum(
        sum(1 for a in (d.get("action_items") or []) if a.get("priority") == "High")
        for _, d in ok_results
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f'<div class="metric-card"><div class="value">{len(files)}</div>'
            f'<div class="label">Transcripts</div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="metric-card"><div class="value" style="color:#059669">'
            f'{len(ok_results)}</div>'
            f'<div class="label">Processed</div></div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div class="metric-card"><div class="value">{total_actions}</div>'
            f'<div class="label">Action Items</div></div>',
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f'<div class="metric-card"><div class="value" style="color:#dc2626">'
            f'{total_high}</div>'
            f'<div class="label">High Priority</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("")

    # -----------------------------------------------------------------------
    # Per-file results
    # -----------------------------------------------------------------------

    for src, data, err in all_results:
        if err:
            st.error(f"**{src.name}** — {err}")
            continue

        out_name = f"{src.stem}.md"
        with st.expander(f"**{src.name}** → `{out_name}`", expanded=False):
            action_items = data.get("action_items") or []

            st.markdown(
                f"**{data.get('meeting_title', 'Meeting')}**  \n"
                f"Date/Time: {data.get('meeting_datetime', 'Not specified')}"
            )

            attendees = data.get("attendees") or []
            if attendees:
                st.markdown(f"Attendees: {', '.join(attendees)}")

            # Notes
            notes = data.get("meeting_notes") or []
            if notes:
                st.markdown("**Discussion Notes**")
                for note in notes:
                    st.markdown(f"- **{note.get('topic')}:** {note.get('summary')}")

            # Decisions
            decisions = data.get("decisions_made") or []
            if decisions:
                st.markdown("**Decisions Made**")
                for d in decisions:
                    st.markdown(f"- {d}")

            # Action items table
            if action_items:
                st.markdown("**Action Items**")
                rows = [
                    {
                        "#": i + 1,
                        "Task": a.get("task", ""),
                        "Owner": a.get("owner", "TBD"),
                        "Due Date": a.get("due_date") or "TBD",
                        "Priority": a.get("priority", "Medium"),
                    }
                    for i, a in enumerate(action_items)
                ]
                df = pd.DataFrame(rows).set_index("#")

                def _colour(val: str) -> str:
                    return {
                        "High": "background-color:#fee2e2",
                        "Medium": "background-color:#fef3c7",
                        "Low": "background-color:#d1fae5",
                    }.get(val, "")

                st.dataframe(
                    df.style.applymap(_colour, subset=["Priority"]),
                    use_container_width=True,
                )

            # Next steps
            next_steps = data.get("next_steps", "")
            if next_steps:
                st.info(f"**Next Steps:** {next_steps}")

            # Download button for this file's markdown
            md_content = format_markdown(data)
            st.download_button(
                label=f"Download {out_name}",
                data=md_content.encode("utf-8"),
                file_name=out_name,
                mime="text/markdown",
                key=f"dl_{src.stem}",
            )

    # -----------------------------------------------------------------------
    # Failures summary
    # -----------------------------------------------------------------------

    if fail_results:
        st.divider()
        st.markdown("**Failed files:**")
        for src, err in fail_results:
            st.error(f"{src.name}: {err}")
