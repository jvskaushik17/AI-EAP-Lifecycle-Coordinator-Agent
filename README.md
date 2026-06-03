# AI EAP Lifecycle Coordinator Agent

A local Streamlit web app that evaluates EAP (Early Access Program) customer data and generates a **prioritized action queue** — complete with draft emails, Jira ticket scaffolds, and stakeholder routing — so you spend less time tracking and more time acting.

---

## What It Does

For every customer in your EAP tracking data, the tool:

1. **Detects the next missing lifecycle step** based on 14 configurable business rules
2. **Assigns a priority** (High / Medium / Low) based on the missing step and days since last touchpoint
3. **Generates a draft email or message** ready to copy, personalize, and send
4. **Generates a Jira ticket scaffold** when customer feedback has been received but no ticket exists
5. **Routes each action** to the right stakeholder (AM, SE, PM, or Customer)
6. **Exports** the full action queue as CSV and all drafts as a Markdown file

---

## How to Run

### Prerequisites

- Python 3.9 or later
- pip

### Setup

```bash
# Clone or download this project, then:
cd AI-EAP-Lifecycle-Coordinator-Agent

pip install -r requirements.txt

streamlit run app.py
```

The app opens at `http://localhost:8501` in your browser.

### First run

Click **"Load Sample Data"** to explore the tool immediately with 12 pre-built customers across 4 EAPs. Or upload your own CSV/Excel file.

---

## Input Fields

All fields should be present as column headers in your CSV or Excel file. Extra columns are ignored.

| Field | Type | Notes |
|---|---|---|
| EAP Name | Text | Program identifier (e.g., "DNS Security EAP") |
| Customer Name | Text | Company name |
| Customer Contact | Text | Email address of primary contact |
| Customer Time Zone | Text | e.g., PST, EST, GMT |
| AM Name / AM Email | Text | Account Manager |
| SE Name / SE Email | Text | Solutions Engineer |
| PM Name / PM Email | Text | Product Manager |
| Feature Requested | Text | The EAP feature area |
| Current Stage | Text | Free-text stage label |
| Customer Interest Status | Text | Potential / Agreed / Dropped / Declined |
| Kickoff Scheduled Date | Date | YYYY-MM-DD |
| Kickoff Completed | Yes/No | |
| Registration Completed | Yes/No | |
| Onboarding Scheduled Date | Date | YYYY-MM-DD |
| Onboarding Completed | Yes/No | |
| Build Shared | Yes/No | Did you send the build? |
| Build Downloaded / Installed | Yes/No | Did they install it? |
| Product Actively Testing | Yes/No | Confirmed active use |
| Feedback Session Scheduled Date | Date | YYYY-MM-DD |
| Feedback Received | Yes/No | |
| Jira Ticket Created | Yes/No | |
| Jira Ticket ID | Text | e.g., EAP-2847 |
| Jira Status | Text | In Progress / Resolved / Done / Closed |
| PM/Engineering Last Update Date | Date | YYYY-MM-DD |
| Customer Updated | Yes/No | Notified of Jira progress? |
| Close-Out Email Sent | Yes/No | |
| Survey Sent | Yes/No | CSAT survey sent? |
| Last Customer Touchpoint Date | Date | YYYY-MM-DD |
| Notes / Evidence | Text | Free-text observations, call notes, context |

Yes/No fields accept: `Yes`, `No`, `True`, `False`, `1`, `0`, `Y` (case-insensitive).

Download the **Template CSV** from the app to get a blank file with all columns pre-set.

---

## How the Rules Work

The rule engine evaluates each customer row in priority order. The first matching rule determines the recommended action. Rules fire from highest-priority lifecycle gaps first.

| Rule | Condition | Action |
|---|---|---|
| 1 | Dropped/Declined | No action |
| 2 | Close-out & survey sent | Complete — no action |
| 3 | Customer updated, close-out not sent | Send close-out email + CSAT survey |
| 4 | Jira resolved, customer not updated | Send customer update email |
| 5 | Jira exists, PM update > 14 days ago | PM/Engineering follow-up |
| 6 | Feedback received, no Jira ticket | Create Jira (scaffold generated) |
| 7 | Actively testing, feedback not scheduled | Schedule feedback session |
| 8 | Build installed, testing not confirmed | Active testing check-in |
| 9 | Build shared, not installed | Build installation follow-up |
| 10 | Onboarding done, build not shared | Share build and download instructions |
| 11 | Registration done, onboarding not scheduled | Schedule technical onboarding |
| 12 | Kickoff done, registration not completed | Registration follow-up |
| 13 | Customer agreed, kickoff not scheduled | Schedule kickoff call |
| 14 | Potential + feature identified | AM/SE outreach coordination |
| 15 | Potential, no feature identified | Direct customer recruitment |

### Priority Escalation

- **High**: Critical lifecycle gap (feedback received, Jira resolved without update, PM silent > 21 days) or days since last touchpoint > 10
- **Medium**: Action needed soon — standard follow-up scenarios or touchpoint 4–10 days ago
- **Low**: Newly identified opportunities or advisory items

---

## Draft Types Generated

| Action Type | Draft Generated |
|---|---|
| Direct Customer Recruitment | Invitation email to prospective EAP participant |
| AM/SE Outreach Coordination | Internal email asking AM/SE to validate fit |
| Kickoff Scheduling | Kickoff call invitation with agenda preview |
| Registration Follow-Up | Reminder to complete registration/NDA |
| Onboarding Scheduling | Technical onboarding scheduling request |
| Build Sharing | Build access email with download instructions |
| Build Installation Follow-Up | Check-in on build download/install status |
| Active Testing Confirmation | Testing status check-in offering support |
| Feedback Session Scheduling | Structured feedback session invitation |
| Jira Creation (PM-facing) | Internal email to PM + full Jira ticket scaffold |
| PM/Engineering Follow-Up | Internal follow-up on stale Jira ticket |
| Customer Update | Customer-facing Jira progress notification |
| Close-Out | Thank-you email with CSAT survey link |

---

## Jira Draft Structure

When feedback has been received but no Jira ticket exists, the tool generates:

- Issue type selector (Bug / Enhancement / Feature Request / UX Improvement / Doc Gap)
- Priority with justification field
- Structured description with: Problem Statement, Customer Impact, Expected vs. Actual Behavior, Steps to Reproduce, Supporting Evidence
- Open questions for PM/Engineering review

---

## Exports

| Export | Format | Contents |
|---|---|---|
| Action Queue | CSV | All filtered action rows (no email/Jira text) |
| All Drafts | Markdown | Every draft email and Jira scaffold for filtered rows |
| Template | CSV | Blank file with all required column headers |

---

## Future Integrations (V2+)

| Integration | What It Would Enable |
|---|---|
| **Jira API** | Auto-create tickets from the scaffold; sync Jira status back to the tracker |
| **Gmail / Outlook** | One-click send of draft emails directly from the action queue |
| **Google Calendar / Outlook** | Schedule kickoff, onboarding, and feedback sessions without leaving the tool |
| **Salesforce / HubSpot** | Pull customer and contact data automatically; log EAP activity to the CRM |
| **Slack** | Post action queue summaries to team channels; notify AMs/SEs of their queued actions |
| **Confluence** | Auto-publish EAP status pages and close-out summaries |
| **Google Sheets / Excel Online** | Live sync from the shared EAP tracker spreadsheet |
| **Claude AI API** | Generate hyper-personalized email drafts using customer context, past notes, and CRM history |

---

## Project Structure

```
AI-EAP-Lifecycle-Coordinator-Agent/
├── app.py                  # Main Streamlit application
├── requirements.txt        # Python dependencies
├── sample_eap_data.csv     # 12-customer sample dataset across 4 EAPs
└── README.md               # This file
```

---

## Notes

- No API keys required. No external services required. Works fully offline.
- All draft emails should be reviewed and personalized before sending.
- All Jira scaffolds should be reviewed and completed before ticket creation.
- The tool does not write back to any data source — it is read-only and advisory.
