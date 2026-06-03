#!/usr/bin/env node
/**
 * Meeting Notes Agent
 * -------------------
 * Reads every transcript from your OneDrive "Documents/Meeting notes" folder,
 * extracts meeting notes and action items using Claude, then saves a .md file
 * next to each transcript in the same OneDrive folder using the same name.
 *
 * Usage:
 *   node meeting_notes_agent.js
 *
 * Required environment variables (copy .env.example → .env and fill in):
 *   ANTHROPIC_API_KEY   – your Anthropic API key (sk-ant-…)
 *   AZURE_TENANT_ID     – Azure AD tenant ID
 *   AZURE_CLIENT_ID     – Azure AD app (client) ID
 *
 * Optional:
 *   ONEDRIVE_FOLDER     – OneDrive path (default: "Documents/Meeting notes")
 *
 * Supported transcript formats: .txt  .vtt  .docx (requires npm install mammoth)
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ─── Load .env ───────────────────────────────────────────────────────────────

(function loadEnv() {
  const envFile = path.join(__dirname, '.env');
  if (!fs.existsSync(envFile)) return;
  for (const line of fs.readFileSync(envFile, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    const val = trimmed.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
    if (key && !(key in process.env)) process.env[key] = val;
  }
})();

// ─── Config ───────────────────────────────────────────────────────────────────

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;
const AZURE_TENANT_ID   = process.env.AZURE_TENANT_ID;
const AZURE_CLIENT_ID   = process.env.AZURE_CLIENT_ID;
const ONEDRIVE_FOLDER   = process.env.ONEDRIVE_FOLDER || 'Documents/Meeting notes';
const TOKEN_FILE        = path.join(__dirname, '.meeting_agent_token.json');
const GRAPH_BASE        = 'https://graph.microsoft.com/v1.0';
const GRAPH_SCOPES      = 'https://graph.microsoft.com/Files.ReadWrite offline_access openid profile';

// ─── Token management ─────────────────────────────────────────────────────────

function loadTokenCache() {
  try { return JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8')); } catch { return null; }
}

function saveTokenCache(data) {
  fs.writeFileSync(TOKEN_FILE, JSON.stringify(data, null, 2));
}

function tokenIsValid(cache) {
  if (!cache?.access_token) return false;
  const expiry = (cache.acquired_at || 0) + (cache.expires_in || 0) * 1000;
  return Date.now() < expiry - 60_000; // 60-second buffer
}

async function refreshAccessToken(refreshTok) {
  const body = new URLSearchParams({
    grant_type:    'refresh_token',
    client_id:     AZURE_CLIENT_ID,
    refresh_token: refreshTok,
    scope:         GRAPH_SCOPES,
  });
  const res  = await fetch(`https://login.microsoftonline.com/${AZURE_TENANT_ID}/oauth2/v2.0/token`, { method: 'POST', body });
  const data = await res.json();
  if (!data.access_token) throw new Error(`Token refresh failed: ${data.error_description || data.error}`);
  data.acquired_at = Date.now();
  return data;
}

async function deviceCodeAuth() {
  // Step 1 – request a device code
  const dcRes = await fetch(
    `https://login.microsoftonline.com/${AZURE_TENANT_ID}/oauth2/v2.0/devicecode`,
    {
      method: 'POST',
      body: new URLSearchParams({ client_id: AZURE_CLIENT_ID, scope: GRAPH_SCOPES }),
    }
  );
  const dc = await dcRes.json();
  if (!dc.device_code) throw new Error(`Device code request failed: ${JSON.stringify(dc)}`);

  console.log('\n' + dc.message + '\n');

  // Step 2 – poll until the user completes sign-in
  const interval = (dc.interval || 5) * 1000;
  const deadline  = Date.now() + (dc.expires_in || 900) * 1000;

  while (Date.now() < deadline) {
    await sleep(interval);
    const tokenRes = await fetch(
      `https://login.microsoftonline.com/${AZURE_TENANT_ID}/oauth2/v2.0/token`,
      {
        method: 'POST',
        body: new URLSearchParams({
          grant_type:  'urn:ietf:params:oauth:grant-type:device_code',
          client_id:   AZURE_CLIENT_ID,
          device_code: dc.device_code,
        }),
      }
    );
    const data = await tokenRes.json();

    if (data.access_token) {
      data.acquired_at = Date.now();
      return data;
    }
    if (data.error === 'authorization_pending') continue;
    if (data.error === 'slow_down')            { await sleep(interval); continue; }
    throw new Error(`Auth failed: ${data.error_description || data.error}`);
  }
  throw new Error('Device code expired before the user signed in.');
}

async function getAccessToken() {
  let cache = loadTokenCache();

  if (tokenIsValid(cache)) return cache.access_token;

  if (cache?.refresh_token) {
    try {
      cache = await refreshAccessToken(cache.refresh_token);
      saveTokenCache(cache);
      return cache.access_token;
    } catch (e) {
      console.warn('Silent refresh failed, re-authenticating…', e.message);
    }
  }

  // Interactive device-code sign-in
  const tokens = await deviceCodeAuth();
  saveTokenCache(tokens);
  return tokens.access_token;
}

// ─── Microsoft Graph helpers ──────────────────────────────────────────────────

async function graphRequest(method, endpoint, body, contentType) {
  const token = await getAccessToken();
  const opts  = {
    method,
    headers: { Authorization: `Bearer ${token}` },
  };
  if (body !== undefined) {
    opts.headers['Content-Type'] = contentType || 'application/json';
    opts.body = body;
  }
  const res = await fetch(`${GRAPH_BASE}${endpoint}`, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Graph ${method} ${endpoint} → ${res.status}: ${text}`);
  }
  const ct = res.headers.get('Content-Type') || '';
  if (ct.includes('application/json')) return res.json();
  return res.arrayBuffer();
}

async function listFolder(folderPath) {
  const enc  = encodeURIComponent(folderPath);
  const data = await graphRequest('GET', `/me/drive/root:/${enc}:/children?$top=250&$select=name,id,size`);
  return data.value || [];
}

async function downloadFile(folderPath, fileName) {
  const enc = encodeURIComponent(`${folderPath}/${fileName}`);
  return graphRequest('GET', `/me/drive/root:/${enc}:/content`);
}

async function uploadFile(folderPath, fileName, textContent) {
  const enc = encodeURIComponent(`${folderPath}/${fileName}`);
  await graphRequest('PUT', `/me/drive/root:/${enc}:/content`, textContent, 'text/plain; charset=utf-8');
}

// ─── Text extraction ──────────────────────────────────────────────────────────

function parseVtt(text) {
  // Keep only speaker-dialogue lines; strip timestamps, NOTE blocks, header
  return text
    .split('\n')
    .filter(l => {
      const t = l.trim();
      return t && !t.startsWith('WEBVTT') && !/^\d{2}:\d{2}/.test(t) && !/^NOTE/.test(t) && !/^\d+$/.test(t);
    })
    .join('\n');
}

async function extractText(fileName, bytes) {
  const buf = Buffer.from(bytes);
  const ext = path.extname(fileName).toLowerCase();

  if (ext === '.vtt') return parseVtt(buf.toString('utf8'));

  if (ext === '.docx') {
    try {
      const mammoth = require('mammoth');
      const result  = await mammoth.extractRawText({ buffer: buf });
      return result.value;
    } catch {
      // mammoth not installed — warn and continue with raw bytes
      console.warn(`  [warn] Install mammoth (npm install mammoth) for better .docx support.`);
      return buf.toString('utf8');
    }
  }

  // .txt and everything else
  return buf.toString('utf8');
}

// ─── Claude tool definition ───────────────────────────────────────────────────

const TOOLS = [
  {
    name: 'extract_meeting_data',
    description:
      'Extract structured meeting information: title, date/time, attendees, ' +
      'topical notes, action items, decisions made, and next steps.',
    input_schema: {
      type: 'object',
      properties: {
        meeting_title: {
          type: 'string',
          description: 'Topic or title of the meeting.',
        },
        meeting_datetime: {
          type: 'string',
          description:
            'Date and time in ISO 8601 or human-readable form, or "Date not specified".',
        },
        attendees: {
          type: 'array',
          items: { type: 'string' },
          description: 'List of participants.',
        },
        meeting_notes: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              topic:   { type: 'string' },
              summary: { type: 'string' },
            },
            required: ['topic', 'summary'],
          },
          description: 'Key discussion points organised by topic.',
        },
        action_items: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              task:     { type: 'string', description: 'What needs to be done.' },
              owner:    { type: 'string', description: 'Who is responsible.' },
              due_date: { type: 'string', description: 'Deadline if mentioned.' },
              priority: { type: 'string', enum: ['High', 'Medium', 'Low'] },
            },
            required: ['task', 'owner', 'priority'],
          },
          description: 'Action items with owner, due date, and priority.',
        },
        decisions_made: {
          type: 'array',
          items: { type: 'string' },
          description: 'Concrete decisions reached.',
        },
        next_steps: {
          type: 'string',
          description: 'Follow-up plan or next meeting details.',
        },
      },
      required: ['meeting_title', 'meeting_datetime', 'meeting_notes', 'action_items'],
    },
  },
];

const SYSTEM_PROMPT = `You are a professional meeting-notes assistant.
Analyse the transcript and call extract_meeting_data to return:
• Meeting title and exact date/time (or best inference).
• All identifiable attendees.
• Concise, topic-organised notes covering every discussion point.
• Every action item: task, owner, due date (if stated), priority
  (High = urgent/critical, Medium = standard, Low = nice-to-have).
• Concrete decisions made.
• Next steps or follow-up meeting info.
Be exhaustive — do not skip any action items or discussion topics.`;

// ─── Claude extraction ────────────────────────────────────────────────────────

async function extractMeetingNotes(transcriptText) {
  const { default: Anthropic } = await import('@anthropic-ai/sdk');
  const client = new Anthropic({ apiKey: ANTHROPIC_API_KEY });

  let messages = [
    {
      role: 'user',
      content:
        'Analyse the following transcript with the extract_meeting_data tool.\n\n' +
        'TRANSCRIPT:\n---\n' + transcriptText + '\n---\n\nBe thorough.',
    },
  ];

  for (let iteration = 0; iteration < 10; iteration++) {
    const response = await client.messages.create({
      model:      'claude-sonnet-4-6',
      max_tokens: 4096,
      system:     SYSTEM_PROMPT,
      tools:      TOOLS,
      messages,
    });

    if (response.stop_reason === 'tool_use') {
      const toolUses   = response.content.filter(b => b.type === 'tool_use');
      const results    = [];
      let extracted    = null;

      for (const tu of toolUses) {
        if (tu.name === 'extract_meeting_data') extracted = tu.input;
        results.push({ type: 'tool_result', tool_use_id: tu.id, content: 'Recorded.' });
      }

      if (extracted) return extracted;

      messages = [
        ...messages,
        { role: 'assistant', content: response.content },
        { role: 'user',      content: results },
      ];
    } else {
      break;
    }
  }

  return null;
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

  if (data.attendees?.length) {
    lines.push(`- **Attendees:** ${data.attendees.join(', ')}`);
  }
  lines.push('');

  if (data.meeting_notes?.length) {
    lines.push('## Meeting Notes', '');
    for (const n of data.meeting_notes) {
      lines.push(`### ${n.topic}`, n.summary, '');
    }
  }

  if (data.decisions_made?.length) {
    lines.push('## Decisions Made');
    for (const d of data.decisions_made) lines.push(`- ${d}`);
    lines.push('');
  }

  if (data.action_items?.length) {
    lines.push(
      '## Action Items',
      '',
      '| # | Task | Owner | Due Date | Priority |',
      '|---|------|-------|----------|----------|',
    );
    data.action_items.forEach((a, i) => {
      lines.push(`| ${i + 1} | ${a.task} | ${a.owner || 'TBD'} | ${a.due_date || 'TBD'} | ${a.priority} |`);
    });
    lines.push('');
  }

  if (data.next_steps) {
    lines.push('## Next Steps', data.next_steps, '');
  }

  lines.push('---', `*Generated by Meeting Notes Agent on ${new Date().toLocaleString()}*`);
  return lines.join('\n');
}

// ─── Utilities ────────────────────────────────────────────────────────────────

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function validateEnv() {
  const missing = ['ANTHROPIC_API_KEY', 'AZURE_TENANT_ID', 'AZURE_CLIENT_ID'].filter(k => !process.env[k]);
  if (missing.length) {
    console.error(`Missing required environment variables: ${missing.join(', ')}`);
    console.error('Copy .env.example to .env and fill in the values.');
    process.exit(1);
  }
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  validateEnv();

  console.log('Meeting Notes Agent');
  console.log(`OneDrive folder : ${ONEDRIVE_FOLDER}`);
  console.log('');

  // Authenticate
  process.stdout.write('Authenticating with Microsoft… ');
  await getAccessToken();
  console.log('OK');

  // List files in the OneDrive folder
  process.stdout.write(`Listing files in "${ONEDRIVE_FOLDER}"… `);
  const allFiles = await listFolder(ONEDRIVE_FOLDER);
  console.log(`${allFiles.length} file(s) found`);

  // Filter to transcript files only (skip .md outputs and other types)
  const TRANSCRIPT_EXTS = new Set(['.txt', '.vtt', '.docx']);
  const transcripts = allFiles.filter(f => TRANSCRIPT_EXTS.has(path.extname(f.name).toLowerCase()));

  if (!transcripts.length) {
    console.log('No transcript files (.txt / .vtt / .docx) found — nothing to process.');
    return;
  }

  console.log(`Processing ${transcripts.length} transcript(s):\n`);

  const results = [];

  for (const file of transcripts) {
    const stem    = path.basename(file.name, path.extname(file.name));
    const outName = `${stem}.md`;
    process.stdout.write(`  ${file.name} → ${outName} … `);

    try {
      const bytes = await downloadFile(ONEDRIVE_FOLDER, file.name);
      const text  = await extractText(file.name, bytes);

      if (!text.trim()) throw new Error('File is empty.');

      const data = await extractMeetingNotes(text);
      if (!data) throw new Error('Claude returned no structured data.');

      const md = formatMarkdown(data);
      await uploadFile(ONEDRIVE_FOLDER, outName, md);

      const actionCount = (data.action_items || []).length;
      console.log(`saved (${actionCount} action item${actionCount !== 1 ? 's' : ''})`);
      results.push({ name: file.name, out: outName, ok: true });

    } catch (err) {
      console.log(`FAILED — ${err.message}`);
      results.push({ name: file.name, ok: false, error: err.message });
    }
  }

  // Summary
  const ok   = results.filter(r => r.ok).length;
  const fail = results.length - ok;
  console.log(`\nDone — ${ok} succeeded${fail ? `, ${fail} failed` : ''}.`);
  if (fail) process.exitCode = 1;
}

main().catch(err => { console.error('\nFatal error:', err.message); process.exit(1); });
