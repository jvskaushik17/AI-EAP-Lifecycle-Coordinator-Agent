# Agent Setup Guide

## What you need

| Thing | Where to get it | Time |
|---|---|---|
| Anthropic API key | console.anthropic.com → API Keys | 2 min |
| Azure AD App (Tenant ID + Client ID) | portal.azure.com (free) | 10 min |

---

## Step 1 — Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. API Keys → Create Key
3. Copy the key (starts with `sk-ant-`)
4. Paste it into the agent dashboard sidebar

---

## Step 2 — Azure AD App Registration (for Outlook + Teams)

### 2a. Create the app

1. Go to [portal.azure.com](https://portal.azure.com) → sign in with your work Microsoft account
2. Search for **"App registrations"** → **New registration**
3. Name: `EAP Agent` (or anything you like)
4. Supported account types: **"Accounts in this organizational directory only"**
5. Click **Register**
6. Copy the **Application (client) ID** — this is your `CLIENT_ID`
7. Copy the **Directory (tenant) ID** — this is your `TENANT_ID`

### 2b. Add API permissions

1. Left menu → **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**
2. Add these permissions:
   - `Mail.Read` — read Outlook emails
   - `Mail.Send` — send emails via Graph (optional — SMTP also works)
   - `Chat.Read` — read Teams chat messages
   - `ChannelMessage.Read.All` — read Teams channel messages
   - `User.Read` — basic profile (already there)
   - `offline_access` — keep you logged in between restarts
3. Click **Add permissions**
4. If you're an admin: click **Grant admin consent** (required for Chat.Read and ChannelMessage.Read.All)
   - If you're not an admin: ask your IT admin to grant consent for the app

### 2c. Enable public client flow (required for device-code auth)

1. Left menu → **Authentication** → **Add a platform** → **Mobile and desktop applications**
2. Check `https://login.microsoftonline.com/common/oauth2/nativeclient`
3. Under **Advanced settings** → set **Allow public client flows** to **Yes**
4. Click **Save**

---

## Step 3 — Run the agent

### Option A: Dashboard only (manual run)

```bash
# Install dependencies
pip install -r requirements.txt

# Start the dashboard
streamlit run agent_app.py
```

1. Open `http://localhost:8501`
2. Paste API key in sidebar
3. Enter Tenant ID + Client ID → click Connect
4. Authenticate in the browser when prompted
5. Click **Run Agent Now** to trigger a scan

### Option B: Automated polling (recommended)

```bash
# Terminal 1 — dashboard
streamlit run agent_app.py

# Terminal 2 — background scanner (runs every 15 min)
ANTHROPIC_API_KEY=sk-ant-xxx \
MS_TENANT_ID=your-tenant-id \
MS_CLIENT_ID=your-client-id \
POLL_INTERVAL_MIN=15 \
python run_agent.py
```

Or use a `.env` file:
```
ANTHROPIC_API_KEY=sk-ant-xxx
MS_TENANT_ID=your-tenant-id
MS_CLIENT_ID=your-client-id
POLL_INTERVAL_MIN=15
HOURS_BACK=2
```

---

## Step 4 — Seed your EAP data

1. In the dashboard sidebar → **Import CSV** → upload your EAP spreadsheet
2. Or click **Load sample data** to explore with 12 sample customers

The agent will match signals from emails/Teams to customers by name and company.

---

## No Azure account? Use paste-and-analyze

If you can't set up Azure right now, the agent still works without it:

1. Copy any email or Teams message
2. Open the **Paste email or Teams message** section on the dashboard
3. Paste the text → click Analyze
4. The agent reads it, updates EAP state, and queues any needed actions

This works immediately with just an Anthropic API key.

---

## What the agent looks for

The agent is trained to detect these signals in your emails and Teams messages:

| Signal in communication | Agent action |
|---|---|
| "we installed the build" | Updates Build Downloaded = Yes |
| "we're actively testing" | Updates Product Actively Testing = Yes |
| "we'd like to share feedback" | Queues feedback session scheduling email |
| "any update on our Jira ticket?" | Flags HIGH priority PM follow-up |
| "we're having trouble with X" | Flags support needed, queues SE email |
| "we're not going to participate" | Updates status = Dropped, flags AM |
| "the build is working great" | Logs insight, may queue feedback scheduling |
| [no reply in 10+ days] | Flags overdue follow-up |

---

## Privacy note

- Emails and Teams messages are sent to the Anthropic API for analysis
- Only message text is sent — no attachments
- Anthropic's API does not train on your data by default (API usage policy)
- Tokens (Outlook/Teams credentials) are stored locally in `.graph_token.json`
- All EAP state is stored locally in `eap_agent.db` (SQLite)
