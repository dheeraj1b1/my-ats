# My ATS — AI-Powered Job Screening & Application Tracker

> A personal automation system for QA / SDET job hunting. Combines a **headless LinkedIn scraping pipeline** with **two-tier Gemini AI filtering** to find relevant jobs, and a **Streamlit dashboard** for manual resume scanning, application tracking, and AI-powered resume tailoring.

---

## What This Project Demonstrates

| Skill | Details |
|---|---|
| **AI Integration** | Google Gemini API for ATS scoring, gap analysis, and resume tailoring |
| **Automation Workflow** | Headless LinkedIn scraping → keyword filter → AI filter → Airtable push |
| **PDF/Document Parsing** | `pdfplumber` for PDF resume reading, `python-docx` for DOCX parsing and in-place editing |
| **API Integration** | Airtable REST API (full CRUD), Supabase Python client (database + storage) |
| **Deduplication Logic** | URL-based and normalized company+role dedup to prevent duplicate job entries |
| **Filtering & Scoring** | Two-tier scoring: local keyword matching + Gemini deep scan with configurable thresholds |
| **CI/CD Automation** | GitHub Actions runs the scout pipeline every 12 hours via cron |
| **Unit Testing** | pytest test files for scout logic and resume tailoring (no network calls required) |
| **Practical QA Use Case** | Purpose-built tool that solves a real SDET job search problem end-to-end |

---

## Tech Stack

| Component | Technology | Role |
|---|---|---|
| **AI / LLM** | Google Gemini API (Flash models) | ATS scoring, gap analysis, resume tailoring |
| **Dashboard** | Streamlit | 5-page web UI for manual workflows |
| **Job Scraping** | requests + BeautifulSoup4 | LinkedIn public page HTML parsing |
| **Database** | Supabase (PostgreSQL) | Rejection cache (`tier1_rejections` table) |
| **Storage** | Supabase Storage | Document vault (PDF/DOCX files, 50 MB cap) |
| **Tracking** | Airtable | Application tracking (full CRUD via REST API) |
| **PDF Parsing** | pdfplumber | Resume PDF text extraction |
| **DOCX Editing** | python-docx | In-place resume section rewriting |
| **CI/CD** | GitHub Actions | Scheduled scout pipeline (every 12h) |
| **Containerization** | Docker | `job_curator/` sub-module is Docker-ready |
| **Testing** | pytest | Unit tests for scout logic and tailor engine |

---

## System Architecture

```
┌─────────────────────────────────────────────────┐
│         GITHUB ACTIONS (every 12 hours)            │
│  scout.py → LinkedIn Scrape (3 cities, 60 jobs)   │
│           → Dedup (URL + Company/Role)             │
│           → Tier 1: Keyword scoring (≥30%)         │
│           → Tier 2: Gemini AI deep scan (≥80%)     │
│           → PUSH → Airtable (Not Applied)          │
│           → CACHE REJECTIONS → Supabase            │
└─────────────────────────────────────────────────┘
                        ↓ feeds
┌─────────────────────────────────────────────────┐
│       STREAMLIT DASHBOARD (app.py, 5 pages)        │
│                                                     │
│  │ Command Center  │  Manual JD scan + Gemini      │
│  │ Airtable Tracker│  Grid + Kanban + CRUD sync     │
│  │ Supabase Viewer │  Rejection cache explorer      │
│  │ Resume Studio   │  AI DOCX tailoring + download  │
│  │ Document Vault  │  PDF upload to Supabase Store  │
└─────────────────────────────────────────────────┘
          ↓                 ↓                  ↓
   ┌──────────┐   ┌──────────┐   ┌─────────────┐
   │Gemini API│   │ Airtable │   │  Supabase    │
   │(Google)  │   │Tracker   │   │DB + Storage │
   └──────────┘   └──────────┘   └─────────────┘
```

---

## Project Structure

```
my-ats/
├── app.py                        # Streamlit dashboard (5-page UI)
├── scout.py                      # Automated LinkedIn scout pipeline
├── tailor.py                     # Resume tailoring backend logic (DOCX)
├── prompts.py                    # Shared ATS scoring prompt template
├── tailor_prompt.py              # Resume tailoring prompt template
├── resume.txt                    # Master resume in plain text (for scout)
├── requirements.txt              # Streamlit app dependencies
├── scout_requirements.txt        # Lightweight scout-only dependencies
├── test_scout_refactor.py        # Unit tests for scout filtering logic
├── test_tailor.py                # Unit tests for DOCX parsing and tailoring
├── ARCHITECTURE.md              # Full technical architecture document
├── .github/
│   └── workflows/
│       └── scout.yml             # GitHub Actions: runs scout.py every 12 hours
└── job_curator/                  # Standalone Docker-ready modular version
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── config.py             # Job search configuration
        ├── dedup.py              # Deduplication logic
        ├── parser.py             # Job listing HTML parser
        ├── rules.py              # Blacklist, seniority, domain filters
        ├── refiner.py            # AI refinement and scoring logic
        ├── excel_writer.py       # Excel output writer
        ├── experience_parser.py  # Resume experience section parser
        └── main.py               # Entry point
```

---

## Key Features

### Scout Pipeline (`scout.py` — runs via GitHub Actions)

- **LinkedIn scraping:** Searches for SDET/QA/Automation jobs across 3 cities (Bangalore, Chennai, Hyderabad), up to 60 jobs per city
- **Dual deduplication:** URL-based dedup + normalized company/role matching to prevent reprocessing
- **Blacklist filters:** Recruiter agency blacklist, seniority filter (junior/intern/fresher), domain mismatch filter (gaming, real estate, etc.)
- **Two-tier AI scoring:**
  - Tier 1 — Local keyword match: Primary skills (Java, Selenium, REST Assured, TestNG) and secondary skills (Python, Playwright). Threshold: ≥30%
  - Tier 2 — Gemini AI deep analysis: Detailed JD vs resume comparison. Threshold: ≥80%
- **Rejection caching:** Failed jobs are cached in Supabase for 36 hours to skip re-evaluation on next run
- **Airtable push:** Qualified jobs are logged with company, role, match score, apply link, and AI gap analysis

### Streamlit Dashboard (`app.py` — 5 pages)

| Page | Description |
|---|---|
| **Command Center** | Paste a JD + upload resume → Gemini scores and analyzes → log to Airtable |
| **Airtable Tracker** | Grid + Kanban view, inline editing, filter/sort, full two-way Airtable sync |
| **Supabase Viewer** | Explore rejection cache, filter/sort, delete old entries |
| **Resume Studio** | Select a job → upload `.docx` → Gemini tailors resume in-place → download or save to vault |
| **Document Vault** | Upload PDF resumes to Supabase Storage, manage files with size tracking |

### Resume Tailoring Engine (`tailor.py`)

- Parses DOCX resume into 4 sections via a state machine: Summary, Experience, Projects, Skills
- Sends section content + JD to Gemini with a structured prompt
- Applies AI output **in-place** to the DOCX, preserving fonts, sizes, bullet styles, and bold formatting
- Model cascade on rate limits: `gemini-flash-lite` → `gemini-flash` (progressive fallback)

---

## Two-Tier Scoring Design

The scoring design minimizes Gemini API costs:

```
LinkedIn Jobs
     ↓
[Tier 1: Local Keyword Match]
  │ Primary skills: Java, Selenium, REST Assured, TestNG, API Testing... (10 pts each)
  │ Secondary skills: Python, Playwright, Appium... (2 pts each)
  │ Threshold: ≥30% to advance
  │
  ├──── < 30% → Cache in Supabase (skip Gemini call)
  └──── ≥30% → Continue to Tier 2
           ↓
    [Tier 2: Gemini AI Deep Scan]
      Threshold: ≥80% to qualify
      │
      ├─── < 80% → Cache rejection in Supabase
      └─── ≥80% → Push to Airtable with score + gap analysis
```

---

## How to Run

### Streamlit Dashboard (Local)

```bash
pip install -r requirements.txt

# Add credentials to .streamlit/secrets.toml:
# [secrets]
# GEMINI_API_KEY = "..."
# AIRTABLE_TOKEN = "..."
# SUPABASE_URL = "..."
# SUPABASE_KEY = "..."

streamlit run app.py
```

### Scout Pipeline (Local)

```bash
pip install -r scout_requirements.txt

export GEMINI_API_KEY="..."
export AIRTABLE_TOKEN="..."
export SUPABASE_URL="..."
export SUPABASE_KEY="..."

python scout.py
```

### Unit Tests (No API keys needed)

```bash
pip install pytest python-docx
pytest test_scout_refactor.py test_tailor.py -v
```

### Docker (job_curator module)

```bash
cd job_curator
docker build -t job-curator .
docker run job-curator
```

---

## CI/CD

The scout pipeline runs automatically on GitHub Actions (`scout.yml`):

- **Trigger:** Cron every 12 hours (`0 */12 * * *`) + manual `workflow_dispatch`
- **Environment:** `ubuntu-latest`, Python 3.12
- **Secrets:** `GEMINI_API_KEY`, `AIRTABLE_TOKEN`, `SUPABASE_URL`, `SUPABASE_KEY` injected via GitHub repository secrets
- **Stale rejection purge:** Supabase records older than 36 hours are purged at the start of each run to stay current

---

## Testing

| Test File | Scope | Notes |
|---|---|---|
| `test_scout_refactor.py` | Company normalization, blacklist, seniority filter, domain filter | Pure logic — no network or API calls |
| `test_tailor.py` | DOCX parsing, markdown replacement, AI section parsing, prompt building | No Gemini calls needed |

Both test files are **dry-run only** — safe to run without any credentials.

---

## External Services

| Service | Purpose |
|---|---|
| **Google Gemini API** | ATS scoring, gap analysis, resume tailoring |
| **Airtable** | Application tracking (CRUD via REST API) |
| **Supabase (DB)** | Rejection cache (`tier1_rejections` table, ~335 rows) |
| **Supabase (Storage)** | Document vault (`tailored_resumes` bucket, 50 MB cap) |
| **LinkedIn** | Job listings scraped via public HTML (no auth required) |

---

## Future Improvements

- [ ] **Cover letter generator** — Gemini-powered, tailored per JD
- [ ] **Multi-resume support** — maintain versions by domain (Java/Python/API), auto-select best match
- [ ] **Naukri / Indeed scraping** — expand beyond LinkedIn
- [ ] **Interview prep module** — auto-generate likely questions per JD
- [ ] **Application status automation** — auto-update Airtable status after download
- [ ] **Analytics dashboard** — conversion funnel (applied → interview → offer) with charts
- [ ] **Consolidate Gemini SDKs** — standardize on one SDK across scout and app modules
- [ ] **Checkpoint/resume for scout** — save pipeline progress so it can recover from mid-run crashes
- [ ] **Docker deployment** — containerize the Streamlit app for cloud hosting

---

## GitHub Topics

`gemini-api` `python` `streamlit` `airtable` `supabase` `job-search-automation` `resume-matching` `ai-tools` `qa-automation` `sdet` `web-scraping` `github-actions` `nlp` `pdf-parsing`

---

> For full architecture details, data models, and design decisions, see [ARCHITECTURE.md](./ARCHITECTURE.md).
