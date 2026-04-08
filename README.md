<div align="center">

# Job Application Assistant

**An AI-powered, multi-agent job application pipeline built with CrewAI and FastAPI.**

Automatically picks your best resume, tailors it to the job description, and writes a matching cover letter — all with a human checkpoint before anything goes out.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![CrewAI](https://img.shields.io/badge/CrewAI-multi--agent-f97316?style=flat-square)](https://crewai.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)](LICENSE)

*Built by [Prasad Watane](https://linkedin.com/in/prasad-watane-4836a8226) — M.Sc. Applied Data Science & Analytics, SRH Heidelberg*

</div>

---

## What it does

Most job application tools stop at resume builders. This one runs a full pipeline:

1. **Picks** the best resume from your collection for a given job
2. **Tailors** that resume with exact JD keywords and ATS-optimised formatting
3. **Writes** a personalised cover letter from the tailored resume
4. **Waits** for you to review and approve before anything is used
5. **Watches** your inbox for replies and notifies you when a company responds

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      You (Input)                        │
│         text · PDF · CSV · URL job description          │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                   FastAPI Server                        │
│              POST /ingest/text|pdf|csv|url              │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Command Engine (injected into all agents)  │
│   ATS defaults (always on) + your custom instructions   │
│   keywords · bullet rules · section order · formatting  │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   ┌─────────┐  ┌─────────┐  ┌─────────────┐
   │ Agent 1 │  │ Agent 2 │  │   Agent 3   │
   │ Resume  │─▶│ Resume  │─▶│Cover Letter │
   │ Picker  │  │ Tailor  │  │  Manager   │
   └─────────┘  └─────────┘  └─────────────┘
   Scores all   Rewrites      Writes letter
   resumes,     chosen        from tailored
   picks best   resume with   resume only
   fit 0-100    JD keywords
        │            │              │
        └────────────┴──────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                  ATS Optimizer                          │
│   keyword coverage score · bullet quality check        │
│   missing keywords flagged · overall score 0-100       │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│               Job Log (SQLite)                          │
│            status: pending_review                       │
└────┬───────────────────────────────────┬────────────────┘
     │                                   │
     ▼                                   ▼
┌─────────────┐                   ┌──────────────┐
│   You       │                   │ Notification │
│  /ui review │                   │ Push + Email │
│  approve or │                   │   (instant)  │
│  reject     │                   └──────────────┘
└─────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│             Email Monitor (background)                  │
│   IMAP polling every 5 min · Claude classification     │
│   interview_invite · rejection · follow_up · ack       │
│   Job log updated · you notified instantly             │
└─────────────────────────────────────────────────────────┘
```

---

## Microservices breakdown

| Service | File | What it does |
|---------|------|-------------|
| Job Ingestion | `services/job_ingestion.py` | Normalises text, PDF, CSV, URL into one format |
| Command Engine | `services/command_engine.py` | ATS rules + your custom instructions, injected into every agent |
| ATS Optimizer | `services/ats_optimizer.py` | Scores keyword coverage and bullet quality post-generation |
| Key Vault | `services/key_vault.py` | AES-128 encrypted storage for LLM API keys |
| Email Monitor | `services/email_monitor.py` | IMAP polling + Claude email classification |
| Email Sender | `services/email_sender.py` | SMTP outbound notifications |
| Job Log | `infrastructure/job_log/job_log.py` | SQLite persistence, full status lifecycle |

---

## Application status lifecycle

```
pending_review  →  approved  →  submitted
     │                               │
     └──  rejected              interview_invited
                                rejected_by_company
                                awaiting_response
```

Every application starts at `pending_review`. Nothing moves forward without your explicit approval.

---

## Features

### Three-agent CrewAI pipeline
- **Resume Picker** — scores all your resumes against the JD, picks the best fit, explains its reasoning
- **Resume Tailor** — rewrites the chosen resume with exact JD keywords, ATS formatting, quantified bullets
- **Cover Letter Manager** — writes a personalised letter from the tailored resume (only if fit score > 83)

### Human-in-the-loop review
Every application goes to `pending_review`. You review the reasoning, fit score, tailored resume, and cover letter before approving. The system never sends anything automatically.

### Multi-provider LLM with encrypted key vault
Supports Groq (free), Anthropic, OpenAI, and Gemini. Keys are AES-128 encrypted before storage — never logged or returned by the API. Switch providers with one click in the UI.

| Provider | Default model | Cost | Get key |
|----------|--------------|------|---------|
| Groq ⚡ | llama-3.3-70b-versatile | Free | [console.groq.com](https://console.groq.com) |
| Anthropic 🧠 | claude-sonnet-4-5 | Paid | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI 🤖 | gpt-4o-mini | Paid | [platform.openai.com](https://platform.openai.com) |
| Gemini ✨ | gemini-2.0-flash | Free tier | [aistudio.google.com](https://aistudio.google.com) |

### ATS optimizer
Post-processes every output before saving:
- Keyword coverage score vs the job description
- Bullet point quality check (action verbs, quantification, no pronouns)
- Missing keywords flagged with suggestions
- Overall ATS score 0–100

### Command engine
Two-layer prompt system injected into every agent call:
- **ATS defaults** — always active: keyword rules, bullet format, section order, cover letter structure
- **Your commands** — add free-text instructions that stack on top. Global or per-job scope.

```bash
POST /commands  {"command": "highlight Python and FastAPI prominently", "type": "content"}
POST /commands  {"command": "formal British English, no contractions", "type": "style"}
POST /commands  {"command": "omit Professional Summary section", "type": "override"}
```

### Email intelligence
IMAP background monitor polls your inbox every 5 minutes. When a company replies, Claude classifies it:
- `interview_invite` — updates status, fires push + email notification immediately
- `rejection` — updates status, logs it
- `follow_up` — flags as awaiting response
- `acknowledgement` — no status change (auto-replies)

### Full dashboard UI
Served from FastAPI at `/ui` — no CORS issues, no separate frontend server.

- Dashboard with stats, recent applications, system status
- Submit jobs as text, PDF, URL, or bulk CSV/XLSX
- Review queue with approve/reject/redo
- Application history with filter by status
- Resume manager — upload, preview extracted text
- Cover letter editor — open, edit, save & regenerate PDF
- AI Agents tab — add/switch providers, keys shown masked only
- Command engine — add/remove custom instructions, view ATS defaults

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/prasadwatane/job-application-assistant.git
cd job-application-assistant
pip install -e .
```

### 2. Configure

```bash
cp sample.env .env
# Edit .env — set FILES_AUTH_KEY to any strong secret
```

### 3. Add your resumes

```bash
mkdir resumes
cp ~/path/to/your-resume.pdf resumes/
```

Update `config.yml`:
```yaml
resume_directory: /absolute/path/to/resumes
```

### 4. Start Docker services (Milvus for semantic search)

```bash
docker compose up -d
```

### 5. Start the API

```bash
uvicorn src.job_application_assistant.main:app --host 0.0.0.0 --port 8000 --reload
```

Open **http://localhost:8000/ui**

### 6. Add an LLM provider

Go to **AI Agents** tab → paste your Groq key → click **Save & set active**.

Groq is free — get a key at [console.groq.com](https://console.groq.com) in 30 seconds.

---

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/ingest/text` | Submit job as plain text |
| `POST` | `/ingest/pdf` | Upload a PDF job posting |
| `POST` | `/ingest/csv` | Bulk upload CSV/XLSX of jobs |
| `POST` | `/ingest/url` | Fetch from a URL |
| `GET` | `/review/pending` | Applications awaiting your review |
| `GET` | `/review/log` | Full history (filter by status) |
| `POST` | `/review/{id}/approve` | Approve an application |
| `POST` | `/review/{id}/reject` | Reject with a reason |
| `POST` | `/review/{id}/mark-submitted` | Record that you sent it |
| `GET` | `/commands` | List active custom commands |
| `POST` | `/commands` | Add a command |
| `POST` | `/commands/redo/{id}` | Re-run with a new instruction |
| `GET` | `/settings/providers` | List LLM providers (keys masked) |
| `POST` | `/settings/providers` | Add/update a provider key |
| `GET` | `/documents/resumes` | List resumes |
| `GET` | `/documents/cover-letters` | List cover letters |
| `PUT` | `/documents/cover-letters/{name}` | Edit cover letter text + regenerate PDF |
| `GET` | `/email-monitor/status` | Email monitor status |
| `POST` | `/email-monitor/poll` | Trigger manual inbox poll |

All endpoints require `?key=FILES_AUTH_KEY`. Full interactive docs at `/docs`.

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FILES_AUTH_KEY` | ✅ | Protects all API endpoints. Change from default. |
| `VAULT_MASTER_KEY` | Optional | AES key for LLM key encryption (defaults to FILES_AUTH_KEY) |
| `EMAIL_ADDRESS` | Email monitor | Your Gmail address |
| `EMAIL_PASSWORD` | Email monitor | Gmail App Password (not account password) |
| `IMAP_HOST` | Email monitor | Default: `imap.gmail.com` |
| `SMTP_HOST` | Notifications | Default: `smtp.gmail.com` |
| `SMTP_USER` | Notifications | Your email address |
| `SMTP_PASSWORD` | Notifications | Gmail App Password |
| `NOTIFY_TO` | Notifications | Where to send alerts |
| `PUSHOVER_USER` | Optional | Pushover push notifications |
| `PUSHOVER_TOKEN` | Optional | Pushover app token |
| `EMAIL_POLL_INTERVAL_SECONDS` | Optional | Default: `300` (5 min) |

LLM API keys are **not** stored in `.env` — they are added via the UI or `POST /settings/providers` and encrypted before storage.

---

## Project structure

```
job-application-assistant/
├── src/job_application_assistant/
│   ├── crew.py                        # 3-agent CrewAI pipeline
│   ├── main.py                        # FastAPI app, router registration
│   ├── schemas.py                     # Pydantic models
│   ├── config/
│   │   ├── agents.yaml                # Agent roles, goals, backstories
│   │   └── tasks.yaml                 # Task descriptions and rules
│   ├── services/
│   │   ├── command_engine.py          # ATS defaults + user commands
│   │   ├── ats_optimizer.py           # Keyword scoring, bullet quality
│   │   ├── job_ingestion.py           # Text, PDF, CSV, URL normalisation
│   │   ├── key_vault.py               # AES-128 encrypted LLM key storage
│   │   ├── email_monitor.py           # IMAP polling + Claude classification
│   │   └── email_sender.py            # SMTP outbound notifications
│   ├── routers/
│   │   ├── ingestion_router.py        # /ingest endpoints
│   │   ├── review_router.py           # /review HITL endpoints
│   │   ├── command_router.py          # /commands endpoints
│   │   ├── settings_router.py         # /settings provider endpoints
│   │   └── documents_router.py        # /documents resume + cover letter
│   ├── infrastructure/job_log/
│   │   └── job_log.py                 # SQLite persistence layer
│   └── tools/
│       ├── cover_letter_saver.py      # CrewAI tool — saves cover letter PDF
│       ├── resume_saver.py            # CrewAI tool — saves tailored resume PDF
│       └── push_tool.py               # CrewAI tool — Pushover notifications
├── dashboard.html                     # Full dashboard UI (served at /ui)
├── config.yml                         # Resume directory, thresholds
├── sample.env                         # Environment variable template
├── docker-compose.yml                 # Milvus + MinIO + Attu
└── pyproject.toml                     # Dependencies
```

---

## Gmail setup for email monitoring

1. Enable 2-Step Verification on your Google account
2. Go to **myaccount.google.com** → Security → App Passwords
3. Create an app password for "Mail" → "Other" → name it "Job Assistant"
4. Copy the 16-character password → set as `EMAIL_PASSWORD` and `SMTP_PASSWORD` in `.env`
5. In Gmail settings → Forwarding and POP/IMAP → Enable IMAP

---

## Responsible AI

This project was designed from the ground up with responsible use as a core requirement, not an afterthought. Here is exactly how it protects candidates, companies, and the integrity of the hiring process.

### The human is always in control

The most important design decision in this system is the mandatory human checkpoint. No application, resume, or cover letter reaches a company without your explicit approval. The pipeline generates, scores, and drafts — but it stops there. You review every document, read the agent's reasoning, and decide whether to approve, reject, or request a redo. The system does not and cannot apply on your behalf.

```
AI generates → you review → you approve → you apply
                    ↑
         nothing bypasses this step
```

This is not a convenience feature. It is a hard architectural constraint. The `/review/{id}/approve` endpoint must be called manually. There is no auto-approve timer, no "apply if score > threshold" shortcut, no background job that fires off applications while you sleep.

### It does not spam companies

Because every application requires your explicit action, the system cannot fire off bulk applications to hundreds of companies. You submit one job at a time, review one application at a time, and decide what to send. Companies only ever hear from you when you choose to reach out — the same as if you had done everything manually, just with better-prepared documents.

### The resume tailor cannot fabricate experience

The Resume Tailor agent operates under a strict constraint baked into its system prompt and backstory: **it can only restructure and reword genuine content from your existing resume.** It cannot add jobs you did not have, skills you do not possess, or achievements that are not in your original documents.

What it can do:
- Mirror exact keywords from the job description to match ATS scanners
- Reorder bullet points to lead with the most relevant achievements
- Rewrite bullets to be more concise, quantified, and action-verb led
- Surface skills you actually have but buried in the wrong section
- Add a "Currently developing:" line for skills you are genuinely working on

What it cannot do:
- Invent a company, role, or date range
- Add a skill not mentioned anywhere in your resume
- Claim a certification you do not hold
- Fabricate a metric or achievement

This matters because misrepresenting experience in a job application is dishonest and harmful — to the employer, to other candidates, and ultimately to the candidate themselves. The system is designed to make your real experience shine, not to manufacture fake experience.

### All AI reasoning is transparent and logged

Every decision the agents make is stored with full reasoning in the job log. When the Resume Picker chooses a particular resume, it explains exactly why — citing specific keyword overlaps and experience gaps. When it gives a fit score, it shows its working. You can read this reasoning before approving, and it is stored permanently so you can audit any application after the fact.

This transparency means you are never blindly trusting the AI. You can disagree with its reasoning, reject its output, and provide a redo instruction. The agent then tries again with your feedback incorporated.

### The fit score prevents wasted applications

The system only generates a tailored resume and cover letter if the fit score exceeds 83 out of 100. This threshold exists to prevent you from applying to roles where your experience is genuinely not a match. Applying to jobs you are significantly underqualified for wastes your time, the recruiter's time, and contributes to the noise that makes hiring harder for everyone. The honest scoring — which agents are explicitly told not to inflate — is a feature, not a limitation.

### Email monitoring is read-only intelligence

The email monitor polls your inbox and classifies replies from companies. It does not send any emails on your behalf. It does not auto-reply. It does not forward your emails anywhere. It reads, classifies, and updates your local job log so you can track where each application stands. Any response to a company is always composed and sent by you.

### No data leaves your machine

The entire system runs locally. Your resumes, cover letters, job descriptions, and application history live in a SQLite database on your own computer. LLM calls go directly from your machine to the provider you configured (Groq, Anthropic, etc.) — no intermediary service, no data aggregation, no analytics. Your job search is private.

---

## Contributing

Pull requests welcome. Open an issue first for major changes.

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">
Built by <a href="https://linkedin.com/in/prasad-watane-4836a8226">Prasad Watane</a> · M.Sc. Applied Data Science & Analytics · SRH Hochschule Heidelberg
</div>
