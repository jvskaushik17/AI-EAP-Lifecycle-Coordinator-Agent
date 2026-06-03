#!/usr/bin/env node
/**
 * Meeting Notes Agent — Playwright + Ollama (zero tokens)
 * --------------------------------------------------------
 * Opens a real browser so you sign in with your normal Microsoft/SSO
 * credentials — no app registrations, no API keys, no Azure config.
 *
 * First run  : browser opens visibly → sign in → session is saved
 * Later runs : headless, silent (session auto-restores)
 *
 * What it does:
 *   1. Reads every transcript from OneDrive "Meeting transcript" folder
 *   2. Extracts notes & action items with a local Ollama model
 *   3. Writes a Word doc (.docx) to "Meeting summary" (same OneDrive)
 *
 * OneDrive layout:
 *   Documents/Meeting notes/
 *     Meeting transcript/   ← input  (.txt / .vtt / .docx)
 *     Meeting summary/      ← output (.docx, auto-created)
 *
 * Prerequisites (one-time):
 *   npm install
 *   npx playwright install chromium
 *   ollama pull llama3.2      (https://ollama.ai)
 *
 * Usage:
 *   node meeting_notes_agent.js
 *   node meeting_notes_agent.js --watch      (poll every 10 min)
 *   node meeting_notes_agent.js --reauth     (force fresh browser login)
 *
 * Optional env vars:
 *   OLLAMA_HOST     Ollama URL   (default: http://localhost:11434)
 *   OLLAMA_MODEL    Model name   (default: llama3.2)
 *   SKIP_EXISTING   Skip up-to-date summaries (default: true)
 *   POLL_INTERVAL   Watch poll interval in ms (default: 600000 = 10 min)
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ─── SharePoint / OneDrive config (from the URL you provided) ─────────────────

const SP_HOST        = 'https://infoblox-my.sharepoint.com';
const SP_SITE        = '/personal/kjandhyala1_infoblox_com';
const SP_SITE_URL    = `${SP_HOST}${SP_SITE}`;
const SP_API         = `${SP_SITE_URL}/_api`;

// Server-relative folder paths (absolute within the SharePoint site collection)
const NOTES_ROOT     = `${SP_SITE}/Documents/Meeting notes`;
const TRANSCRIPT_DIR = `${NOTES_ROOT}/Meeting transcript`;
const SUMMARY_DIR    = `${NOTES_ROOT}/Meeting summary`;

// Auth session persisted here so you only sign in once
const SESSION_FILE   = path.join(__dirname, '.sp_session.json');

// ─── Ollama config ────────────────────────────────────────────────────────────

const OLLAMA_BASE     = (process.env.OLLAMA_HOST || 'http://localhost:11434').replace(/\/$/, '');
const MODEL           = process.env.OLLAMA_MODEL || 'llama3.2';
const SKIP_EXISTING   = process.env.SKIP_EXISTING !== 'false';
const POLL_INTERVAL   = parseInt(process.env.POLL_INTERVAL || '600000', 10);
const TRANSCRIPT_EXTS = new Set(['.txt', '.vtt', '.docx']);

const TOOL_CAPABLE_MODELS = [
  'llama3', 'llama3.1', 'llama3.2', 'llama3.3',
  'qwen2.5', 'qwen2', 'mistral-nemo', 'firefunction', 'command-r',
];

// ─── Browser / auth ───────────────────────────────────────────────────────────

async function launchBrowser(headless) {
  const { chromium } = require('playwright');
  const contextOptions = { ...(fs.existsSync(SESSION_FILE) ? { storageState: SESSION_FILE } : {}) };
  const browser = await chromium.launch({ headless });
  const context = await browser.newContext(contextOptions);
  const page    = await context.newPage();
  return { browser, context, page };
}

async function authenticate(forceReauth = false) {
  const hasSession = !forceReauth && fs.existsSync(SESSION_FILE);

  // Try silently first if we have a saved session
  if (hasSession) {
    const { browser, context, page } = await launchBrowser(true);
    try {
      await page.goto(`${SP_SITE_URL}/_api/web/title`, { timeout: 15000 });
      const body = await page.content();
      if (body.includes('"Title"') || body.includes('Infoblox')) {
        console.log('Session restored (headless).');
        return { browser, context, page };
      }
    } catch {}
    await browser.close();
  }

  // Interactive sign-in
  console.log('\nOpening browser for Microsoft sign-in…');
  console.log('Sign in with your Infoblox credentials. The window will close automatically.\n');
  const { browser, context, page } = await launchBrowser(false);

  await page.goto(`${SP_HOST}/personal/kjandhyala1_infoblox_com/_layouts/15/onedrive.aspx`);

  // Wait until the OneDrive page loads (sign-in complete)
  await page.waitForFunction(
    () => document.title && !document.title.toLowerCase().includes('sign') &&
          !document.title.toLowerCase().includes('login'),
    { timeout: 180_000 },
  );
  await page.waitForTimeout(2000); // let cookies settle

  // Persist the session
  await context.storageState({ path: SESSION_FILE });
  console.log('Signed in. Session saved for future runs.\n');

  return { browser, context, page };
}

// ─── SharePoint REST API helpers ──────────────────────────────────────────────

async function spGet(page, path, asBuffer = false) {
  const url = `${SP_API}${path}`;
  const res  = await page.request.get(url, {
    headers: { Accept: 'application/json;odata=verbose' },
  });
  if (res.status() === 401 || res.status() === 403) {
    throw Object.assign(new Error('Auth expired — re-run with --reauth'), { code: 'REAUTH' });
  }
  if (!res.ok() && res.status() !== 404) {
    throw new Error(`SharePoint GET ${path} → ${res.status()}`);
  }
  if (asBuffer) return res.body();
  if (res.status() === 404) return null;
  return res.json();
}

async function getDigest(page) {
  const res  = await page.request.post(`${SP_API}/contextinfo`, {
    headers: { Accept: 'application/json;odata=verbose' },
  });
  const data = await res.json();
  return data.d.GetContextWebInformation.FormDigestValue;
}

async function spPost(page, urlPath, bodyBuffer, contentType, digest) {
  const res = await page.request.post(`${SP_API}${urlPath}`, {
    headers: {
      Accept:           'application/json;odata=verbose',
      'X-RequestDigest': digest,
      'Content-Type':   contentType,
    },
    data: bodyBuffer,
  });
  if (!res.ok()) {
    const txt = await res.text();
    throw new Error(`SharePoint POST ${urlPath} → ${res.status()}: ${txt.slice(0, 300)}`);
  }
  return res;
}

// ─── OneDrive operations ──────────────────────────────────────────────────────

function encodeFolder(serverRelPath) {
  // OData literal: decodedurl='...'  — single-quotes escaped as %27 in path component
  return `GetFolderByServerRelativePath(decodedurl='${serverRelPath}')`;
}

function encodeFile(serverRelUrl) {
  return `GetFileByServerRelativePath(decodedurl='${serverRelUrl}')`;
}

async function listTranscripts(page) {
  const enc  = encodeFolder(TRANSCRIPT_DIR);
  const data = await spGet(page, `/web/${enc}/Files?$select=Name,ServerRelativeUrl,TimeLastModified`);
  if (!data) {
    throw new Error(
      `"Meeting transcript" folder not found in OneDrive.\n` +
      `  Expected: ${TRANSCRIPT_DIR}\n` +
      `  Create the folder and add your transcript files.`
    );
  }
  const all = data.d.results || [];
  return all.filter(f => TRANSCRIPT_EXTS.has(path.extname(f.Name).toLowerCase()));
}

async function downloadTranscript(page, serverRelUrl) {
  return spGet(page, `/web/${encodeFile(serverRelUrl)}/$value`, true);
}

async function ensureSummaryFolder(page, digest) {
  const enc  = encodeFolder(SUMMARY_DIR);
  const data = await spGet(page, `/web/${enc}`);
  if (data) return; // already exists

  await spPost(
    page,
    `/web/folders/add('${SUMMARY_DIR}')`,
    Buffer.alloc(0),
    'application/json;odata=verbose',
    digest,
  );
  console.log(`  Created OneDrive folder: Meeting summary`);
}

async function fileExistsInSummary(page, filename) {
  const enc  = encodeFile(`${SUMMARY_DIR}/${filename}`);
  const data = await spGet(page, `/web/${enc}?$select=TimeLastModified`);
  return data ? new Date(data.d.TimeLastModified) : null;
}

async function uploadSummary(page, filename, buffer, digest) {
  const enc = encodeFolder(SUMMARY_DIR);
  await spPost(
    page,
    `/web/${enc}/Files/add(overwrite=true,url='${encodeURIComponent(filename)}')`,
    buffer,
    'application/octet-stream',
    digest,
  );
}

// ─── Text extraction from transcript ─────────────────────────────────────────

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

async function bufferToText(filename, buf) {
  const ext = path.extname(filename).toLowerCase();
  if (ext === '.vtt') return parseVtt(buf.toString('utf8'));
  if (ext === '.docx') {
    try {
      const mammoth = require('mammoth');
      return (await mammoth.extractRawText({ buffer: buf })).value;
    } catch {
      return buf.toString('utf8');
    }
  }
  return buf.toString('utf8');
}

// ─── Ollama / extraction ──────────────────────────────────────────────────────

async function ollamaChat(messages, tools = []) {
  const res = await fetch(`${OLLAMA_BASE}/v1/chat/completions`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ model: MODEL, messages, stream: false, ...(tools.length ? { tools } : {}) }),
  });
  if (!res.ok) throw new Error(`Ollama ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

async function checkOllama() {
  try {
    const res  = await fetch(`${OLLAMA_BASE}/api/tags`, { signal: AbortSignal.timeout(4000) });
    const data = await res.json();
    return (data.models || []).map(m => m.name);
  } catch { return null; }
}

const TOOLS = [
  {
    type: 'function',
    function: {
      name:        'save_meeting_notes',
      description: 'Save structured meeting notes extracted from the transcript.',
      parameters: {
        type: 'object',
        properties: {
          meeting_title:    { type: 'string' },
          meeting_datetime: { type: 'string' },
          attendees:        { type: 'array', items: { type: 'string' } },
          meeting_notes: {
            type: 'array',
            items: {
              type: 'object',
              properties: { topic: { type: 'string' }, summary: { type: 'string' } },
              required: ['topic', 'summary'],
            },
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
          },
          decisions_made: { type: 'array', items: { type: 'string' } },
          next_steps:     { type: 'string' },
        },
        required: ['meeting_title', 'meeting_datetime', 'meeting_notes', 'action_items'],
      },
    },
  },
];

const SYS_TOOL = `You are a precise meeting-notes assistant.
Analyse the transcript and call save_meeting_notes with:
• meeting_title and meeting_datetime (exact or inferred; "Date not specified" if unknown)
• attendees — everyone identifiable
• meeting_notes — every topic with a concise summary
• action_items — every item: task, owner, due_date, priority (High/Medium/Low)
• decisions_made — concrete decisions
• next_steps — follow-up or next-meeting details
Always call the function. Never respond with plain text only.`;

const SYS_JSON = `You are a precise meeting-notes assistant.
Return ONLY valid JSON (no markdown fences, no prose):
{"meeting_title":"...","meeting_datetime":"...","attendees":["..."],
 "meeting_notes":[{"topic":"...","summary":"..."}],
 "action_items":[{"task":"...","owner":"...","due_date":"...","priority":"High|Medium|Low"}],
 "decisions_made":["..."],"next_steps":"..."}
Be exhaustive — do not miss any action items.`;

async function extractWithTools(transcript) {
  let messages = [
    { role: 'system', content: SYS_TOOL },
    { role: 'user',   content: `Analyse this transcript and call save_meeting_notes.\n\nTRANSCRIPT:\n---\n${transcript}\n---` },
  ];
  for (let i = 0; i < 3; i++) {
    const resp = await ollamaChat(messages, TOOLS);
    const msg  = resp.choices?.[0]?.message;
    if (msg?.tool_calls?.length) {
      for (const tc of msg.tool_calls) {
        if (tc.function?.name === 'save_meeting_notes') {
          try { return typeof tc.function.arguments === 'string' ? JSON.parse(tc.function.arguments) : tc.function.arguments; } catch {}
        }
      }
    }
    const text = msg?.content || '';
    const m    = text.match(/\{[\s\S]*\}/);
    if (m) { try { const p = JSON.parse(m[0]); if (p.meeting_title && Array.isArray(p.action_items)) return p; } catch {} }
    if (i < 2) messages = [...messages, { role: 'assistant', content: text }, { role: 'user', content: 'Call the save_meeting_notes function. Do not return plain text.' }];
  }
  return null;
}

async function extractWithJson(transcript) {
  const msgs = [{ role: 'system', content: SYS_JSON }, { role: 'user', content: `TRANSCRIPT:\n---\n${transcript}\n---\nReturn JSON only.` }];
  for (let i = 0; i < 3; i++) {
    const text = ((await ollamaChat(msgs)).choices?.[0]?.message?.content || '').replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
    const m    = text.match(/\{[\s\S]*\}/);
    if (m) { try { const p = JSON.parse(m[0]); if (p.meeting_title && Array.isArray(p.action_items)) return p; } catch {} }
  }
  return null;
}

async function extractMeetingData(transcript) {
  const lower = MODEL.toLowerCase();
  const supportsTools = TOOL_CAPABLE_MODELS.some(k => lower.includes(k));
  if (supportsTools) { const r = await extractWithTools(transcript); if (r) return r; }
  return extractWithJson(transcript);
}

// ─── Word document builder ────────────────────────────────────────────────────

async function buildWordDoc(data) {
  const {
    Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
    HeadingLevel, WidthType, ShadingType,
  } = require('docx');

  const BRAND = '1F3864';
  const HDRF  = 'D6E4F0';

  const priorityFill = p => ({ High: 'FDDEDE', Medium: 'FFF3CD', Low: 'D4EDDA' }[p] || 'FFFFFF');

  const hCell = t => new TableCell({ children: [new Paragraph({ children: [new TextRun({ text: t, bold: true })] })], shading: { type: ShadingType.SOLID, color: HDRF }, width: { size: 20, type: WidthType.PERCENTAGE } });
  const dCell = (t, fill = 'FFFFFF') => new TableCell({ children: [new Paragraph({ text: t || '' })], shading: { type: ShadingType.SOLID, color: fill }, width: { size: 20, type: WidthType.PERCENTAGE } });

  const children = [
    new Paragraph({ text: `Meeting Notes: ${data.meeting_title || 'Meeting'}`, heading: HeadingLevel.HEADING_1, spacing: { after: 100 } }),
    new Paragraph({ children: [new TextRun({ text: 'Date/Time:  ', bold: true }), new TextRun(data.meeting_datetime || 'Date not specified')], spacing: { after: 60 } }),
    ...(data.attendees?.length ? [new Paragraph({ children: [new TextRun({ text: 'Attendees:  ', bold: true }), new TextRun(data.attendees.join(', '))], spacing: { after: 200 } })] : []),
  ];

  if (data.meeting_notes?.length) {
    children.push(new Paragraph({ text: 'Discussion Notes', heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 100 } }));
    for (const n of data.meeting_notes) {
      children.push(
        new Paragraph({ text: n.topic, heading: HeadingLevel.HEADING_3, spacing: { before: 120, after: 60 } }),
        new Paragraph({ text: n.summary || '', spacing: { after: 80 } }),
      );
    }
  }

  if (data.decisions_made?.length) {
    children.push(new Paragraph({ text: 'Decisions Made', heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 100 } }));
    for (const d of data.decisions_made) children.push(new Paragraph({ text: `• ${d}`, spacing: { after: 60 } }));
  }

  if (data.action_items?.length) {
    children.push(new Paragraph({ text: 'Action Items', heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 120 } }));
    children.push(new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      rows: [
        new TableRow({ children: [hCell('#'), hCell('Task'), hCell('Owner'), hCell('Due Date'), hCell('Priority')], tableHeader: true }),
        ...data.action_items.map((a, i) => new TableRow({ children: [
          dCell(String(i + 1)),
          dCell(a.task || ''),
          dCell(a.owner || 'TBD'),
          dCell(a.due_date || 'TBD'),
          new TableCell({ children: [new Paragraph({ children: [new TextRun({ text: a.priority || 'Medium', bold: true })] })], shading: { type: ShadingType.SOLID, color: priorityFill(a.priority) }, width: { size: 20, type: WidthType.PERCENTAGE } }),
        ]})),
      ],
    }));
    children.push(new Paragraph({ text: '', spacing: { after: 100 } }));
  }

  if (data.next_steps) {
    children.push(
      new Paragraph({ text: 'Next Steps', heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 100 } }),
      new Paragraph({ text: data.next_steps }),
    );
  }

  children.push(new Paragraph({ children: [new TextRun({ text: `Generated by Meeting Notes Agent · ${new Date().toLocaleString()} · model: ${MODEL}`, italics: true, size: 18, color: '888888' })], spacing: { before: 400 } }));

  const doc = new Document({
    styles: { default: { heading1: { run: { size: 32, bold: true, color: BRAND } }, heading2: { run: { size: 26, bold: true, color: BRAND } }, heading3: { run: { size: 22, bold: true, color: '333333' } } } },
    sections: [{ children }],
  });

  return Packer.toBuffer(doc);
}

// ─── Process a single transcript ─────────────────────────────────────────────

async function processFile(page, file, digest) {
  const stem    = path.basename(file.Name, path.extname(file.Name));
  const outName = `${stem}.docx`;

  // Skip if summary is newer than transcript
  if (SKIP_EXISTING) {
    const existing = await fileExistsInSummary(page, outName);
    if (existing && existing >= new Date(file.TimeLastModified)) {
      return { status: 'skipped' };
    }
  }

  const raw  = await downloadTranscript(page, file.ServerRelativeUrl);
  const text = await bufferToText(file.Name, Buffer.from(raw));
  if (!text.trim()) throw new Error('transcript is empty');

  const data = await extractMeetingData(text);
  if (!data) throw new Error('Ollama could not extract structured data after retries');

  const docBuf = await buildWordDoc(data);
  await uploadSummary(page, outName, docBuf, digest);

  return { status: 'ok', outName, actionCount: (data.action_items || []).length };
}

// ─── Full run ─────────────────────────────────────────────────────────────────

async function runOnce(page) {
  const digest = await getDigest(page);
  await ensureSummaryFolder(page, digest);

  const files = await listTranscripts(page);
  if (!files.length) {
    console.log('No transcript files found in "Meeting transcript".');
    return { ok: 0, skipped: 0, failed: 0 };
  }

  console.log(`Found ${files.length} transcript${files.length !== 1 ? 's' : ''}:\n`);
  let ok = 0, skipped = 0, failed = 0;

  for (const file of files) {
    process.stdout.write(`  ${file.Name} → `);
    try {
      const result = await processFile(page, file, digest);
      if (result.status === 'skipped') { console.log('skipped (up to date)'); skipped++; }
      else                             { console.log(`${result.outName} saved (${result.actionCount} action item${result.actionCount !== 1 ? 's' : ''})`); ok++; }
    } catch (err) {
      console.log(`FAILED — ${err.message}`);
      failed++;
    }
  }

  return { ok, skipped, failed };
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const args      = process.argv.slice(2);
  const watchMode = args.includes('--watch');
  const reauth    = args.includes('--reauth');

  if (args.includes('--help') || args.includes('-h')) {
    console.log(`
Meeting Notes Agent — browser-based SharePoint access, zero tokens

Usage:
  node meeting_notes_agent.js              process all transcripts
  node meeting_notes_agent.js --watch      also poll every ${POLL_INTERVAL / 60000} min
  node meeting_notes_agent.js --reauth     force a fresh browser sign-in

Setup (one-time):
  npm install
  npx playwright install chromium
  ollama pull llama3.2

OneDrive layout:
  Documents/Meeting notes/
    Meeting transcript/   ← .txt / .vtt / .docx transcripts
    Meeting summary/      ← .docx summaries (auto-created)
    `);
    process.exit(0);
  }

  // ── Check docx / Ollama ──
  try { require('docx'); } catch { console.error('Run: npm install'); process.exit(1); }

  process.stdout.write('Checking Ollama… ');
  const models = await checkOllama();
  if (!models) { console.error(`not running\n\nInstall Ollama (https://ollama.ai) then: ollama pull ${MODEL}\n`); process.exit(1); }
  const modelOk = models.some(m => m.split(':')[0] === MODEL.split(':')[0]);
  if (!modelOk) {
    console.log(`Model "${MODEL}" not installed. Available: ${models.join(', ') || '(none)'}`);
    console.log(`Run: ollama pull ${MODEL}`);
    process.exit(1);
  }
  console.log(`OK · model: ${MODEL}`);

  // ── Authenticate ──
  console.log('\nConnecting to SharePoint…');
  let browser, context, page;
  try {
    ({ browser, context, page } = await authenticate(reauth));
  } catch (err) {
    console.error(`Auth failed: ${err.message}`);
    console.error('Try:  node meeting_notes_agent.js --reauth');
    process.exit(1);
  }

  console.log(`OneDrive: ${SP_SITE_URL}`);
  console.log(`Input   : Meeting transcript/`);
  console.log(`Output  : Meeting summary/  (.docx)\n`);

  // ── Process ──
  try {
    const { ok, skipped, failed } = await runOnce(page);
    console.log(`\n${ok} saved, ${skipped} skipped, ${failed} failed.`);
    if (failed) process.exitCode = 1;
  } catch (err) {
    if (err.code === 'REAUTH') {
      console.error('\nSession expired. Run:  node meeting_notes_agent.js --reauth');
    } else {
      console.error(`\nError: ${err.message}`);
    }
    await browser.close();
    process.exit(1);
  }

  // ── Watch mode ──
  if (watchMode) {
    console.log(`\nWatch mode — polling every ${POLL_INTERVAL / 60000} min. Press Ctrl+C to stop.\n`);
    const tick = async () => {
      console.log(`[${new Date().toLocaleTimeString()}] Checking for new transcripts…`);
      try {
        const { ok, skipped, failed } = await runOnce(page);
        if (ok > 0 || failed > 0) console.log(`  ${ok} saved, ${skipped} skipped, ${failed} failed.`);
        else console.log('  No changes.');
      } catch (err) {
        console.error(`  Error: ${err.message}`);
      }
    };
    setInterval(tick, POLL_INTERVAL);
  } else {
    await browser.close();
  }
}

main().catch(async err => { console.error('\nFatal:', err.message); process.exit(1); });
