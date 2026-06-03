"""
Meeting Notes Agent
-------------------
Give it a folder that contains meeting transcripts (.txt files) and it will:
  1. Read every transcript in the folder.
  2. Use Claude to extract meeting notes, decisions, and action items.
  3. Save a Markdown file (.md) next to each transcript, using the same name.

CLI usage:
    python meeting_notes_agent.py /path/to/transcripts/
"""

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
# Core extraction
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

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        else:
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

    notes = data.get("meeting_notes") or []
    if notes:
        lines.append("## Meeting Notes")
        lines.append("")
        for note in notes:
            lines.append(f"### {note.get('topic', 'Topic')}")
            lines.append(note.get("summary", ""))
            lines.append("")

    decisions = data.get("decisions_made") or []
    if decisions:
        lines.append("## Decisions Made")
        for d in decisions:
            lines.append(f"- {d}")
        lines.append("")

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
# Folder processing
# ---------------------------------------------------------------------------


def process_folder(folder_path: str) -> list[tuple[str, str | None, str | None]]:
    """
    Process every .txt transcript in *folder_path*.

    For each file, saves a .md file in the same folder with the same base name.
    Returns a list of (source_filename, output_filename, error_message) tuples.
    """
    folder = Path(folder_path).resolve()

    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    files = sorted(folder.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"No .txt transcript files found in: {folder}")

    results = []
    for src in files:
        print(f"  Processing: {src.name}")
        try:
            text = src.read_text(encoding="utf-8").strip()
            if not text:
                raise ValueError("File is empty.")

            data = extract_meeting_notes(text)
            if not data:
                raise RuntimeError("Claude returned no structured data.")

            md = format_markdown(data)
            out = src.parent / f"{src.stem}.md"
            out.write_text(md, encoding="utf-8")

            print(f"  Saved:      {out.name}")
            results.append((src.name, out.name, None))

        except Exception as exc:
            print(f"  FAILED:     {src.name} — {exc}")
            results.append((src.name, None, str(exc)))

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage:  python meeting_notes_agent.py /path/to/transcripts/")
        sys.exit(1)

    folder = Path(sys.argv[1])
    print(f"\nMeeting Notes Agent — scanning: {folder.resolve()}\n")

    try:
        results = process_folder(str(folder))
    except (NotADirectoryError, FileNotFoundError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    ok = sum(1 for _, out, _ in results if out)
    fail = len(results) - ok

    print(f"\nDone — {ok} succeeded, {fail} failed.")


if __name__ == "__main__":
    main()
