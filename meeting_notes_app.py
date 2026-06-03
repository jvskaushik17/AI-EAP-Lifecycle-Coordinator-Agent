"""
Meeting Notes App (Streamlit)
------------------------------
Drag-and-drop or paste a meeting transcript to extract structured notes and
action items with Claude.  The resulting Markdown file is saved to the same
folder as the uploaded transcript (when a path is provided) and can also be
downloaded directly from the browser.

Run:
    streamlit run meeting_notes_app.py
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path

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

# ---------------------------------------------------------------------------
# Custom CSS — match the existing app's card / badge style
# ---------------------------------------------------------------------------

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

    .badge-high   { background:#fee2e2; color:#dc2626; padding:2px 8px;
                    border-radius:12px; font-size:0.78rem; font-weight:600; }
    .badge-medium { background:#fef3c7; color:#d97706; padding:2px 8px;
                    border-radius:12px; font-size:0.78rem; font-weight:600; }
    .badge-low    { background:#d1fae5; color:#059669; padding:2px 8px;
                    border-radius:12px; font-size:0.78rem; font-weight:600; }

    .action-table th { background:#f1f5f9; }
    .stTextArea textarea { font-family: monospace; font-size: 0.85rem; }
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
        <p>Upload a transcript or paste text — Claude extracts notes, decisions,
           and action items and saves a Markdown file to the same folder.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar — configuration
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
    st.markdown("### How it works")
    st.markdown(
        """
1. **Upload** a `.txt` transcript or paste text below.
2. Click **Extract Meeting Notes**.
3. Claude identifies:
   - Meeting title & date/time
   - Attendees
   - Discussion notes by topic
   - Decisions made
   - Action items (owner, due date, priority)
   - Next steps
4. The Markdown file is **saved next to the original** and is also available to **download** here.
        """
    )

    st.divider()
    st.caption("Powered by Claude claude-sonnet-4-6 · anthropic")

# ---------------------------------------------------------------------------
# Input section
# ---------------------------------------------------------------------------

col_left, col_right = st.columns([1, 1], gap="large")

with col_left:
    st.subheader("📂 Input Transcript")

    input_mode = st.radio(
        "Input method",
        ["Upload file", "Paste text"],
        horizontal=True,
        label_visibility="collapsed",
    )

    transcript_text = ""
    source_dir: Path | None = None
    source_stem: str = "meeting_notes"

    if input_mode == "Upload file":
        uploaded = st.file_uploader(
            "Drop your transcript (.txt)",
            type=["txt"],
            help="Plain-text meeting transcript",
        )
        if uploaded:
            transcript_text = uploaded.read().decode("utf-8")
            source_stem = Path(uploaded.name).stem
            st.success(f"Loaded **{uploaded.name}** ({len(transcript_text):,} chars)")

            # Optional: let user specify where the file lives so we can save it there
            save_dir_input = st.text_input(
                "📁 Original file directory (optional)",
                placeholder="/path/to/transcripts",
                help=(
                    "If you want the .md file saved next to the original transcript, "
                    "enter the folder path here. Leave blank to download only."
                ),
            )
            if save_dir_input.strip():
                source_dir = Path(save_dir_input.strip())

    else:
        transcript_text = st.text_area(
            "Paste transcript",
            height=300,
            placeholder="Paste your meeting transcript here…",
        )
        source_stem = st.text_input(
            "Output file name (without extension)",
            value=f"meeting_{datetime.now().strftime('%Y-%m-%d')}",
        )
        save_dir_input = st.text_input(
            "📁 Save directory (optional)",
            placeholder="/path/to/transcripts",
            help="Folder where the .md file will be saved. Leave blank to download only.",
        )
        if save_dir_input.strip():
            source_dir = Path(save_dir_input.strip())

    # Character preview
    if transcript_text:
        with st.expander("Preview transcript", expanded=False):
            st.text(transcript_text[:2000] + ("…" if len(transcript_text) > 2000 else ""))

# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

with col_right:
    st.subheader("📋 Extracted Notes")

    ready = bool(transcript_text.strip()) and bool(
        os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        st.warning("Enter your Anthropic API key in the sidebar to continue.")

    extract_btn = st.button(
        "⚡ Extract Meeting Notes",
        disabled=not ready,
        use_container_width=True,
        type="primary",
    )

    if extract_btn and ready:
        with st.spinner("Claude is analysing the transcript…"):
            try:
                data = extract_meeting_notes(transcript_text)
            except Exception as exc:
                st.error(f"Extraction failed: {exc}")
                st.stop()

        if not data:
            st.error("Claude returned no structured data. Please check the transcript.")
            st.stop()

        md_content = format_markdown(data)

        # ---- Metrics row ----
        action_items = data.get("action_items") or []
        high   = sum(1 for a in action_items if a.get("priority") == "High")
        medium = sum(1 for a in action_items if a.get("priority") == "Medium")
        low    = sum(1 for a in action_items if a.get("priority") == "Low")

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.markdown(
                f'<div class="metric-card"><div class="value">{len(action_items)}</div>'
                f'<div class="label">Action Items</div></div>',
                unsafe_allow_html=True,
            )
        with m2:
            st.markdown(
                f'<div class="metric-card"><div class="value" style="color:#dc2626">{high}</div>'
                f'<div class="label">High Priority</div></div>',
                unsafe_allow_html=True,
            )
        with m3:
            st.markdown(
                f'<div class="metric-card"><div class="value" style="color:#d97706">{medium}</div>'
                f'<div class="label">Medium Priority</div></div>',
                unsafe_allow_html=True,
            )
        with m4:
            st.markdown(
                f'<div class="metric-card"><div class="value" style="color:#059669">{low}</div>'
                f'<div class="label">Low Priority</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown("")

        # ---- Meeting details ----
        st.markdown(
            f"**{data.get('meeting_title', 'Meeting')}**  \n"
            f"🕐 {data.get('meeting_datetime', 'Date not specified')}"
        )
        attendees = data.get("attendees") or []
        if attendees:
            st.markdown(f"👥 {', '.join(attendees)}")

        st.divider()

        # ---- Notes by topic ----
        notes = data.get("meeting_notes") or []
        if notes:
            st.markdown("### Discussion Notes")
            for note in notes:
                with st.expander(note.get("topic", "Topic"), expanded=True):
                    st.markdown(note.get("summary", ""))

        # ---- Decisions ----
        decisions = data.get("decisions_made") or []
        if decisions:
            st.markdown("### Decisions Made")
            for d in decisions:
                st.markdown(f"- {d}")

        # ---- Action items table ----
        if action_items:
            st.markdown("### Action Items")
            import pandas as pd

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

            def colour_priority(val: str) -> str:
                colours = {"High": "background-color:#fee2e2",
                           "Medium": "background-color:#fef3c7",
                           "Low": "background-color:#d1fae5"}
                return colours.get(val, "")

            st.dataframe(
                df.style.applymap(colour_priority, subset=["Priority"]),
                use_container_width=True,
            )

        # ---- Next steps ----
        next_steps = data.get("next_steps", "")
        if next_steps:
            st.markdown("### Next Steps")
            st.info(next_steps)

        st.divider()

        # ---- Save & download ----
        output_filename = f"{source_stem}.md"

        if source_dir:
            try:
                source_dir.mkdir(parents=True, exist_ok=True)
                out_path = source_dir / output_filename
                out_path.write_text(md_content, encoding="utf-8")
                st.success(f"Saved to **{out_path}**")
            except Exception as exc:
                st.error(f"Could not save file: {exc}")

        st.download_button(
            label=f"⬇️ Download {output_filename}",
            data=md_content.encode("utf-8"),
            file_name=output_filename,
            mime="text/markdown",
            use_container_width=True,
        )

        with st.expander("View raw Markdown", expanded=False):
            st.code(md_content, language="markdown")

# ---------------------------------------------------------------------------
# Batch processing section
# ---------------------------------------------------------------------------

st.divider()
st.subheader("🗂️ Batch Process a Folder")

with st.expander("Process all .txt transcripts in a directory", expanded=False):
    batch_dir = st.text_input(
        "Folder path",
        placeholder="/path/to/transcripts",
        key="batch_dir",
    )
    batch_btn = st.button(
        "▶ Process All",
        disabled=not (batch_dir.strip() and os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        key="batch_btn",
    )

    if batch_btn:
        folder = Path(batch_dir.strip())
        if not folder.is_dir():
            st.error(f"Directory not found: {folder}")
        else:
            files = sorted(folder.glob("*.txt"))
            if not files:
                st.warning("No .txt files found in that directory.")
            else:
                progress = st.progress(0, text="Starting…")
                results = []
                for idx, f in enumerate(files):
                    progress.progress(
                        int((idx / len(files)) * 100),
                        text=f"Processing {f.name}…",
                    )
                    try:
                        text = f.read_text(encoding="utf-8").strip()
                        if not text:
                            results.append((f.name, None, "Empty file"))
                            continue
                        extracted = extract_meeting_notes(text)
                        if not extracted:
                            results.append((f.name, None, "No data returned"))
                            continue
                        md = format_markdown(extracted)
                        out = f.parent / f"{f.stem}.md"
                        out.write_text(md, encoding="utf-8")
                        results.append((f.name, out.name, None))
                    except Exception as exc:
                        results.append((f.name, None, str(exc)))

                progress.progress(100, text="Done!")

                st.markdown("**Results:**")
                for src, out, err in results:
                    if err:
                        st.error(f"✗ {src}: {err}")
                    else:
                        st.success(f"✓ {src} → {out}")
