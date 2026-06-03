# ============================================================
# AI EAP Lifecycle Coordinator Agent
# Version 1.0
# ============================================================

import streamlit as st
import pandas as pd
import io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ─── Page Config ─────────────────────────────────────────────

st.set_page_config(
    page_title="AI EAP Lifecycle Coordinator",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ─────────────────────────────────────────────────────

st.markdown(
    """
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; }

/* Banner */
.banner {
    background: linear-gradient(90deg, #0d1b4b 0%, #1a3a8f 55%, #2756c8 100%);
    color: white;
    padding: 22px 30px 18px;
    border-radius: 12px;
    margin-bottom: 22px;
}
.banner h1 { margin: 0 0 4px; font-size: 1.75em; font-weight: 700; letter-spacing: -0.3px; }
.banner p  { margin: 0; opacity: 0.80; font-size: 0.92em; }

/* Metric boxes */
.metric-box {
    background: white;
    border: 1px solid #e3e8f0;
    border-radius: 10px;
    padding: 16px 12px;
    text-align: center;
    box-shadow: 0 1px 4px rgba(0,0,0,.05);
}
.metric-box .num { font-size: 2.2em; font-weight: 800; line-height: 1.1; }
.metric-box .lbl { font-size: 0.72em; text-transform: uppercase;
                    letter-spacing: .7px; color: #6b7a99; margin-top: 2px; }

/* Priority badges */
.badge {
    display: inline-block; padding: 2px 11px; border-radius: 12px;
    font-size: 0.76em; font-weight: 700; letter-spacing: .4px; margin-right: 4px;
}
.badge-high   { background:#fde8e8; color:#b71c1c; border:1px solid #ef9a9a; }
.badge-medium { background:#fff8e1; color:#e65100; border:1px solid #ffd54f; }
.badge-low    { background:#e8f5e9; color:#1b5e20; border:1px solid #a5d6a7; }

/* Field labels inside expanders */
.field-lbl {
    font-size: 0.7em; text-transform: uppercase; color: #8897b0;
    letter-spacing: .6px; font-weight: 600; margin-bottom: 1px;
}
.field-val { font-size: 0.9em; color: #1a2340; font-weight: 500; }

/* Touchpoint warning colors */
.tw-hot    { color: #b71c1c; font-weight: 700; }
.tw-warm   { color: #e65100; font-weight: 600; }
.tw-ok     { color: #1b5e20; font-weight: 600; }

.divider { border: none; border-top: 1px solid #edf0f7; margin: 10px 0; }

/* Sidebar EAP card */
.eap-card {
    background: #f4f6fb; border-radius: 8px; padding: 8px 12px;
    margin-bottom: 8px; border-left: 3px solid #2756c8;
}
.eap-card .eap-name { font-weight: 700; font-size: 0.88em; color: #0d1b4b; }
.eap-card .eap-meta { font-size: 0.76em; color: #556; margin-top: 2px; }
</style>
""",
    unsafe_allow_html=True,
)

# ─── Constants ────────────────────────────────────────────────

TEMPLATE_COLUMNS = [
    "EAP Name", "Customer Name", "Customer Contact", "Customer Time Zone",
    "AM Name", "AM Email", "SE Name", "SE Email", "PM Name", "PM Email",
    "Feature Requested", "Current Stage", "Customer Interest Status",
    "Kickoff Scheduled Date", "Kickoff Completed", "Registration Completed",
    "Onboarding Scheduled Date", "Onboarding Completed", "Build Shared",
    "Build Downloaded / Installed", "Product Actively Testing",
    "Feedback Session Scheduled Date", "Feedback Received",
    "Jira Ticket Created", "Jira Ticket ID", "Jira Status",
    "PM/Engineering Last Update Date", "Customer Updated",
    "Close-Out Email Sent", "Survey Sent",
    "Last Customer Touchpoint Date", "Notes / Evidence",
]

# ─── Utility Helpers ─────────────────────────────────────────

def safe_str(val, default="—"):
    """Return a clean string or default for NaN/None/empty."""
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except TypeError:
        pass
    s = str(val).strip()
    return s if s and s.lower() not in ("nan", "none", "nat", "") else default


def is_yes(val):
    """Return True if value represents a positive boolean."""
    if val is None:
        return False
    try:
        if pd.isna(val):
            return False
    except TypeError:
        pass
    return str(val).strip().lower() in ("yes", "true", "1", "y")


def parse_date(val):
    """Parse a date value to a Python date object."""
    s = safe_str(val, "")
    if not s or s == "—":
        return None
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return None


def days_since(val):
    """Days elapsed since a date value. Returns 9999 if missing."""
    d = parse_date(val)
    if d is None:
        return 9999
    return max(0, (date.today() - d).days)


def first_name(full_name):
    s = safe_str(full_name, "")
    if not s or s == "—":
        return "there"
    return s.split()[0]


def contact_first_name(contact_str):
    """Best-effort first name from an email or full name string."""
    s = safe_str(contact_str, "")
    if not s or s == "—":
        return "there"
    if "@" in s:
        local = s.split("@")[0]
        parts = local.replace(".", " ").replace("_", " ").replace("-", " ").split()
        return parts[0].capitalize() if parts else "there"
    return s.split()[0]


# ─── Email Draft Generators ───────────────────────────────────

def _sig(am_name, am_email=""):
    parts = [f"\nBest regards,\n{safe_str(am_name, '[Your Name]')}"]
    ae = safe_str(am_email, "")
    if ae and ae != "—":
        parts.append(ae)
    parts.append("Infoblox")
    return "\n".join(parts)


def email_direct_recruitment(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    feature = safe_str(row.get("Feature Requested"), "next-generation capabilities")
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    tz      = safe_str(row.get("Customer Time Zone"), "your time zone")
    fn      = contact_first_name(contact)
    return f"""Subject: Exclusive Invitation — {eap} Early Access Program

Hi {fn},

I hope you're doing well. I'm reaching out because we're launching the **{eap}** and believe {cust} would be an exceptional early access partner.

Based on your team's Infoblox footprint and strategic goals, this program was built for organizations like yours — those who want to influence what ships before general availability.

**What you get as an EAP participant:**
• Early access to {feature} in a pre-GA environment
• Direct input into the product roadmap through structured feedback sessions
• Dedicated support from our Product and Solutions Engineering teams
• Recognition as a Design Partner upon program completion

**What we ask of you:**
• A 30-minute kickoff call to align on goals and timeline
• 4–6 weeks of testing in your environment
• One structured feedback session with our product team

I'd love to schedule a quick 20-minute overview call this week. Would morning or afternoon work best for you ({tz})?

Looking forward to the conversation.
{_sig(am_name, am_eml)}"""


def email_am_se_outreach(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    feature = safe_str(row.get("Feature Requested"), "the requested feature area")
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    se_name = safe_str(row.get("SE Name"))
    notes   = safe_str(row.get("Notes / Evidence"))
    fn_se   = first_name(se_name)
    return f"""Subject: [EAP Candidate] {cust} — {eap} — Coordination Needed

Hi {fn_se},

I wanted to flag **{cust}** as a strong candidate for the **{eap}** and loop you in before we send formal outreach.

**Customer:** {cust}
**Primary Contact:** {contact}
**Feature Interest:** {feature}
**Background:** {notes}

Before I send the recruitment email, I'd like your input on:
1. **Technical fit** — does {cust}'s environment align with what this EAP requires?
2. **Outreach approach** — any nuances I should factor in given your relationship with them?
3. **Availability** — can you join the kickoff call once they agree?

Once you've reviewed, I'll handle the initial customer outreach and coordinate the kickoff.

Can you reply or ping me on Slack with your take? Happy to jump on a quick call this week to align.
{_sig(am_name, am_eml)}"""


def email_kickoff_scheduling(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    se_name = safe_str(row.get("SE Name"))
    feature = safe_str(row.get("Feature Requested"))
    tz      = safe_str(row.get("Customer Time Zone"), "your time zone")
    fn      = contact_first_name(contact)
    return f"""Subject: Kickoff Call — {eap} — Let's Get Scheduled

Hi {fn},

Thank you for agreeing to participate in the **{eap}**! We're excited to have {cust} as an early access partner.

The next step is a **30-minute kickoff call** to:
• Review the EAP objectives and timeline
• Preview the {feature} capabilities you'll be testing
• Set mutual expectations for the testing phase
• Introduce {se_name}, your dedicated Solutions Engineer for this program

**Please share your availability for the coming week** and I'll send a calendar invite immediately. We're happy to work around your schedule ({tz}).

As a starting point, here are a few slots that work on our end:
• [Option A — e.g., Mon June 9, 10:00 AM {tz}]
• [Option B — e.g., Wed June 11, 2:00 PM {tz}]
• [Option C — e.g., Fri June 13, 11:00 AM {tz}]

Just reply with what works, or suggest alternative times.

Looking forward to kicking things off!
{_sig(am_name, am_eml)}"""


def email_registration_followup(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    notes   = safe_str(row.get("Notes / Evidence"))
    fn      = contact_first_name(contact)
    context = f"\n\nFor reference: {notes}" if notes and notes != "—" else ""
    return f"""Subject: Quick Follow-Up: EAP Registration — {eap}

Hi {fn},

Hope the kickoff call got you excited about what's ahead with the **{eap}**! I wanted to follow up on one housekeeping item before we move to the technical phase.

We haven't yet received a completed EAP registration from your side. This typically includes:
• Signing the EAP participation agreement / mutual NDA
• Completing portal access setup
• Submitting the brief onboarding intake form

This is a required step before we can schedule your technical onboarding. It generally takes about 5–10 minutes to complete.

👉 **[Registration Link — insert here]**

If you've hit a snag — procurement approval delays, access issues, or need a different signatory — please let me know and I'll help clear the path right away.{context}

Is there anything I can do to help move this forward?
{_sig(am_name, am_eml)}"""


def email_onboarding_scheduling(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    se_name = safe_str(row.get("SE Name"))
    se_eml  = safe_str(row.get("SE Email"))
    feature = safe_str(row.get("Feature Requested"))
    tz      = safe_str(row.get("Customer Time Zone"), "your time zone")
    fn      = contact_first_name(contact)
    fn_se   = first_name(se_name)
    return f"""Subject: Next Step: Technical Onboarding — {eap}

Hi {fn},

Your EAP registration is confirmed — great news! You're ready to move into the technical onboarding phase of the **{eap}**.

{se_name} ({se_eml}), your dedicated Solutions Engineer for this program, will lead a **60-minute technical onboarding session** that covers:
• Environment prerequisites and compatibility review
• Step-by-step deployment walkthrough for {feature}
• Configuration best practices tailored to your environment
• Defining success criteria and test scenarios for your evaluation

**To schedule, please share 2–3 availability windows** in the coming week ({tz}), and {fn_se} will send a confirmed calendar invite with join details and a pre-read.

We recommend having your infrastructure or network team available for this session.

Looking forward to getting you up and running!
{_sig(am_name, am_eml)}"""


def email_build_sharing(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    se_name = safe_str(row.get("SE Name"))
    se_eml  = safe_str(row.get("SE Email"))
    feature = safe_str(row.get("Feature Requested"))
    fn      = contact_first_name(contact)
    fn_se   = first_name(se_name)
    return f"""Subject: {eap} Build Access — Download Instructions Inside

Hi {fn},

Following a successful onboarding session, we're ready to share the **{eap}** build for {feature}. Here's everything you need to get started.

**Access Details:**
• EAP Portal: [Insert portal URL]
• Credentials: [Insert or confirm existing login]
• Build Version: [e.g., v9.0-EAP-build-4]
• Release Notes: [Insert link]
• Installation Guide: [Insert link]

**Pre-Installation Checklist:**
☐ Test environment meets prerequisites from your onboarding session
☐ Rollback plan in place (snapshot or backup recommended)
☐ Primary technical contact identified for the testing phase
☐ Any firewall/network changes documented and approved

**Support:**
{se_name} ({se_eml}) is your go-to for any technical questions during the EAP. {fn_se} is familiar with your environment from the onboarding session and will prioritize your requests.

Please confirm once you've successfully downloaded the build — and let us know if you hit any issues.
{_sig(am_name, am_eml)}"""


def email_build_followup(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    se_name = safe_str(row.get("SE Name"))
    se_eml  = safe_str(row.get("SE Email"))
    feature = safe_str(row.get("Feature Requested"))
    notes   = safe_str(row.get("Notes / Evidence"))
    fn      = contact_first_name(contact)
    fn_se   = first_name(se_name)
    note_line = f"\n\nFor reference from our last touchpoint: {notes}" if notes and notes != "—" else ""
    return f"""Subject: Checking In — {eap} Build Download

Hi {fn},

I wanted to check in on your progress with the **{eap}** build for {feature}. We shared the download instructions a little while back and want to make sure everything went smoothly.

**Quick status check:**
☐ Downloaded the EAP build?
☐ Installed in your test environment?
☐ Initial smoke test completed?

If you've run into any issues — download errors, compatibility problems, or access questions — {se_name} ({se_eml}) is ready to help. {fn_se} can jump on a quick call or troubleshoot async, whichever you prefer.{note_line}

If priorities have shifted on your end, no problem at all — just let us know and we'll adjust the timeline accordingly. We want this to work around your schedule.
{_sig(am_name, am_eml)}"""


def email_active_testing(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    se_name = safe_str(row.get("SE Name"))
    se_eml  = safe_str(row.get("SE Email"))
    feature = safe_str(row.get("Feature Requested"))
    fn      = contact_first_name(contact)
    fn_se   = first_name(se_name)
    return f"""Subject: EAP Testing Check-In — How's {feature} Going?

Hi {fn},

Hope the {feature} build is up and running! I wanted to check in on how the testing phase is going and make sure you have everything you need.

**A few quick questions:**
1. Are you actively testing {feature} in your environment?
2. Any blockers we can help clear — technical issues, resource constraints, or open questions?
3. Any early observations or impressions worth capturing before the formal feedback session?

{se_name} ({se_eml}) is on standby for any technical deep-dives. {fn_se} can schedule a working session if you'd find that helpful.

We'll also be reaching out soon to schedule your structured feedback session with our product team — your observations during this testing phase will make that conversation much richer.

Don't hesitate to reach out anytime.
{_sig(am_name, am_eml)}"""


def email_feedback_scheduling(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    se_name = safe_str(row.get("SE Name"))
    pm_name = safe_str(row.get("PM Name"))
    feature = safe_str(row.get("Feature Requested"))
    tz      = safe_str(row.get("Customer Time Zone"), "your time zone")
    fn      = contact_first_name(contact)
    fn_pm   = first_name(pm_name)
    return f"""Subject: Let's Schedule Your Feedback Session — {eap}

Hi {fn},

You've reached one of the most impactful milestones in the **{eap}** — your structured feedback session with our Product team!

This is your direct line to influence the roadmap for {feature}. {fn_pm} ({pm_name}), our Product Manager for this area, will join along with {se_name} and me to make sure every piece of feedback is captured accurately and routed to engineering with full context.

**What we'll cover (60 minutes):**
• Overall experience with {feature} — what's working well
• Pain points, gaps, or unexpected behaviors observed
• Feature requests and enhancement ideas
• Use-case scenarios we may not have considered
• Open Q&A with the product team

**Please share 2–3 availability windows** in the next 1–2 weeks ({tz}), and we'll confirm immediately.

Your feedback genuinely shapes what ships. Thank you for the partnership.
{_sig(am_name, am_eml)}"""


def email_jira_creation_pm(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    se_name = safe_str(row.get("SE Name"))
    pm_name = safe_str(row.get("PM Name"))
    feature = safe_str(row.get("Feature Requested"))
    notes   = safe_str(row.get("Notes / Evidence"))
    fn_pm   = first_name(pm_name)
    return f"""Subject: [EAP Action] Jira Ticket Needed — Feedback from {cust} ({eap})

Hi {fn_pm},

We've completed the feedback session with **{cust}** as part of the **{eap}**, and I'm sending over the summary for Jira ticket creation.

**Customer:** {cust}
**EAP Program:** {eap}
**Feature Area:** {feature}

**Feedback Summary:**
{notes}

I've included a detailed Jira draft (see below or in the attached export). Please:
1. Review the draft and fill in any bracketed fields with engineering detail
2. Create the ticket(s) and share the ticket ID(s) with me
3. Provide your initial read on priority and feasibility
4. Flag any open questions you have for the customer — I can gather additional context

{se_name} can provide supplemental technical detail if needed. The customer is engaged and expecting follow-up once a ticket is created.

Thank you for the quick turnaround on this.
{_sig(am_name, am_eml)}"""


def email_pm_followup(row):
    eap       = safe_str(row.get("EAP Name"))
    cust      = safe_str(row.get("Customer Name"))
    am_name   = safe_str(row.get("AM Name"))
    am_eml    = safe_str(row.get("AM Email"))
    pm_name   = safe_str(row.get("PM Name"))
    jira_id   = safe_str(row.get("Jira Ticket ID"))
    jira_st   = safe_str(row.get("Jira Status"))
    ds_pm     = days_since(row.get("PM/Engineering Last Update Date"))
    fn_pm     = first_name(pm_name)
    return f"""Subject: [EAP Follow-Up] Status on {jira_id} — {cust} Requesting Update

Hi {fn_pm},

I wanted to follow up on **{jira_id}** ({jira_st}) related to our **{eap}** participant **{cust}**.

It's been approximately **{ds_pm} days** since the last engineering update, and the customer is actively asking for a status via our AM relationship. I want to proactively keep them in the loop before it becomes a concern.

Could you share:
1. Current status and any engineering progress made
2. Expected timeline for the next milestone or resolution
3. Any blockers, dependencies, or open questions for the customer
4. Whether there's anything I or the SE can provide to support the effort

Once I have an update, I'll draft a customer-facing communication to keep them informed. Happy to schedule a quick sync if that's easier than email.

Appreciate the help — customer satisfaction on this one is important for the EAP relationship.
{_sig(am_name, am_eml)}"""


def email_customer_update(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    jira_id = safe_str(row.get("Jira Ticket ID"))
    jira_st = safe_str(row.get("Jira Status"))
    feature = safe_str(row.get("Feature Requested"))
    notes   = safe_str(row.get("Notes / Evidence"))
    fn      = contact_first_name(contact)
    return f"""Subject: Update on Your Feedback — {eap}

Hi {fn},

I have a positive update to share regarding the feedback you provided during the **{eap}**.

**Jira Ticket {jira_id} — Status: {jira_st}**

Our engineering team has made progress on the items you raised, particularly around {feature}. Here's what I can share:

• [Summary of what was resolved or improved — customize from Jira resolution notes]
• [Specific enhancements or fixes incorporated based on your feedback]
• [GA availability timeline, if applicable]

Your participation directly contributed to this outcome. The feedback you provided gave our product team the clarity and real-world context they needed.

**What comes next:**
We'll be wrapping up the formal EAP phase and reaching out shortly to complete the close-out process. We'll also share a brief experience survey — your honest input helps us improve the program for future participants.

Thank you again, {fn}. This kind of engaged partnership is exactly what makes EAPs valuable for everyone.
{_sig(am_name, am_eml)}"""


def email_closeout(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    am_eml  = safe_str(row.get("AM Email"))
    feature = safe_str(row.get("Feature Requested"))
    fn      = contact_first_name(contact)
    return f"""Subject: Thank You — {eap} Program Close-Out + Quick Survey

Hi {fn},

On behalf of the entire Infoblox team, I want to sincerely thank you and the {cust} team for your outstanding participation in the **{eap}**.

From the kickoff call through active testing and your detailed feedback session — your engagement was exactly the kind of committed partnership that drives better products. The insights you shared around {feature} will have a lasting impact on what we ship and how we prioritize it.

**What happens from here:**
• Your feedback is formally logged and has been routed to Product and Engineering
• Open Jira items will continue to be tracked and you'll receive updates as they progress
• You'll have early visibility when {feature} reaches general availability

**We'd love your candid feedback on the EAP experience:**

👉 **[CSAT Survey — Insert Link Here]** (5 minutes)

Your input helps us make the program better for every future participant.

---

**One more ask:** If {cust} would be open to sharing your experience in a joint case study or customer reference, I'd love to explore that with you. It's completely optional, but it's a meaningful way to amplify your team's work.

Thank you again, {fn}. It's been a genuine pleasure working with you on this.
{_sig(am_name, am_eml)}"""


# ─── Teams & Outlook Integration ─────────────────────────────

def extract_subject(email_text):
    """Pull the Subject: line out of a draft email string."""
    for line in email_text.strip().splitlines():
        if line.lower().startswith("subject:"):
            return line[8:].strip()
    return "EAP Follow-Up"


def _priority_color(priority):
    return {"High": "d32f2f", "Medium": "e65100", "Low": "2e7d32"}.get(priority, "0d1b4b")


def post_to_teams(webhook_url, title, facts, text="", theme_color="0d1b4b"):
    """
    Post a MessageCard to a Teams Incoming Webhook.
    facts: list of {"name": ..., "value": ...} dicts.
    """
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": theme_color,
        "summary": title,
        "sections": [{
            "activityTitle": title,
            "facts": facts,
            "text": text,
            "markdown": True,
        }],
    }
    r = requests.post(webhook_url, json=payload, timeout=10)
    r.raise_for_status()


def post_action_to_teams(webhook_url, action):
    """Post a single action card to Teams."""
    pri = action.get("Priority", "Medium")
    icons = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}
    title = f"{icons.get(pri, '•')} {pri.upper()} — {action['Customer Name']} | {action['EAP Name']}"
    facts = [
        {"name": "Action",      "value": action.get("Recommended Action", "")},
        {"name": "Missing",     "value": action.get("Missing Step", "")},
        {"name": "Stage",       "value": action.get("Current Stage", "")},
        {"name": "Stakeholder", "value": action.get("Stakeholder to Contact", "")},
        {"name": "Owner",       "value": action.get("Suggested Next Owner", "")},
        {"name": "Touchpoint",  "value": f"{action.get('Days Since Touchpoint', 'N/A')} days ago"},
    ]
    evidence = action.get("Evidence", "—")
    post_to_teams(webhook_url, title, facts,
                  text=f"**Evidence:** {evidence}" if evidence != "—" else "",
                  theme_color=_priority_color(pri))


def post_summary_to_teams(webhook_url, df_actions):
    """Post a full action-queue summary card to Teams."""
    high_items  = df_actions[df_actions["Priority"] == "High"]
    med_items   = df_actions[df_actions["Priority"] == "Medium"]
    low_items   = df_actions[df_actions["Priority"] == "Low"]

    def bullet_list(rows):
        parts = [f"{r['Customer Name']} — {r['Recommended Action']}" for _, r in rows.iterrows()]
        return "; ".join(parts) if parts else "None"

    facts = [
        {"name": f"🔴 HIGH ({len(high_items)})",   "value": bullet_list(high_items)},
        {"name": f"🟡 MEDIUM ({len(med_items)})",  "value": bullet_list(med_items)},
        {"name": f"🟢 LOW ({len(low_items)})",     "value": bullet_list(low_items)},
    ]
    title = f"🚀 EAP Action Queue Update — {date.today().strftime('%B %-d, %Y')}"
    post_to_teams(webhook_url, title, facts, theme_color="0d1b4b")


def send_via_outlook(sender_email, sender_password, to_email, subject, body):
    """Send an email via Office 365 SMTP."""
    msg = MIMEMultipart()
    msg["From"]    = sender_email
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP("smtp.office365.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)


# ─── Jira Draft Generator ─────────────────────────────────────

def generate_jira_draft(row):
    eap     = safe_str(row.get("EAP Name"))
    cust    = safe_str(row.get("Customer Name"))
    contact = safe_str(row.get("Customer Contact"))
    am_name = safe_str(row.get("AM Name"))
    se_name = safe_str(row.get("SE Name"))
    pm_name = safe_str(row.get("PM Name"))
    feature = safe_str(row.get("Feature Requested"))
    notes   = safe_str(row.get("Notes / Evidence"))
    fb_date = safe_str(row.get("Feedback Session Scheduled Date"), "[date of feedback session]")

    return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
JIRA TICKET DRAFT
EAP: {eap} | Customer: {cust}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ISSUE TYPE:   [ ] Bug  [ ] Enhancement  [ ] Feature Request  [ ] UX Improvement  [ ] Doc Gap
PROJECT:      [Insert Jira project key — e.g., BLOX, NIOS, DNS]
PRIORITY:     [ ] Critical  [ ] High  [x] Medium  [ ] Low   ← adjust after PM review
LABELS:       EAP, {eap.replace(" ", "-")}, Customer-Feedback, {cust.replace(" ", "-").replace(",", "")}
COMPONENTS:   {feature}
REPORTER:     {am_name}
ASSIGNEE:     [Assign to engineering owner after PM triage]
FIX VERSION:  [Target release — to be set by PM]

──────────────────────────────────────────────────────
SUMMARY (TITLE):
[EAP – {feature}] Customer-Reported Feedback from {cust}: [brief one-line description]

──────────────────────────────────────────────────────
DESCRIPTION:

## Problem Statement
[Summarize the core issue or request in 2–3 sentences.
What is the gap between what the customer expects and what the product currently does?]

Raw EAP Notes for reference:
  {notes}

## Customer Impact
| Field            | Value                        |
|------------------|------------------------------|
| Customer         | {cust}                        |
| Contact          | {contact}                     |
| EAP Program      | {eap}                         |
| Feedback Date    | {fb_date}                     |
| Feature Area     | {feature}                     |

[Describe the operational or business impact to this customer.
How severe is this? How many users or environments are affected?]

## Expected Behavior
[What did the customer expect the product to do in this scenario?]

## Actual Behavior
[What does the product currently do instead? What is the observable gap or failure?]

## Steps to Reproduce (if applicable)
1. [Describe Step 1]
2. [Describe Step 2]
3. [Describe Step 3 — add or remove steps as needed]

Expected result: [What should happen]
Actual result:   [What happens instead]

## Supporting Evidence
- EAP Program: {eap}
- Customer: {cust} | Contact: {contact}
- Account Manager: {am_name}
- Solutions Engineer: {se_name}
- Product Manager: {pm_name}
- Feedback Date: {fb_date}
- Raw Notes: {notes}

## Priority Justification
[Explain why this priority level was selected. Reference:
• Customer tier and strategic value
• Frequency or scope of impact
• Whether this blocks the customer from active testing
• Any competitive implications]

## Open Questions for PM / Engineering
1. Is this enhancement within scope of the current release cycle?
2. What is the estimated level of engineering effort?
3. Are there existing workarounds we can share with the customer in the interim?
4. What additional information, logs, or test cases are needed from the customer?
5. Should this be linked to an existing epic or roadmap initiative?
6. Are other EAP customers or GA customers likely affected by the same issue?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMPORTANT: Complete all bracketed fields and review with PM before creating.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


# ─── Rule Engine ─────────────────────────────────────────────

def evaluate_customer(row):
    """
    Apply lifecycle rules to a single customer row (pandas Series).
    Returns an action dict: priority, missing step, draft email, Jira draft, etc.
    """

    def g(key):
        return row.get(key) if hasattr(row, "get") else getattr(row, key, None)

    # Boolean flags
    kickoff_done     = is_yes(g("Kickoff Completed"))
    reg_done         = is_yes(g("Registration Completed"))
    onboarding_done  = is_yes(g("Onboarding Completed"))
    build_shared     = is_yes(g("Build Shared"))
    build_installed  = is_yes(g("Build Downloaded / Installed"))
    actively_testing = is_yes(g("Product Actively Testing"))
    feedback_recv    = is_yes(g("Feedback Received"))
    jira_created     = is_yes(g("Jira Ticket Created"))
    cust_updated     = is_yes(g("Customer Updated"))
    closeout_sent    = is_yes(g("Close-Out Email Sent"))
    survey_sent      = is_yes(g("Survey Sent"))

    # Scheduled date presence
    kickoff_sched   = safe_str(g("Kickoff Scheduled Date"), "") not in ("", "—")
    onboarding_sched = safe_str(g("Onboarding Scheduled Date"), "") not in ("", "—")
    feedback_sched  = safe_str(g("Feedback Session Scheduled Date"), "") not in ("", "—")

    # Key derived values
    interest     = safe_str(g("Customer Interest Status"), "").strip().lower()
    jira_id      = safe_str(g("Jira Ticket ID"), "")
    jira_status  = safe_str(g("Jira Status"), "").strip().lower()
    feature      = safe_str(g("Feature Requested"), "")
    ds_touch     = days_since(g("Last Customer Touchpoint Date"))
    ds_pm        = days_since(g("PM/Engineering Last Update Date"))

    # Common display values
    eap     = safe_str(g("EAP Name"))
    cust    = safe_str(g("Customer Name"))
    contact = safe_str(g("Customer Contact"))
    am_name = safe_str(g("AM Name"))
    se_name = safe_str(g("SE Name"))
    pm_name = safe_str(g("PM Name"))
    pm_eml  = safe_str(g("PM Email"))
    notes   = safe_str(g("Notes / Evidence"))
    stage   = safe_str(g("Current Stage"))

    touch_display = str(ds_touch) if ds_touch < 9000 else "N/A"

    def result(priority, missing, action, action_type, stakeholder, owner, email_draft, jira=""):
        return {
            "Priority":              priority,
            "EAP Name":              eap,
            "Customer Name":         cust,
            "Customer Contact":      contact,
            "Current Stage":         stage,
            "Missing Step":          missing,
            "Recommended Action":    action,
            "Action Type":           action_type,
            "Stakeholder to Contact": stakeholder,
            "Evidence":              notes,
            "Days Since Touchpoint": touch_display,
            "Draft Email":           email_draft,
            "Jira Draft":            jira,
            "Suggested Next Owner":  owner,
            "AM":                    am_name,
            "SE":                    se_name,
            "PM":                    pm_name,
        }

    # ── Dropped / Inactive ──────────────────────────────────
    if interest in ("dropped", "declined", "not interested", "churned", "no"):
        return result(
            "Low", "Customer Exited EAP",
            "No action — customer has exited or declined the EAP",
            "no_action", "—", "—", "", "",
        )

    # ── Rule 1: Fully Complete ───────────────────────────────
    if closeout_sent and survey_sent:
        return result(
            "Low", "None — EAP Complete",
            "EAP cycle complete — no pending actions",
            "complete", "—", "—", "", "",
        )

    # ── Rule 2: Close-Out Email + Survey ────────────────────
    if cust_updated and (not closeout_sent or not survey_sent):
        priority = "High" if ds_touch > 10 else "Medium"
        return result(
            priority,
            "Close-Out Email and CSAT Survey Not Sent",
            "Send close-out thank-you email and CSAT survey link",
            "closeout", contact, am_name,
            email_closeout(row),
        )

    # ── Rule 3: Customer update after Jira resolved ─────────
    if jira_created and jira_status in ("resolved", "done", "closed", "complete", "released"):
        if not cust_updated:
            return result(
                "High",
                "Customer Not Notified of Jira Resolution",
                "Send customer update — Jira ticket has been resolved",
                "customer_update", contact, pm_name or am_name,
                email_customer_update(row),
            )

    # ── Rule 4: PM/Engineering follow-up (stale Jira) ───────
    if jira_created and jira_id not in ("—", "") and ds_pm > 14:
        priority = "High" if ds_pm > 21 else "Medium"
        return result(
            priority,
            f"No PM/Engineering Update in {ds_pm} Days",
            f"Follow up with PM/Engineering on Jira ticket {jira_id}",
            "pm_followup", pm_eml or pm_name, pm_name,
            email_pm_followup(row),
        )

    # ── Rule 5: Jira creation from feedback ─────────────────
    if feedback_recv and not jira_created:
        return result(
            "High",
            "Jira Ticket Not Created from Customer Feedback",
            "Create Jira ticket(s) capturing customer feedback",
            "jira_creation", pm_eml or pm_name, pm_name,
            email_jira_creation_pm(row),
            generate_jira_draft(row),
        )

    # ── Rule 6: Feedback session scheduling ─────────────────
    if actively_testing and not feedback_recv and not feedback_sched:
        priority = "High" if ds_touch > 7 else "Medium"
        return result(
            priority,
            "Feedback Session Not Scheduled",
            "Schedule structured feedback session with customer and PM",
            "feedback_scheduling", contact, se_name or am_name,
            email_feedback_scheduling(row),
        )

    # ── Rule 7: Active testing confirmation ─────────────────
    if build_installed and not actively_testing:
        priority = "High" if ds_touch > 10 else "Medium"
        return result(
            priority,
            "Active Testing Not Confirmed",
            "Confirm customer is actively testing and offer support",
            "active_testing", contact, se_name or am_name,
            email_active_testing(row),
        )

    # ── Rule 8: Build installation follow-up ────────────────
    if build_shared and not build_installed:
        priority = "High" if ds_touch > 10 else "Medium"
        return result(
            priority,
            "Build Not Downloaded / Installed",
            "Follow up on build download and installation status",
            "build_followup", contact, se_name or am_name,
            email_build_followup(row),
        )

    # ── Rule 9: Share build post-onboarding ─────────────────
    if onboarding_done and not build_shared:
        return result(
            "High",
            "Build Not Shared with Customer",
            "Share EAP build and download/access instructions",
            "build_sharing", contact, se_name,
            email_build_sharing(row),
        )

    # ── Rule 10: Onboarding scheduling ──────────────────────
    if reg_done and not onboarding_done and not onboarding_sched:
        priority = "High" if ds_touch > 7 else "Medium"
        return result(
            priority,
            "Technical Onboarding Not Scheduled",
            "Schedule technical onboarding session with customer",
            "onboarding_scheduling", contact, se_name,
            email_onboarding_scheduling(row),
        )

    # ── Rule 11: Registration follow-up ─────────────────────
    if kickoff_done and not reg_done:
        priority = "High" if ds_touch > 7 else "Medium"
        return result(
            priority,
            "EAP Registration Not Completed",
            "Follow up on EAP registration and portal access",
            "registration_followup", contact, am_name,
            email_registration_followup(row),
        )

    # ── Rule 12: Kickoff scheduling ─────────────────────────
    if interest in ("agreed", "confirmed", "yes", "active") and not kickoff_done and not kickoff_sched:
        priority = "High" if ds_touch > 7 else "Medium"
        return result(
            priority,
            "Kickoff Call Not Scheduled",
            "Schedule EAP kickoff call with customer",
            "kickoff_scheduling", contact, am_name,
            email_kickoff_scheduling(row),
        )

    # ── Rule 13: AM/SE outreach (feature specified) ──────────
    if interest in ("potential", "") and feature not in ("—", ""):
        priority = "High" if ds_touch > 14 else "Medium"
        if ds_touch <= 3:
            priority = "Low"
        return result(
            priority,
            "AM/SE Coordination and Outreach Not Initiated",
            "Coordinate with AM/SE to validate fit and send EAP outreach",
            "am_se_outreach", safe_str(g("AM Email")) or am_name, am_name,
            email_am_se_outreach(row),
        )

    # ── Rule 14: Direct recruitment (no feature yet) ────────
    if interest in ("potential", ""):
        return result(
            "Medium",
            "Customer Outreach Not Sent",
            "Send direct EAP recruitment email to customer",
            "direct_recruitment", contact, am_name,
            email_direct_recruitment(row),
        )

    # ── Fallback ─────────────────────────────────────────────
    return result(
        "Low",
        "Manual Review Required",
        "Review customer record — stage may need updating",
        "review", am_name, am_name, "",
    )


# ─── Markdown Export ─────────────────────────────────────────

def build_drafts_markdown(df_actions):
    lines = [
        "# EAP Lifecycle Coordinator — Action Queue & Drafts Export",
        f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        "---",
        "",
    ]
    for _, row in df_actions.iterrows():
        if row.get("Action Type", "") in ("complete", "no_action"):
            continue
        pri = row.get("Priority", "")
        lines += [
            f"## [{pri.upper()}] {row.get('Customer Name', '')}  ·  {row.get('EAP Name', '')}",
            f"**Recommended Action:** {row.get('Recommended Action', '')}  ",
            f"**Stage:** {row.get('Current Stage', '')}  ",
            f"**Missing Step:** {row.get('Missing Step', '')}  ",
            f"**Stakeholder:** {row.get('Stakeholder to Contact', '')}  |  "
            f"**Suggested Owner:** {row.get('Suggested Next Owner', '')}  ",
            f"**Days Since Touchpoint:** {row.get('Days Since Touchpoint', 'N/A')}",
            "",
        ]
        evidence = row.get("Evidence", "—")
        if evidence and evidence != "—":
            lines += [f"**Evidence / Notes:**  ", f"> {evidence}", ""]

        draft = row.get("Draft Email", "")
        if draft and draft.strip():
            lines += ["### ✉️ Draft Email", "```", draft.strip(), "```", ""]

        jira = row.get("Jira Draft", "")
        if jira and jira.strip():
            lines += ["### 🎫 Jira Draft", "```", jira.strip(), "```", ""]

        lines += ["---", ""]

    return "\n".join(lines)


# ─── Template CSV Download ────────────────────────────────────

def build_template_csv():
    return pd.DataFrame(columns=TEMPLATE_COLUMNS).to_csv(index=False)


# ─── Sample Data Loader ───────────────────────────────────────

def load_sample_data():
    try:
        return pd.read_csv("sample_eap_data.csv")
    except FileNotFoundError:
        st.error("sample_eap_data.csv not found. Please ensure it is in the same directory as app.py.")
        return pd.DataFrame(columns=TEMPLATE_COLUMNS)


# ─── Priority sort helper ─────────────────────────────────────

_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


# ─── Main App ─────────────────────────────────────────────────

def main():

    # ── Banner ──────────────────────────────────────────────
    st.markdown(
        """
        <div class="banner">
          <h1>🚀 AI EAP Lifecycle Coordinator</h1>
          <p>Prioritized action queue · Draft emails · Jira scaffolds · Multi-program visibility</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Data Loading ────────────────────────────────────────
    col_up, col_mid, col_samp, col_tmpl = st.columns([3, 0.4, 1.8, 1.8])

    with col_up:
        uploaded = st.file_uploader(
            "Upload EAP tracking file (CSV or Excel)",
            type=["csv", "xlsx", "xls"],
            help="Must match the template column structure.",
            label_visibility="visible",
        )

    with col_mid:
        st.markdown("<div style='padding-top:28px;text-align:center;color:#888;font-size:.9em;'>— or —</div>",
                    unsafe_allow_html=True)

    with col_samp:
        st.markdown("<div style='padding-top:20px;'></div>", unsafe_allow_html=True)
        use_sample = st.button("📋 Load Sample Data", use_container_width=True, type="secondary")

    with col_tmpl:
        st.markdown("<div style='padding-top:20px;'></div>", unsafe_allow_html=True)
        st.download_button(
            "⬇️ Download Template CSV",
            data=build_template_csv(),
            file_name="eap_template.csv",
            mime="text/csv",
            use_container_width=True,
            help="Download a blank CSV with all required columns.",
        )

    # Manage session state for sample toggle
    if use_sample:
        st.session_state["using_sample"] = True
    if uploaded is not None:
        st.session_state["using_sample"] = False

    df = None
    source_label = ""

    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
            df.columns = [str(c).strip() for c in df.columns]
            source_label = f"📁 {uploaded.name}"
        except Exception as exc:
            st.error(f"Could not read file: {exc}")
            return

    elif st.session_state.get("using_sample"):
        df = load_sample_data()
        source_label = "📋 Sample EAP Data — 12 customers across 4 EAPs"

    # ── Welcome screen ──────────────────────────────────────
    if df is None:
        st.markdown("---")
        st.markdown(
            """
            ### How this tool works

            1. **Upload** your EAP tracking CSV/Excel **or** click **Load Sample Data** to explore with built-in data.
            2. The coordinator evaluates every customer row against lifecycle rules and generates a **prioritized action queue**.
            3. For each action you'll see: what's missing, who to contact, why it matters, and a ready-to-customize **draft email**.
            4. Where feedback has been received but no Jira exists, a full **Jira ticket scaffold** is generated.
            5. **Export** the action queue as CSV or download all drafts as a Markdown file.

            ---
            **Supported stages:** Recruitment → Kickoff → Registration → Onboarding → Build Installation →
            Active Testing → Feedback Collection → Jira Creation → PM Follow-Up → Customer Update → Close-Out
            """
        )
        return

    # ── Data Preview ────────────────────────────────────────
    with st.expander(f"📊 Data Preview — {source_label} ({len(df)} rows)", expanded=False):
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{len(df)} rows × {len(df.columns)} columns")

    # ── Generate Action Queue ────────────────────────────────
    with st.spinner("Evaluating lifecycle rules…"):
        actions = [evaluate_customer(row) for _, row in df.iterrows()]
    actions_df = pd.DataFrame(actions)

    # ── Sidebar ──────────────────────────────────────────────
    with st.sidebar:
        st.header("🔎 Filters")

        eap_opts = sorted(actions_df["EAP Name"].unique().tolist())
        sel_eaps = st.multiselect("EAP Program", eap_opts, default=eap_opts)

        pri_opts = ["High", "Medium", "Low"]
        sel_pris = st.multiselect("Priority", pri_opts, default=pri_opts)

        act_opts = sorted(actions_df["Recommended Action"].unique().tolist())
        sel_acts = st.multiselect("Recommended Action", act_opts, default=act_opts)

        cust_opts = sorted(actions_df["Customer Name"].unique().tolist())
        sel_custs = st.multiselect("Customer", cust_opts, default=cust_opts)

        st.divider()

        # Per-EAP overview
        st.header("📈 EAP Overview")
        for eap_name in eap_opts:
            sub = actions_df[actions_df["EAP Name"] == eap_name]
            high_n = (sub["Priority"] == "High").sum()
            med_n  = (sub["Priority"] == "Medium").sum()
            st.markdown(
                f"""<div class="eap-card">
                    <div class="eap-name">{eap_name}</div>
                    <div class="eap-meta">
                        {len(sub)} customers &nbsp;·&nbsp;
                        🔴 {high_n} high &nbsp;·&nbsp;
                        🟡 {med_n} medium
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )

        st.divider()

        # ── Integrations ──────────────────────────────────────
        st.header("🔗 Integrations")

        with st.expander("Microsoft Teams", expanded=False):
            st.caption("Paste an **Incoming Webhook URL** from any Teams channel.")
            teams_url = st.text_input(
                "Webhook URL",
                value=st.session_state.get("teams_webhook", ""),
                type="password",
                placeholder="https://outlook.office.com/webhook/...",
                key="teams_url_input",
            )
            if teams_url:
                st.session_state["teams_webhook"] = teams_url

            if st.button("📣 Post Full Queue Summary to Teams",
                         disabled=not st.session_state.get("teams_webhook"),
                         use_container_width=True):
                try:
                    post_summary_to_teams(st.session_state["teams_webhook"], filtered)
                    st.success("✅ Summary posted to Teams!")
                except Exception as exc:
                    st.error(f"Teams error: {exc}")

            if not HAS_REQUESTS:
                st.warning("Install `requests` to enable Teams posting.")

        with st.expander("Outlook / Office 365", expanded=False):
            st.caption("Uses Office 365 SMTP. Requires an **App Password** "
                       "(not your regular login) if MFA is enabled — "
                       "[generate one here](https://account.microsoft.com/security).")
            ol_email = st.text_input(
                "Your Office 365 email",
                value=st.session_state.get("outlook_email", ""),
                placeholder="you@company.com",
                key="ol_email_input",
            )
            ol_pass = st.text_input(
                "App Password",
                type="password",
                placeholder="xxxx xxxx xxxx xxxx",
                key="ol_pass_input",
            )
            if ol_email:
                st.session_state["outlook_email"] = ol_email
            if ol_pass:
                st.session_state["outlook_password"] = ol_pass

            if st.button("🔑 Test Outlook Connection",
                         disabled=not (st.session_state.get("outlook_email")
                                       and st.session_state.get("outlook_password")),
                         use_container_width=True):
                try:
                    with smtplib.SMTP("smtp.office365.com", 587) as srv:
                        srv.ehlo()
                        srv.starttls()
                        srv.login(st.session_state["outlook_email"],
                                  st.session_state["outlook_password"])
                    st.success("✅ Outlook connection successful!")
                except Exception as exc:
                    st.error(f"Outlook error: {exc}")

        st.divider()
        st.caption("AI EAP Lifecycle Coordinator v1.0")
        st.caption("Recommendations require human review before action.")

    # ── Apply Filters ─────────────────────────────────────────
    filtered = actions_df[
        actions_df["EAP Name"].isin(sel_eaps)
        & actions_df["Priority"].isin(sel_pris)
        & actions_df["Recommended Action"].isin(sel_acts)
        & actions_df["Customer Name"].isin(sel_custs)
    ].copy()

    # Sort: priority then days-since-touchpoint (most overdue first)
    filtered["_p"] = filtered["Priority"].map(_PRIORITY_RANK).fillna(3)
    filtered["_d"] = pd.to_numeric(filtered["Days Since Touchpoint"], errors="coerce").fillna(0)
    filtered = filtered.sort_values(["_p", "_d"], ascending=[True, False]).reset_index(drop=True)

    # ── Summary Metrics ───────────────────────────────────────
    total_n  = len(filtered)
    high_n   = (filtered["Priority"] == "High").sum()
    medium_n = (filtered["Priority"] == "Medium").sum()
    low_n    = (filtered["Priority"] == "Low").sum()

    mc1, mc2, mc3, mc4 = st.columns(4)
    for col, num, lbl, color in [
        (mc1, total_n,  "Total Actions",   "#1a237e"),
        (mc2, high_n,   "High Priority",   "#b71c1c"),
        (mc3, medium_n, "Medium Priority", "#e65100"),
        (mc4, low_n,    "Low Priority",    "#1b5e20"),
    ]:
        with col:
            st.markdown(
                f'<div class="metric-box">'
                f'<div class="num" style="color:{color};">{num}</div>'
                f'<div class="lbl">{lbl}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)

    # ── Export Buttons ────────────────────────────────────────
    exp1, exp2, _ = st.columns([2.2, 2.2, 3.6])
    export_cols = [c for c in filtered.columns
                   if c not in ("Draft Email", "Jira Draft", "_p", "_d")]
    with exp1:
        st.download_button(
            "⬇️ Export Action Queue (CSV)",
            data=filtered[export_cols].to_csv(index=False),
            file_name=f"eap_action_queue_{date.today()}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with exp2:
        st.download_button(
            "⬇️ Export All Drafts (Markdown)",
            data=build_drafts_markdown(filtered),
            file_name=f"eap_drafts_{date.today()}.md",
            mime="text/markdown",
            use_container_width=True,
        )

    # ── Action Queue ──────────────────────────────────────────
    st.markdown(f"### 🗂️ Action Queue — {total_n} item{'s' if total_n != 1 else ''}")

    if total_n == 0:
        st.info("No actions match the current filter selections.")
        return

    for idx, action in filtered.iterrows():
        priority   = action["Priority"]
        cust       = action["Customer Name"]
        eap_lbl    = action["EAP Name"]
        rec_action = action["Recommended Action"]
        missing    = action["Missing Step"]
        stakeholder = action["Stakeholder to Contact"]
        owner      = action["Suggested Next Owner"]
        stage      = action["Current Stage"]
        evidence   = action["Evidence"]
        contact    = action["Customer Contact"]
        am         = action.get("AM", "—")
        se         = action.get("SE", "—")
        pm         = action.get("PM", "—")
        draft_em   = action.get("Draft Email", "") or ""
        draft_ji   = action.get("Jira Draft", "") or ""
        ds_t       = action["Days Since Touchpoint"]
        action_type = action.get("Action Type", "")

        badge_cls = f"badge-{priority.lower()}"
        is_done   = action_type in ("complete", "no_action")

        exp_label = f"{priority.upper()}  |  {cust}  ({eap_lbl})  —  {rec_action}"

        with st.expander(exp_label, expanded=(priority == "High" and not is_done)):

            # Header line
            st.markdown(
                f'<span class="badge {badge_cls}">{priority.upper()}</span> '
                f'<strong style="font-size:1.05em;">{cust}</strong> '
                f'<span style="color:#8897b0;"> · {eap_lbl}</span>',
                unsafe_allow_html=True,
            )

            # Four-column info grid
            gi1, gi2, gi3, gi4 = st.columns(4)
            for col_w, lbl, val in [
                (gi1, "Current Stage",        stage),
                (gi2, "Missing Step",          missing),
                (gi3, "Stakeholder",           stakeholder),
                (gi4, "Suggested Next Owner",  owner),
            ]:
                with col_w:
                    st.markdown(
                        f'<div class="field-lbl">{lbl}</div>'
                        f'<div class="field-val">{val}</div>',
                        unsafe_allow_html=True,
                    )

            # Touchpoint staleness indicator
            if isinstance(ds_t, str) and ds_t.isdigit():
                ds_int = int(ds_t)
            elif isinstance(ds_t, (int, float)) and ds_t < 9000:
                ds_int = int(ds_t)
            else:
                ds_int = None

            if ds_int is not None:
                if ds_int > 14:
                    tw_cls, tw_icon = "tw-hot",  "🔴"
                elif ds_int > 7:
                    tw_cls, tw_icon = "tw-warm", "🟡"
                else:
                    tw_cls, tw_icon = "tw-ok",   "🟢"
                st.markdown(
                    f'{tw_icon} <span class="{tw_cls}">Last customer touchpoint: {ds_int} day{"s" if ds_int != 1 else ""} ago</span>',
                    unsafe_allow_html=True,
                )

            # Team strip
            st.markdown(
                f'<div style="font-size:0.78em;color:#8897b0;margin-top:4px;">'
                f'AM: {am} &nbsp;·&nbsp; SE: {se} &nbsp;·&nbsp; PM: {pm} &nbsp;·&nbsp; Contact: {contact}'
                f'</div>',
                unsafe_allow_html=True,
            )

            st.markdown('<hr class="divider">', unsafe_allow_html=True)

            # Tabs
            has_jira = bool(draft_ji.strip())
            if has_jira:
                tab_ev, tab_em, tab_ji = st.tabs(
                    ["📋 Evidence & Notes", "✉️ Draft Email", "🎫 Jira Draft"]
                )
            else:
                tab_ev, tab_em = st.tabs(["📋 Evidence & Notes", "✉️ Draft Email"])
                tab_ji = None

            with tab_ev:
                st.markdown("**Evidence / Notes:**")
                if evidence and evidence != "—":
                    st.info(evidence)
                else:
                    st.caption("No evidence or notes recorded for this customer.")
                st.markdown("**Recommended Action:**")
                if is_done:
                    st.success(rec_action)
                else:
                    st.warning(rec_action)

            with tab_em:
                if draft_em.strip():
                    st.caption("✏️  Review, personalize the bracketed fields, and send.")
                    st.text_area(
                        "draft_email",
                        value=draft_em,
                        height=380,
                        key=f"em_{idx}_{cust}",
                        label_visibility="collapsed",
                    )

                    # ── Send / Share buttons ──────────────────
                    btn1, btn2, btn3 = st.columns([2.5, 2.5, 3])

                    with btn1:
                        ol_ready = bool(st.session_state.get("outlook_email")
                                        and st.session_state.get("outlook_password"))
                        if st.button("📧 Send via Outlook",
                                     key=f"ol_send_{idx}",
                                     disabled=not ol_ready,
                                     use_container_width=True,
                                     help="Configure Outlook in the sidebar first." if not ol_ready else ""):
                            to_addr = stakeholder if "@" in stakeholder else contact
                            subject = extract_subject(draft_em)
                            body    = "\n".join(
                                line for line in draft_em.splitlines()
                                if not line.lower().startswith("subject:")
                            ).strip()
                            try:
                                send_via_outlook(
                                    st.session_state["outlook_email"],
                                    st.session_state["outlook_password"],
                                    to_addr, subject, body,
                                )
                                st.success(f"✅ Sent to {to_addr}")
                            except Exception as exc:
                                st.error(f"Send failed: {exc}")

                    with btn2:
                        tm_ready = bool(HAS_REQUESTS
                                        and st.session_state.get("teams_webhook"))
                        if st.button("💬 Post to Teams",
                                     key=f"tm_act_{idx}",
                                     disabled=not tm_ready,
                                     use_container_width=True,
                                     help="Configure Teams webhook in the sidebar first." if not tm_ready else ""):
                            try:
                                post_action_to_teams(
                                    st.session_state["teams_webhook"],
                                    action.to_dict(),
                                )
                                st.success("✅ Posted to Teams!")
                            except Exception as exc:
                                st.error(f"Teams error: {exc}")

                    with btn3:
                        st.caption("Buttons activate after configuring integrations in the sidebar.")
                else:
                    st.caption("No draft email generated for this action type.")

            if tab_ji is not None:
                with tab_ji:
                    st.caption("✏️  Complete all bracketed fields before creating the Jira ticket.")
                    st.text_area(
                        "draft_jira",
                        value=draft_ji,
                        height=520,
                        key=f"ji_{idx}_{cust}",
                        label_visibility="collapsed",
                    )

    st.markdown("---")
    st.caption(
        "AI EAP Lifecycle Coordinator v1.0 · Built with Streamlit and Pandas · "
        "All AI-generated recommendations should be reviewed by a human before action is taken."
    )


if __name__ == "__main__":
    main()
