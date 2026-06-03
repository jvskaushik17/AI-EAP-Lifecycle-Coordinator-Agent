#!/usr/bin/env node
/**
 * Meeting Notes Agent — powered by Ollama (local LLM, zero tokens)
 * -----------------------------------------------------------------
 * No API keys. No cloud auth. Runs entirely on your machine.
 *
 * Prerequisites (one-time):
 *   1. Install Ollama     →  https://ollama.ai
 *   2. Pull a model       →  ollama pull llama3.2
 *
 * Usage:
 *   node meeting_notes_agent.js /path/to/Meeting\ notes
 *   node meeting_notes_agent.js /path/to/Meeting\ notes --watch   # watch for new files
 *
 * OneDrive path (macOS):
 *   ~/Library/CloudStorage/OneDrive-Infoblox/Documents/Meeting notes
 *   — or —
 *   ~/OneDrive\ -\ Infoblox/Documents/Meeting\ notes
 *
 * Transcript formats supported: .txt  .vtt  .docx (needs: npm install mammoth)
 *
 * Optional env vars (no tokens needed):
 *   OLLAMA_HOST   – Ollama server URL  (default: http://localhost:11434)
 *   OLLAMA_MODEL  – Model to use       (default: llama3.2)
 *   SKIP_EXISTING – Skip up-to-date .md files (default: true)
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ─── Config ───────────────────────────────────────────────────────────────────

const OLLAMA_BASE     = (process.env.OLLAMA_HOST  || 'http://localhost:11434').replace(/\/$/, '');
const MODEL           = process.env.OLLAMA_MODEL  || 'llama3.2';
const SKIP_EXISTING   = process.env.SKIP_EXISTING !== 'false';
const TRANSCRIPT_EXTS = new Set(['.txt', '.vtt', '.docx']);

// Models known to support tool/function calling
const TOOL_CAPABLE_MODELS = ['llama3', 'llama3.1', 'llama3.2', 'llama3.3', 'qwen2.5', 'qwen2', 'mistral-nemo', 'firefunction', 'command-r'];

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

function modelSupportsTools(modelName) {
  const lower = modelName.toLowerCase();
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
          meeting_title: {
            type:        'string',
            description: 'Topic or title of the meeting.',
          },
          meeting_datetime: {
            type:        'string',
            description: 'Date and time (ISO 8601 or natural language). Use "Date not specified" if unknown.',
          },
          attendees: {
            type:  'array',
            items: { type: 'string' },
            description: 'All identifiable participants.',
          },
          meeting_notes: {
            type:  'array',
            items: {
              type: 'object',
              properties: {
                topic:   { type: 'string' },
                summary: { type: 'string' },
              },
              required: ['topic', 'summary'],
            },
            description: 'Key discussion points, one entry per topic.',
          },
          action_items: {
            type:  'array',
            items: {
              type: 'object',
              properties: {
                task:     { type: 'string',  description: 'What needs to be done.' },
                owner:    { type: 'string',  description: 'Person responsible.' },
                due_date: { type: 'string',  description: 'Deadline, if mentioned.' },
                priority: { type: 'string',  enum: ['High', 'Medium', 'Low'] },
              },
              required: ['task', 'owner', 'priority'],
            },
            description: 'Every action item with owner, deadline, and priority.',
          },
          decisions_made: {
            type:  'array',
            items: { type: 'string' },
            description: 'Concrete decisions reached during the meeting.',
          },
          next_steps: {
            type:        'string',
            description: 'Follow-up plan or next-meeting details.',
          },
        },
        required: ['meeting_title', 'meeting_datetime', 'meeting_notes', 'action_items'],
      },
    },
  },
];

// ─── Prompts ──────────────────────────────────────────────────────────────────

const SYSTEM_TOOL = `You are a precise meeting-notes assistant.
Analyse the transcript and call save_meeting_notes with:
• meeting_title and meeting_datetime (exact or inferred)
• attendees — everyone identifiable
• meeting_notes — every discussion topic with a concise summary
• action_items — EVERY item: task, owner, due_date (if mentioned), priority
  (High=urgent, Medium=standard, Low=nice-to-have)
• decisions_made — concrete decisions
• next_steps — follow-up or next meeting info
Always call the function. Never respond with plain text.`;

const SYSTEM_JSON = `You are a precise meeting-notes assistant.
Analyse the transcript and return ONLY a JSON object (no explanation, no markdown) with:
{
  "meeting_title": "...",
  "meeting_datetime": "... or Date not specified",
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
    const choice = resp.choices?.[0];
    const msg    = choice?.message;

    // Model called the tool correctly
    if (msg?.tool_calls?.length) {
      for (const tc of msg.tool_calls) {
        if (tc.function?.name === 'save_meeting_notes') {
          try {
            return typeof tc.function.arguments === 'string'
              ? JSON.parse(tc.function.arguments)
              : tc.function.arguments;
          } catch { /* bad JSON, fall through */ }
        }
      }
    }

    // Model responded with text — try to parse embedded JSON
    const text = msg?.content || '';
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      try {
        const parsed = JSON.parse(jsonMatch[0]);
        if (parsed.meeting_title && Array.isArray(parsed.action_items)) return parsed;
      } catch {}
    }

    // Nudge and retry
    if (attempt < 3) {
      messages = [
        ...messages,
        { role: 'assistant', content: text },
        { role: 'user',      content: 'Please call the save_meeting_notes function. Do not return plain text.' },
      ];
    }
  }
  return null;
}

async function extractWithJson(transcript) {
  const messages = [
    { role: 'system', content: SYSTEM_JSON },
    { role: 'user',   content: `Analyse this transcript and return structured JSON only.\n\nTRANSCRIPT:\n---\n${transcript}\n---` },
  ];

  for (let attempt = 1; attempt <= 3; attempt++) {
    const resp = await ollamaChat(messages);
    const text = resp.choices?.[0]?.message?.content || '';

    // strip markdown fences if present
    const cleaned   = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
    const jsonMatch = cleaned.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      try {
        const parsed = JSON.parse(jsonMatch[0]);
        if (parsed.meeting_title && Array.isArray(parsed.action_items)) return parsed;
      } catch {}
    }
  }
  return null;
}

async function extractMeetingData(transcript) {
  // Try tool calling first; fall back to JSON prompt if model doesn't support it
  if (modelSupportsTools(MODEL)) {
    const result = await extractWithTools(transcript);
    if (result) return result;
  }
  return extractWithJson(transcript);
}

// ─── Text extraction from files ───────────────────────────────────────────────

function parseVtt(text) {
  return text
    .split('\n')
    .filter(l => {
      const t = l.trim();
      return t
        && !t.startsWith('WEBVTT')
        && !/^\d{2}:\d{2}/.test(t)
        && !/^NOTE/.test(t)
        && !/^\d+$/.test(t);
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
      const result  = await mammoth.extractRawText({ buffer: buf });
      return result.value;
    } catch {
      process.stdout.write('\n    [hint] npm install mammoth for better .docx support — ');
      return buf.toString('utf8');
    }
  }

  return buf.toString('utf8');
}

// ─── Markdown formatter ───────────────────────────────────────────────────────

function formatMarkdown(data) {
  const lines = [];

  lines.push(
    `# Meeting Notes: ${data.meeting_title || 'Meeting'}`,
    '',
    '## Meeting Details',
    `- **Date/Time:** ${data.meeting_datetime || 'Date not specified'}`,
  );
  if (data.attendees?.length) lines.push(`- **Attendees:** ${data.attendees.join(', ')}`);
  lines.push('');

  if (data.meeting_notes?.length) {
    lines.push('## Discussion Notes', '');
    for (const n of data.meeting_notes) {
      lines.push(`### ${n.topic}`, n.summary || '', '');
    }
  }

  if (data.decisions_made?.length) {
    lines.push('## Decisions Made');
    for (const d of data.decisions_made) lines.push(`- ${d}`);
    lines.push('');
  }

  if (data.action_items?.length) {
    lines.push(
      '## Action Items', '',
      '| # | Task | Owner | Due Date | Priority |',
      '|---|------|-------|----------|----------|',
    );
    data.action_items.forEach((a, i) => {
      const task     = (a.task     || '').replace(/\|/g, '\\|');
      const owner    = (a.owner    || 'TBD').replace(/\|/g, '\\|');
      const due_date = (a.due_date || 'TBD').replace(/\|/g, '\\|');
      lines.push(`| ${i + 1} | ${task} | ${owner} | ${due_date} | ${a.priority || 'Medium'} |`);
    });
    lines.push('');
  }

  if (data.next_steps) lines.push('## Next Steps', data.next_steps, '');

  lines.push('---', `*Meeting Notes Agent · ${new Date().toLocaleString()} · model: ${MODEL}*`);
  return lines.join('\n');
}

// ─── File-level processing ────────────────────────────────────────────────────

function isUpToDate(srcPath, outPath) {
  if (!SKIP_EXISTING) return false;
  try {
    return fs.statSync(outPath).mtimeMs >= fs.statSync(srcPath).mtimeMs;
  } catch {
    return false;
  }
}

async function processFile(filePath) {
  const stem    = path.basename(filePath, path.extname(filePath));
  const outPath = path.join(path.dirname(filePath), `${stem}.md`);

  if (isUpToDate(filePath, outPath)) return { status: 'skipped' };

  const text = await readTranscript(filePath);
  if (!text.trim()) throw new Error('file is empty');

  const data = await extractMeetingData(text);
  if (!data)  throw new Error('could not extract structured data after retries');

  fs.writeFileSync(outPath, formatMarkdown(data), 'utf8');
  return { status: 'ok', outPath, actionCount: (data.action_items || []).length };
}

// ─── Folder scan ──────────────────────────────────────────────────────────────

function scanFolder(folderPath) {
  return fs.readdirSync(folderPath, { withFileTypes: true })
    .filter(e => e.isFile() && TRANSCRIPT_EXTS.has(path.extname(e.name).toLowerCase()))
    .map(e => path.join(folderPath, e.name))
    .sort();
}

// ─── Watch mode ───────────────────────────────────────────────────────────────

function watchFolder(folderPath) {
  console.log('\nWatch mode active — waiting for new or changed transcript files…');
  console.log('(Press Ctrl+C to stop)\n');

  const pending = new Set();

  fs.watch(folderPath, async (event, filename) => {
    if (!filename) return;
    const ext = path.extname(filename).toLowerCase();
    if (!TRANSCRIPT_EXTS.has(ext)) return;
    if (pending.has(filename)) return;

    // Debounce — file may still be writing
    pending.add(filename);
    setTimeout(async () => {
      pending.delete(filename);
      const filePath = path.join(folderPath, filename);
      if (!fs.existsSync(filePath)) return;

      process.stdout.write(`  [new/changed] ${filename} → `);
      try {
        const result = await processFile(filePath);
        if (result.status === 'skipped') {
          console.log('skipped (up to date)');
        } else {
          console.log(`${path.basename(result.outPath)} (${result.actionCount} action item${result.actionCount !== 1 ? 's' : ''})`);
        }
      } catch (err) {
        console.log(`FAILED — ${err.message}`);
      }
    }, 1500);
  });
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const args       = process.argv.slice(2);
  const watchMode  = args.includes('--watch');
  const folderArg  = args.find(a => !a.startsWith('--'));

  if (!folderArg) {
    console.log(`
Meeting Notes Agent — zero tokens, powered by Ollama

Usage:
  node meeting_notes_agent.js <folder>           process all transcripts once
  node meeting_notes_agent.js <folder> --watch   also watch for new files

Setup (one-time):
  1. Install Ollama  →  https://ollama.ai
  2. ollama pull llama3.2

Your OneDrive folder (macOS):
  ~/Library/CloudStorage/OneDrive-Infoblox/Documents/Meeting\\ notes
    `);
    process.exit(1);
  }

  const folder = path.resolve(folderArg.replace(/^~/, process.env.HOME || '~'));
  if (!fs.existsSync(folder)) {
    console.error(`Folder not found: ${folder}`);
    process.exit(1);
  }

  console.log('Meeting Notes Agent');
  console.log(`Folder : ${folder}`);
  console.log(`Model  : ${MODEL}  (${OLLAMA_BASE})\n`);

  // ── Verify Ollama is running ──
  process.stdout.write('Checking Ollama… ');
  const available = await checkOllama();

  if (!available) {
    console.error(`not found\n
Ollama is not running. Please:
  1. Install Ollama  →  https://ollama.ai
  2. ollama pull ${MODEL}
  3. Re-run this agent.
`);
    process.exit(1);
  }
  console.log(`OK (${available.length} model${available.length !== 1 ? 's' : ''} available)`);

  const modelOk = available.some(m => m.split(':')[0] === MODEL.split(':')[0]);
  if (!modelOk) {
    console.log(`\nModel "${MODEL}" is not installed.`);
    if (available.length) {
      console.log(`Available: ${available.join(', ')}`);
      console.log(`Re-run with:  OLLAMA_MODEL=${available[0].split(':')[0]} node meeting_notes_agent.js <folder>`);
    } else {
      console.log(`Run:  ollama pull ${MODEL}`);
    }
    process.exit(1);
  }

  // ── Process existing files ──
  const files = scanFolder(folder);
  if (!files.length) {
    console.log('\nNo transcript files (.txt / .vtt / .docx) found.');
  } else {
    console.log(`\nProcessing ${files.length} file${files.length !== 1 ? 's' : ''}:\n`);
    let ok = 0, skipped = 0, failed = 0;

    for (const filePath of files) {
      process.stdout.write(`  ${path.basename(filePath)} → `);
      try {
        const result = await processFile(filePath);
        if (result.status === 'skipped') {
          console.log('skipped (up to date)');
          skipped++;
        } else {
          console.log(`${path.basename(result.outPath)} (${result.actionCount} action item${result.actionCount !== 1 ? 's' : ''})`);
          ok++;
        }
      } catch (err) {
        console.log(`FAILED — ${err.message}`);
        failed++;
      }
    }

    console.log(`\n${ok} processed, ${skipped} skipped, ${failed} failed.`);
    if (failed) process.exitCode = 1;
  }

  // ── Watch mode ──
  if (watchMode) watchFolder(folder);
}

main().catch(err => { console.error('\nFatal:', err.message); process.exit(1); });
