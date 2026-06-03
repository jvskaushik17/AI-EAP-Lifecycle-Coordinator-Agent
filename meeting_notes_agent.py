"""
Meeting Notes Agent
-------------------
Extracts meeting notes, action items, decisions, and attendee information
from a meeting transcript using Claude tool-use and saves the result as a
Markdown file in the same directory as the source transcript.

CLI usage:
    python meeting_notes_agent.py <transcript_file>
    python meeting_notes_agent.py transcripts/   # batch-process all .txt files

The output file will have the same base name as the transcript with a .md
extension, saved in the same folder.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Claude tool definition
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "extract_meeting_data",
        "description": (
            "Extract structured meeting information from a transcript: "
            "title, date/time, attendees, topical notes, action items, "
            "decisions made, and next steps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_title": {
                    "type": "string",
                    "description": "Topic or title of the meeting.",
                },
                "meeting_datetime": {
                    "type": "string",
                    "description": (
                        "Date and time of the meeting. Use ISO 8601 when available "
                        "(e.g. 2024-01-15T14:00:00); otherwise a human-readable form "
                        "or 'Date not specified'."
                    ),
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of meeting participants.",
                },
                "meeting_notes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                        "required": ["topic", "summary"],
                    },
                    "description": "Key discussion points organised by topic.",
                },
                "action_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "What needs to be done.",
                            },
                            "owner": {
                                "type": "string",
                                "description": "Who is responsible.",
                            },
                            "due_date": {
                                "type": "string",
                                "description": "Deadline, if mentioned.",
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["High", "Medium", "Low"],
                                "description": "Priority inferred from context.",
                            },
                        },
                        "required": ["task", "owner", "priority"],
                    },
                    "description": "Action items with owners, due dates, and priorities.",
                },
                "decisions_made": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Concrete decisions reached during the meeting.",
                },
                "next_steps": {
                    "type": "string",
                    "description": "Overall follow-up plan or next meeting details.",
                },
            },
            "required": [
                "meeting_title",
                "meeting_datetime",
                "meeting_notes",
                "action_items",
            ],
        },
    }
]

SYSTEM_PROMPT = """You are a professional meeting-notes assistant.

Analyse the transcript carefully and use the extract_meeting_data tool to return:
• A clear meeting title and the exact date/time (or your best inference).
• All attendees you can identify.
• Concise, topic-organised meeting notes that capture every key discussion point.
• Every action item with: the task description, the owner (person responsible),
  the due date (if mentioned), and a priority (High = urgent/critical,
  Medium = standard commitment, Low = nice-to-have).
• Concrete decisions reached.
• Next steps or follow-up meeting details.

Be exhaustive — do not drop action items or discussion points."""


# ---------------------------------------------------------------------------
# Core extraction logic
# ---------------------------------------------------------------------------


def extract_meeting_notes(transcript_text: str) -> dict:
    """Call Claude with tool-use to extract structured meeting data."""
    client = anthropic.Anthropic()

    messages = [
        {
            "role": "user",
            "content": (
                "Please analyse the following meeting transcript and extract all "
                "information using the extract_meeting_data tool.\n\n"
                "TRANSCRIPT:\n---\n"
                + transcript_text
                + "\n---\n\nBe thorough and use the tool to return structured data."
            ),
        }
    ]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            tool_results = []
            extracted_data = None

            for tu in tool_uses:
                if tu.name == "extract_meeting_data":
                    extracted_data = tu.input
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": "Extraction recorded.",
                    }
                )

            if extracted_data:
                return extracted_data

            # Feed results back and continue
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        else:
            # end_turn or other stop — no tool data available
            return {}


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------


def format_markdown(data: dict) -> str:
    """Render extracted meeting data as a clean Markdown document."""
    lines: list[str] = []

    title = data.get("meeting_title", "Meeting")
    meeting_dt = data.get("meeting_datetime", "Date not specified")

    lines += [
        f"# Meeting Notes: {title}",
        "",
        "## Meeting Details",
        f"- **Date/Time:** {meeting_dt}",
    ]

    attendees = data.get("attendees") or []
    if attendees:
        lines.append(f"- **Attendees:** {', '.join(attendees)}")

    lines.append("")

    # Discussion notes
    notes = data.get("meeting_notes") or []
    if notes:
        lines.append("## Meeting Notes")
        lines.append("")
        for note in notes:
            lines.append(f"### {note.get('topic', 'Topic')}")
            lines.append(note.get("summary", ""))
            lines.append("")

    # Decisions
    decisions = data.get("decisions_made") or []
    if decisions:
        lines.append("## Decisions Made")
        for d in decisions:
            lines.append(f"- {d}")
        lines.append("")

    # Action items table
    action_items = data.get("action_items") or []
    if action_items:
        lines += [
            "## Action Items",
            "",
            "| # | Task | Owner | Due Date | Priority |",
            "|---|------|-------|----------|----------|",
        ]
        for i, item in enumerate(action_items, 1):
            task = item.get("task", "TBD")
            owner = item.get("owner", "TBD")
            due = item.get("due_date") or "TBD"
            priority = item.get("priority", "Medium")
            lines.append(f"| {i} | {task} | {owner} | {due} | {priority} |")
        lines.append("")

    # Next steps
    next_steps = data.get("next_steps", "")
    if next_steps:
        lines += ["## Next Steps", next_steps, ""]

    lines += [
        "---",
        f"*Generated by Meeting Notes Agent on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File-level entry point
# ---------------------------------------------------------------------------


def process_transcript_file(transcript_path: str) -> str:
    """
    Read *transcript_path*, extract notes with Claude, and write a Markdown
    file to the same directory with the same base name (.md extension).

    Returns the absolute path of the saved notes file.
    """
    src = Path(transcript_path).resolve()

    if not src.exists():
        raise FileNotFoundError(f"Transcript not found: {src}")

    text = src.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Transcript file is empty: {src}")

    print(f"  Processing: {src.name}")
    print("  Calling Claude to extract meeting notes and action items…")

    data = extract_meeting_notes(text)
    if not data:
        raise RuntimeError("Claude did not return structured data for this transcript.")

    md = format_markdown(data)

    out_path = src.parent / f"{src.stem}.md"
    out_path.write_text(md, encoding="utf-8")

    print(f"  Saved:      {out_path}")
    return str(out_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python meeting_notes_agent.py <transcript_file>")
        print("  python meeting_notes_agent.py <folder/>   # batch all .txt files")
        sys.exit(1)

    target = Path(sys.argv[1])

    if target.is_dir():
        files = sorted(target.glob("*.txt"))
        if not files:
            print(f"No .txt files found in {target}")
            sys.exit(1)
        results = []
        for f in files:
            try:
                out = process_transcript_file(str(f))
                results.append((f.name, Path(out).name, None))
            except Exception as exc:
                results.append((f.name, None, str(exc)))

        print("\nBatch summary:")
        for src_name, out_name, err in results:
            if err:
                print(f"  FAILED  {src_name}: {err}")
            else:
                print(f"  OK      {src_name} -> {out_name}")
    else:
        out = process_transcript_file(str(target))
        print(f"\nDone! Notes file: {out}")


if __name__ == "__main__":
    main()
