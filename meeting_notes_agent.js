#!/usr/bin/env node
/**
 * Meeting Notes Agent — powered by Ollama (local LLM, zero tokens)
 * -----------------------------------------------------------------
 * No API keys. No cloud auth. Runs entirely on your machine.
 *
 * Folder layout (pass the root "Meeting notes" folder):
 *
 *   Meeting notes/
 *     Meeting transcript/    ← agent reads transcripts from here
 *       standup_2026-06-03.txt
 *       product_review.vtt
 *     Meeting summary/       ← agent writes Word docs here (auto-created)
 *       standup_2026-06-03.docx
 *       product_review.docx
 *
 * Prerequisites (one-time):
 *   1. Install Ollama         →  https://ollama.ai
 *   2. ollama pull llama3.2
 *   3. npm install            →  installs docx + mammoth
 *
 * Usage:
 *   node meeting_notes_agent.js "/path/to/Meeting notes"
 *   node meeting_notes_agent.js "/path/to/Meeting notes" --watch
 *
 * Your OneDrive path (macOS):
 *   ~/Library/CloudStorage/OneDrive-Infoblox/Documents/Meeting notes
 *
 * Optional env vars (no tokens needed):
 *   OLLAMA_HOST    Ollama URL          (default: http://localhost:11434)
 *   OLLAMA_MODEL   Model name          (default: llama3.2)
 *   SKIP_EXISTING  Skip up-to-date docs (default: true)
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ─── Config ───────────────────────────────────────────────────────────────────

const OLLAMA_BASE     = (process.env.OLLAMA_HOST  || 'http://localhost:11434').replace(/\/$/, '');
const MODEL           = process.env.OLLAMA_MODEL  || 'llama3.2';
const SKIP_EXISTING   = process.env.SKIP_EXISTING !== 'false';
const TRANSCRIPT_EXTS = new Set(['.txt', '.vtt', '.docx']);

const TOOL_CAPABLE_MODELS = [
  'llama3', 'llama3.1', 'llama3.2', 'llama3.3',
  'qwen2.5', 'qwen2', 'mistral-nemo', 'firefunction', 'command-r',
];

const INPUT_SUBFOLDER  = 'Meeting transcript';
const OUTPUT_SUBFOLDER = 'Meeting summary';

// ─── Ollama API ───────────────────────────────────────────────────────────────

async function ollamaChat(messages, tools = []) {
  const body = {
    model:  MODEL,
    messages,
    stream: false,
    ...(tools.length ? { tools } : {}),
  };
  const res = await fetch(`${OLLAMA_BASE}/v1/chat/completions`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`Ollama ${res.status}: ${txt.slice(0, 200)}`);
  }
  return res.json();
}

async function checkOllama() {
  try {
    const res = await fetch(`${OLLAMA_BASE}/api/tags`, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) return null;
    const data = await res.json();
    return (data.models || []).map(m => m.name);
  } catch {
    return null;
  }
}

function modelSupportsTools(name) {
  const lower = name.toLowerCase();
  return TOOL_CAPABLE_MODELS.some(k => lower.includes(k));
}

// ─── Tool definition ──────────────────────────────────────────────────────────

const TOOLS = [
  {
    type: 'function',
    function: {
      name:        'save_meeting_notes',
      description: 'Save structured meeting notes extracted from the transcript.',
      parameters: {
        type: 'object',
        properties: {
          meeting_title:    { type: 'string', description: 'Topic or title of the meeting.' },
          meeting_datetime: { type: 'string', description: 'Date and time (ISO 8601 or natural language), or "Date not specified".' },
          attendees:        { type: 'array', items: { type: 'string' }, description: 'All identifiable participants.' },
          meeting_notes: {
            type: 'array',
            items: {
              type: 'object',
              properties: { topic: { type: 'string' }, summary: { type: 'string' } },
              required: ['topic', 'summary'],
            },
            description: 'Key discussion points, one object per topic.',
          },
          action_items: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                task:     { type: 'string' },
                owner:    { type: 'string' },
                due_date: { type: 'string' },
                priority: { type: 'string', enum: ['High', 'Medium', 'Low'] },
              },
              required: ['task', 'owner', 'priority'],
            },
            description: 'Every action item with owner, due date, and priority.',
          },
          decisions_made: { type: 'array', items: { type: 'string' }, description: 'Concrete decisions reached.' },
          next_steps:     { type: 'string', description: 'Follow-up plan or next-meeting details.' },
        },
        required: ['meeting_title', 'meeting_datetime', 'meeting_notes', 'action_items'],
      },
    },
  },
];

const SYSTEM_TOOL = `You are a precise meeting-notes assistant.
Analyse the transcript and call save_meeting_notes with:
• meeting_title and meeting_datetime (exact or inferred; "Date not specified" if truly unknown)
• attendees — everyone identifiable
• meeting_notes — every discussion topic with a concise summary (be thorough)
• action_items — EVERY item: task, owner, due_date (if mentioned), priority
  (High=urgent/critical, Medium=standard commitment, Low=nice-to-have)
• decisions_made — concrete decisions reached
• next_steps — follow-up plan or next-meeting details
Always call the function. Never respond with plain text only.`;

const SYSTEM_JSON = `You are a precise meeting-notes assistant.
Analyse the transcript and return ONLY valid JSON (no prose, no markdown fences) matching:
{
  "meeting_title": "...",
  "meeting_datetime": "...",
  "attendees": ["..."],
  "meeting_notes": [{"topic":"...","summary":"..."}],
  "action_items": [{"task":"...","owner":"...","due_date":"...","priority":"High|Medium|Low"}],
  "decisions_made": ["..."],
  "next_steps": "..."
}
Be exhaustive — do not miss any action items.`;

// ─── Extraction agent loop ────────────────────────────────────────────────────

async function extractWithTools(transcript) {
  let messages = [
    { role: 'system', content: SYSTEM_TOOL },
    { role: 'user',   content: `Analyse this transcript and call save_meeting_notes.\n\nTRANSCRIPT:\n---\n${transcript}\n---` },
  ];

  for (let attempt = 1; attempt <= 3; attempt++) {
    const resp   = await ollamaChat(messages, TOOLS);
    const msg    = resp.choices?.[0]?.message;

    if (msg?.tool_calls?.length) {
      for (const tc of msg.tool_calls) {
        if (tc.function?.name === 'save_meeting_notes') {
          try {
            return typeof tc.function.arguments === 'string'
              ? JSON.parse(tc.function.arguments)
              : tc.function.arguments;
          } catch {}
        }
      }
    }

    const text = msg?.content || '';
    const m    = text.match(/\{[\s\S]*\}/);
    if (m) {
      try {
        const p = JSON.parse(m[0]);
        if (p.meeting_title && Array.isArray(p.action_items)) return p;
      } catch {}
    }

    if (attempt < 3) {
      messages = [
        ...messages,
        { role: 'assistant', content: text },
        { role: 'user', content: 'Please call the save_meeting_notes function with the extracted data. Do not return plain text.' },
      ];
    }
  }
  return null;
}

async function extractWithJson(transcript) {
  const messages = [
    { role: 'system', content: SYSTEM_JSON },
    { role: 'user',   content: `TRANSCRIPT:\n---\n${transcript}\n---\nReturn JSON only.` },
  ];

  for (let attempt = 1; attempt <= 3; attempt++) {
    const text    = (await ollamaChat(messages)).choices?.[0]?.message?.content || '';
    const cleaned = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
    const m       = cleaned.match(/\{[\s\S]*\}/);
    if (m) {
      try {
        const p = JSON.parse(m[0]);
        if (p.meeting_title && Array.isArray(p.action_items)) return p;
      } catch {}
    }
  }
  return null;
}

async function extractMeetingData(transcript) {
  if (modelSupportsTools(MODEL)) {
    const result = await extractWithTools(transcript);
    if (result) return result;
  }
  return extractWithJson(transcript);
}

// ─── Read transcript text ─────────────────────────────────────────────────────

function parseVtt(text) {
  return text
    .split('\n')
    .filter(l => {
      const t = l.trim();
      return t && !t.startsWith('WEBVTT') && !/^\d{2}:\d{2}/.test(t)
          && !/^NOTE/.test(t) && !/^\d+$/.test(t);
    })
    .join('\n');
}

async function readTranscript(filePath) {
  const buf = fs.readFileSync(filePath);
  const ext = path.extname(filePath).toLowerCase();
  if (ext === '.vtt') return parseVtt(buf.toString('utf8'));
  if (ext === '.docx') {
    try {
      const mammoth = require('mammoth');
      return (await mammoth.extractRawText({ buffer: buf })).value;
    } catch {
      process.stdout.write('\n    [hint] npm install for better .docx reading — ');
      return buf.toString('utf8');
    }
  }
  return buf.toString('utf8');
}

// ─── Word document builder ────────────────────────────────────────────────────

async function buildWordDoc(data) {
  const {
    Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
    HeadingLevel, AlignmentType, WidthType, BorderStyle, ShadingType,
  } = require('docx');

  const BRAND_BLUE  = '1F3864';
  const HEADER_FILL = 'D6E4F0';
  const HIGH_FILL   = 'FDDEDE';
  const MED_FILL    = 'FFF3CD';
  const LOW_FILL    = 'D4EDDA';

  const children = [];

  // ── Title ──
  children.push(
    new Paragraph({
      text:    `Meeting Notes: ${data.meeting_title || 'Meeting'}`,
      heading: HeadingLevel.HEADING_1,
      spacing: { after: 100 },
    }),
  );

  // ── Meeting details ──
  children.push(
    new Paragraph({
      children: [
        new TextRun({ text: 'Date/Time:  ', bold: true }),
        new TextRun(data.meeting_datetime || 'Date not specified'),
      ],
      spacing: { after: 60 },
    }),
  );

  if (data.attendees?.length) {
    children.push(
      new Paragraph({
        children: [
          new TextRun({ text: 'Attendees:  ', bold: true }),
          new TextRun(data.attendees.join(', ')),
        ],
        spacing: { after: 200 },
      }),
    );
  }

  // ── Discussion Notes ──
  if (data.meeting_notes?.length) {
    children.push(
      new Paragraph({ text: 'Discussion Notes', heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 100 } }),
    );
    for (const note of data.meeting_notes) {
      children.push(
        new Paragraph({ text: note.topic, heading: HeadingLevel.HEADING_3, spacing: { before: 120, after: 60 } }),
        new Paragraph({ text: note.summary || '', spacing: { after: 80 } }),
      );
    }
  }

  // ── Decisions Made ──
  if (data.decisions_made?.length) {
    children.push(
      new Paragraph({ text: 'Decisions Made', heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 100 } }),
    );
    for (const d of data.decisions_made) {
      children.push(new Paragraph({ text: `• ${d}`, spacing: { after: 60 } }));
    }
  }

  // ── Action Items table ──
  if (data.action_items?.length) {
    children.push(
      new Paragraph({ text: 'Action Items', heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 120 } }),
    );

    const headerCell = (text) =>
      new TableCell({
        children: [new Paragraph({ children: [new TextRun({ text, bold: true, color: '000000' })] })],
        shading:  { type: ShadingType.SOLID, color: HEADER_FILL },
        width:    { size: 20, type: WidthType.PERCENTAGE },
      });

    const priorityFill = (p) => ({ High: HIGH_FILL, Medium: MED_FILL, Low: LOW_FILL }[p] || 'FFFFFF');

    const dataCell = (text, fill = 'FFFFFF') =>
      new TableCell({
        children: [new Paragraph({ text: text || '' })],
        shading:  { type: ShadingType.SOLID, color: fill },
        width:    { size: 20, type: WidthType.PERCENTAGE },
      });

    const rows = [
      new TableRow({
        children: [
          headerCell('#'),
          headerCell('Task'),
          headerCell('Owner'),
          headerCell('Due Date'),
          headerCell('Priority'),
        ],
        tableHeader: true,
      }),
      ...data.action_items.map((a, i) => {
        const fill = priorityFill(a.priority);
        return new TableRow({
          children: [
            dataCell(String(i + 1)),
            dataCell(a.task     || ''),
            dataCell(a.owner    || 'TBD'),
            dataCell(a.due_date || 'TBD'),
            new TableCell({
              children: [new Paragraph({ children: [new TextRun({ text: a.priority || 'Medium', bold: true })] })],
              shading:  { type: ShadingType.SOLID, color: fill },
              width:    { size: 20, type: WidthType.PERCENTAGE },
            }),
          ],
        });
      }),
    ];

    children.push(
      new Table({
        rows,
        width: { size: 100, type: WidthType.PERCENTAGE },
      }),
    );
    children.push(new Paragraph({ text: '', spacing: { after: 100 } }));
  }

  // ── Next Steps ──
  if (data.next_steps) {
    children.push(
      new Paragraph({ text: 'Next Steps', heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 100 } }),
      new Paragraph({ text: data.next_steps, spacing: { after: 80 } }),
    );
  }

  // ── Footer note ──
  children.push(
    new Paragraph({
      children: [new TextRun({ text: `Generated by Meeting Notes Agent · ${new Date().toLocaleString()} · model: ${MODEL}`, italics: true, size: 18, color: '888888' })],
      spacing: { before: 400 },
    }),
  );

  const doc = new Document({
    styles: {
      default: {
        heading1: { run: { size: 32, bold: true, color: BRAND_BLUE } },
        heading2: { run: { size: 26, bold: true, color: BRAND_BLUE } },
        heading3: { run: { size: 22, bold: true, color: '333333' } },
      },
    },
    sections: [{ children }],
  });

  return Packer.toBuffer(doc);
}

// ─── File-level processing ────────────────────────────────────────────────────

function isUpToDate(srcPath, outPath) {
  if (!SKIP_EXISTING) return false;
  try { return fs.statSync(outPath).mtimeMs >= fs.statSync(srcPath).mtimeMs; }
  catch { return false; }
}

async function processFile(srcPath, summaryFolder) {
  const stem    = path.basename(srcPath, path.extname(srcPath));
  const outPath = path.join(summaryFolder, `${stem}.docx`);

  if (isUpToDate(srcPath, outPath)) return { status: 'skipped' };

  const text = await readTranscript(srcPath);
  if (!text.trim()) throw new Error('file is empty');

  const data = await extractMeetingData(text);
  if (!data)  throw new Error('could not extract structured data after retries');

  const buf = await buildWordDoc(data);
  fs.writeFileSync(outPath, buf);

  return { status: 'ok', outPath, actionCount: (data.action_items || []).length };
}

// ─── Folder helpers ───────────────────────────────────────────────────────────

function scanFolder(folderPath) {
  return fs.readdirSync(folderPath, { withFileTypes: true })
    .filter(e => e.isFile() && TRANSCRIPT_EXTS.has(path.extname(e.name).toLowerCase()))
    .map(e => path.join(folderPath, e.name))
    .sort();
}

function resolveSubfolders(rootFolder) {
  const transcriptFolder = path.join(rootFolder, INPUT_SUBFOLDER);
  const summaryFolder    = path.join(rootFolder, OUTPUT_SUBFOLDER);

  if (!fs.existsSync(transcriptFolder)) {
    console.error(`\nTranscript folder not found: ${transcriptFolder}`);
    console.error(`Please create it and drop your transcript files (.txt / .vtt / .docx) inside.\n`);
    process.exit(1);
  }

  if (!fs.existsSync(summaryFolder)) {
    fs.mkdirSync(summaryFolder, { recursive: true });
    console.log(`Created output folder: ${summaryFolder}`);
  }

  return { transcriptFolder, summaryFolder };
}

// ─── Watch mode ───────────────────────────────────────────────────────────────

function watchFolder(transcriptFolder, summaryFolder) {
  console.log(`\nWatch mode active — monitoring "${INPUT_SUBFOLDER}" for changes…`);
  console.log('(Press Ctrl+C to stop)\n');

  const pending = new Set();

  fs.watch(transcriptFolder, async (event, filename) => {
    if (!filename) return;
    if (!TRANSCRIPT_EXTS.has(path.extname(filename).toLowerCase())) return;
    if (pending.has(filename)) return;

    pending.add(filename);
    setTimeout(async () => {
      pending.delete(filename);
      const srcPath = path.join(transcriptFolder, filename);
      if (!fs.existsSync(srcPath)) return;

      const stem = path.basename(filename, path.extname(filename));
      process.stdout.write(`  [changed] ${filename} → ${stem}.docx … `);
      try {
        const result = await processFile(srcPath, summaryFolder);
        if (result.status === 'skipped') {
          console.log('skipped (up to date)');
        } else {
          console.log(`saved (${result.actionCount} action item${result.actionCount !== 1 ? 's' : ''})`);
        }
      } catch (err) {
        console.log(`FAILED — ${err.message}`);
      }
    }, 1500);
  });
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const args      = process.argv.slice(2);
  const watchMode = args.includes('--watch');
  const rootArg   = args.find(a => !a.startsWith('--'));

  if (!rootArg) {
    console.log(`
Meeting Notes Agent — zero tokens, powered by Ollama

Usage:
  node meeting_notes_agent.js "/path/to/Meeting notes"
  node meeting_notes_agent.js "/path/to/Meeting notes" --watch

Expected folder layout:
  Meeting notes/
    Meeting transcript/   ← place your .txt / .vtt / .docx transcripts here
    Meeting summary/      ← Word docs are saved here (auto-created)

Setup (one-time):
  1. Install Ollama  →  https://ollama.ai
  2. ollama pull llama3.2
  3. npm install

Your OneDrive path (macOS):
  ~/Library/CloudStorage/OneDrive-Infoblox/Documents/Meeting\\ notes
    `);
    process.exit(1);
  }

  const rootFolder = path.resolve(rootArg.replace(/^~/, process.env.HOME || ''));
  if (!fs.existsSync(rootFolder)) {
    console.error(`Root folder not found: ${rootFolder}`);
    process.exit(1);
  }

  console.log('Meeting Notes Agent');
  console.log(`Root   : ${rootFolder}`);
  console.log(`Input  : ${INPUT_SUBFOLDER}/`);
  console.log(`Output : ${OUTPUT_SUBFOLDER}/  (.docx)`);
  console.log(`Model  : ${MODEL}  (${OLLAMA_BASE})\n`);

  // ── Check docx package ──
  try { require('docx'); } catch {
    console.error('Missing dependency: run  npm install  first.');
    process.exit(1);
  }

  // ── Check Ollama ──
  process.stdout.write('Checking Ollama… ');
  const available = await checkOllama();
  if (!available) {
    console.error(`not running\n\nInstall Ollama (https://ollama.ai) then: ollama pull ${MODEL}\n`);
    process.exit(1);
  }
  console.log(`OK (${available.length} model${available.length !== 1 ? 's' : ''} available)`);

  const modelOk = available.some(m => m.split(':')[0] === MODEL.split(':')[0]);
  if (!modelOk) {
    console.log(`\nModel "${MODEL}" not installed.`);
    console.log(available.length
      ? `Available: ${available.join(', ')}\nRe-run with: OLLAMA_MODEL=<name> node meeting_notes_agent.js …`
      : `Run: ollama pull ${MODEL}`);
    process.exit(1);
  }

  // ── Resolve subfolders ──
  const { transcriptFolder, summaryFolder } = resolveSubfolders(rootFolder);

  // ── Process existing transcripts ──
  const files = scanFolder(transcriptFolder);
  if (!files.length) {
    console.log(`\nNo transcript files found in "${INPUT_SUBFOLDER}".`);
  } else {
    console.log(`\nFound ${files.length} transcript${files.length !== 1 ? 's' : ''} in "${INPUT_SUBFOLDER}":\n`);
    let ok = 0, skipped = 0, failed = 0;

    for (const srcPath of files) {
      const stem = path.basename(srcPath, path.extname(srcPath));
      process.stdout.write(`  ${path.basename(srcPath)} → ${stem}.docx … `);
      try {
        const result = await processFile(srcPath, summaryFolder);
        if (result.status === 'skipped') {
          console.log('skipped (up to date)');
          skipped++;
        } else {
          console.log(`saved (${result.actionCount} action item${result.actionCount !== 1 ? 's' : ''})`);
          ok++;
        }
      } catch (err) {
        console.log(`FAILED — ${err.message}`);
        failed++;
      }
    }

    console.log(`\n${ok} saved, ${skipped} skipped, ${failed} failed.`);
    if (failed) process.exitCode = 1;
  }

  if (watchMode) watchFolder(transcriptFolder, summaryFolder);
}

main().catch(err => { console.error('\nFatal:', err.message); process.exit(1); });
