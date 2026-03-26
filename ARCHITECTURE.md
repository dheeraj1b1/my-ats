# My ATS — Architecture & Project Knowledge Document

> **Purpose:** This document captures the complete architecture, implementation details, data flows, and future roadmap of the **My ATS** project so that any AI model or new developer can quickly understand the system and continue building on it.
>
> **Last Updated:** 2026-03-25

---

## 1. Project Overview

**My ATS** is a personal **Applicant Tracking System** built for an SDET / QA Automation Engineer job search. It combines:

1. **Automated Job Scouting** — A headless pipeline that scrapes LinkedIn, scores jobs using two tiers of filtering (local keyword + Gemini AI), and pushes qualified matches to Airtable.
2. **Streamlit Dashboard** — A multi-page web app for manually scanning resumes against JDs, tracking applications in Airtable, viewing Supabase rejection data, tailoring resumes with AI, and managing documents in a cloud vault.

The system runs on **Python 3.12** and uses **Gemini AI** (Google) for all LLM-powered features.

---

## 2. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    GITHUB ACTIONS (every 12h)                     │
│  scout.py → LinkedIn Scrape → Tier1 (Local) → Tier2 (Gemini AI) │
│             ↓ rejected → Supabase cache                          │
│             ↓ qualified → Airtable (Not Applied)                 │
└──────────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────────┐
│                STREAMLIT DASHBOARD (app.py)                       │
│  ┌───────────────┬──────────────┬────────────────┐               │
│  │ Command Center│ Airtable     │ Supabase Viewer│               │
│  │ (Manual Scan) │ Tracker      │ (Rejections)   │               │
│  ├───────────────┼──────────────┼────────────────┤               │
│  │ Resume Studio │ Document     │                │               │
│  │ (AI Tailor)   │ Vault (PDF)  │                │               │
│  └───────────────┴──────────────┴────────────────┘               │
└──────────────────────────────────────────────────────────────────┘
                ↕                      ↕                ↕
         ┌──────────┐          ┌──────────────┐   ┌───────────┐
         │ Gemini AI│          │   Airtable   │   │ Supabase  │
         │ (Google) │          │ Applications │   │ DB + Store│
         └──────────┘          └──────────────┘   └───────────┘
```

---

## 3. File Structure

```
my-ats/
├── app.py                    # Streamlit dashboard (1031 lines, 5 pages)
├── scout.py                  # Automated LinkedIn scout pipeline (621 lines)
├── tailor.py                 # Resume tailoring backend logic (350 lines)
├── prompts.py                # Shared ATS scoring prompt for scout.py
├── tailor_prompt.py          # Resume tailoring prompt template
├── resume.txt                # Master resume in plain text (for scout.py)
│
├── requirements.txt          # Streamlit app dependencies (pip freeze)
├── scout_requirements.txt    # Scout pipeline dependencies (lightweight)
│
├── .streamlit/
│   └── secrets.toml          # Local secrets (Gemini, Airtable, Supabase, Apify)
│
├── .github/workflows/
│   └── scout.yml             # GitHub Actions: runs scout.py every 12 hours
│
├── test_scout_refactor.py    # Unit tests for scout.py logic (no network)
├── test_tailor.py            # Unit tests for tailor.py (DOCX parsing, AI parsing)
├── test.py                   # Quick Gemini model listing utility
├── test_delete.py            # Supabase storage deletion test utility
│
├── .gitignore                # Ignores venv/, .streamlit/, __pycache__/, test.py
└── venv/                     # Local Python virtual environment
```

---

## 4. External Services & Credentials

| Service | Purpose | Credential Keys |
|---------|---------|----------------|
| **Google Gemini AI** | LLM for ATS scoring, resume tailoring | `GEMINI_API_KEY` |
| **Airtable** | Application tracking database (CRUD) | `AIRTABLE_TOKEN`, Base ID: `appABPMwKgXkr8Rgn` |
| **Supabase (DB)** | Rejection cache (`tier1_rejections` table) | `SUPABASE_URL`, `SUPABASE_KEY` |
| **Supabase (Storage)** | Document vault (`tailored_resumes` bucket) | Same keys as above |
| **Apify** | Token exists in secrets but **NOT currently used** in code | `APIFY_TOKEN` |
| **LinkedIn** | Job listings scraped via public HTML (no login) | None (public scraping) |

### Credentials Location
- **Local development:** `.streamlit/secrets.toml` (gitignored)
- **CI/CD (GitHub Actions):** GitHub repository secrets → injected as environment variables

---

## 5. Data Models

### 5.1 Airtable — `Applications` Table

| Field | Type | Description |
|-------|------|-------------|
| `Company` | Text | Company name |
| `Role` | Text | Job title |
| `Match Score` | Number | ATS match percentage (0–100) |
| `Status` | Single Select | `Not Applied` · `Applied` · `Interviewing` · `Rejected` |
| `Applied Date` | Text (ISO) | IST timestamp when logged |
| `JD Description` | Long Text | Full job description text |
| `Resume Name` | Text | Resume version used |
| `Apply Link` | URL | LinkedIn job posting URL |
| `Missing Details` | Long Text | AI-generated gap analysis (from scout) |

### 5.2 Supabase — `tier1_rejections` Table

| Column | Type | Details |
|--------|------|---------|
| `id` | bigint | Auto-incrementing primary key |
| `rejected_at` | timestamptz | Default: `now()` |
| `job_url` | text | Unique, nullable — LinkedIn URL |
| `company_name` | text | Nullable |
| `job_title` | text | Nullable |

- **RLS:** Enabled
- **Current rows:** ~335
- **Purge policy:** Records older than 36 hours are auto-purged at the start of each scout run

### 5.3 Supabase Storage — `tailored_resumes` Bucket

- Stores PDF and DOCX resume files
- 50 MB global storage limit (enforced in app.py)
- Public URLs available for download
- Supports upsert (overwrite duplicates)

---

## 6. Component Deep Dives

### 6.1 Scout Pipeline (`scout.py`)

**What it does:** Automatically finds relevant QA/SDET jobs and pushes matches to Airtable.

**Runs via:** GitHub Actions every 12 hours + manual trigger

**Pipeline Steps:**

```
1. Purge stale rejections from Supabase (>36h old)
2. Read resume.txt
3. Scrape LinkedIn (3 cities × 60 jobs max = 180 jobs)
4. Fetch existing Airtable records for dedup
5. Fetch Supabase rejection cache for dedup
6. For each job:
   a. Dedup check (URL + Company/Role) against Airtable & Supabase
   b. Recruiter/agency blacklist check
   c. Seniority filter (reject junior/intern/fresher)
   d. Domain mismatch filter (reject gaming, real estate, etc.)
   e. Tier 1: Local keyword scoring (≥30% to advance)
   f. Tier 2: Gemini AI deep scan (≥80% to push)
   g. Push qualified jobs to Airtable with "Not Applied" status
   h. Cache rejected jobs in Supabase
```

**Key Configuration:**

| Parameter | Value | Description |
|-----------|-------|-------------|
| `SEARCH_KEYWORDS` | SDET, Automation Engineer, QA, etc. | LinkedIn search query |
| `TARGET_CITIES` | Bangalore, Chennai, Hyderabad | Indian metro cities |
| `JOBS_PER_CITY` | 60 | Max jobs scraped per city |
| `TIER1_THRESHOLD` | 30% | Local keyword pass mark |
| `TIER2_THRESHOLD` | 80% | Gemini AI pass mark |
| `GEMINI_SLEEP` | 90s | Pause before each Gemini call |
| `GEMINI_RETRY_SLEEP` | 180s | Pause on 429 before retry |
| Gemini Model | `gemini-3.1-flash-lite-preview` | Used for Tier 2 scoring |

**Skill Scoring (Tier 1):**
- **Primary skills** (10 pts each): Java, Selenium, REST Assured, TestNG, API Testing, CI/CD, Jenkins, Microservices, JMeter, SQL
- **Secondary skills** (2 pts each): Python, Playwright, Appium
- Score = sum of matched skills found in **both** resume AND JD (capped at 100)

**Company Normalization:** Strips suffixes like "Pvt Ltd", "Technologies", "Solutions", "India", etc. to enable fuzzy dedup matching.

### 6.2 Streamlit Dashboard (`app.py`)

**5 Pages:**

#### Page 1: 🏠 Command Center
- Manual paste-a-JD + upload-resume workflow
- Calls Gemini (`gemini-2.5-flash`) to score and analyze
- Extracts company, role, match score from structured AI output
- Button to log the result to Airtable

#### Page 2: 📊 Airtable Tracker
- **Grid View:** `st.data_editor` with in-line editing, row add/delete, save to Airtable
- **Kanban View:** Status-grouped cards (Not Applied → Applied → Interviewing → Rejected)
- Filters: Search by company/role, filter by status
- Sorting: Date, Company name, Score (ascending/descending)
- Full two-way sync: edits, creates, deletes are all PATCHed/POSTed/DELETed to Airtable

#### Page 3: 🗄️ Supabase Viewer
- Reads `tier1_rejections` table via Supabase Python client
- Filter by company, title, reason
- Sort by date or company name
- Interactive data editor with deletion capability

#### Page 4: ✂️ Resume Studio
- Fetches "Not Applied" jobs from Airtable
- User selects a job and uploads a `.docx` resume
- Parses resume into 4 sections: Summary, Experience, Projects, Skills
- Builds a prompt using `TAILOR_PROMPT` and sends to Gemini
- **Model Cascade** (429 fallback): `gemini-3.1-flash-lite-preview` → `gemini-3-flash-preview` → `gemini-2.5-flash`
- Applies AI output **in-place** to the DOCX (preserves formatting, handles markdown bold)
- Offers download + optional auto-save to Supabase Vault
- **Daily rate limit:** 540 API calls per day (tracked in `st.session_state`)

#### Page 5: ☁️ Document Vault
- Upload finalized PDFs to Supabase Storage (`tailored_resumes` bucket)
- 50 MB global limit with usage bar
- Sort by date, filename, size
- Interactive data editor with file deletion
- Public URL links for each document

### 6.3 Prompt Templates

| File | Used By | Template Variables |
|------|---------|-------------------|
| `prompts.py` → `MASTER_PROMPT` | `scout.py` (Tier 2) + `app.py` (Command Center) | `{jd_text}`, `{resume_text}` |
| `tailor_prompt.py` → `TAILOR_PROMPT` | `tailor.py` (Resume Studio) | `{jd_text}`, `{summary_text}`, `{experience_bullets}`, `{projects_bullets}`, `{skills_text}` |

### 6.4 Resume Tailoring Engine (`tailor.py`)

**Core Logic:**
1. **DOCX Parsing:** State machine that identifies sections by header text (EXPERIENCE, PROJECTS, SKILLS, PROFESSIONAL SUMMARY, etc.)
2. **AI Response Parsing:** Splits AI output into 4 sections by markers (SUMMARY_TEXT, EXPERIENCE_BULLETS, PROJECTS_BULLETS, SKILLS_TEXT)
3. **In-Place Replacement:** Clears existing DOCX runs, creates new runs with markdown bold (`**text**`) converted to native Word bold formatting, preserves original font name/size and bullet prefixes
4. **Rate Limiting:** Daily counter in Streamlit session state, resets at midnight

---

## 7. CI/CD — GitHub Actions

### `scout.yml`
- **Trigger:** Cron every 12 hours (`0 */12 * * *`) + manual `workflow_dispatch`
- **Runner:** `ubuntu-latest`
- **Python:** 3.12
- **Dependencies:** `scout_requirements.txt` (requests, beautifulsoup4, google-genai, supabase)
- **Secrets:** `GEMINI_API_KEY`, `AIRTABLE_TOKEN`, `SUPABASE_URL`, `SUPABASE_KEY`

---

## 8. Testing

| Test File | Scope | Dependencies |
|-----------|-------|-------------|
| `test_scout_refactor.py` | `normalize_company`, recruiter blacklist, seniority filter, domain reject | None (pure logic, no imports from scout.py) |
| `test_tailor.py` | DOCX parsing, markdown replacement, AI section parsing, prompt builder, end-to-end tailor pipeline | `python-docx`, `tailor.py` |

**Test runner:** pytest

Both test files are **dry-run only** — no network calls, no API keys needed.

---

## 9. Known Limitations & Technical Debt

| # | Issue | Details |
|---|-------|---------|
| 1 | **LinkedIn scraping is fragile** | Relies on CSS class patterns (`base-card`, `base-search-card__title`). LinkedIn's HTML changes frequently, which can break scraping. |
| 2 | **No error recovery in scout pipeline** | If the pipeline crashes mid-run (e.g., Gemini quota exhausted), there's no checkpoint/resume mechanism. |
| 3 | **Airtable Base ID is hardcoded** | `appABPMwKgXkr8Rgn` is hardcoded in both `scout.py` and `tailor.py`. Should be an env var. |
| 4 | **Prompt is duplicated** | The Command Center (app.py) has an inline copy of the ATS prompt instead of importing `MASTER_PROMPT` from `prompts.py`. |
| 5 | **Gemini SDK mismatch** | `scout.py` uses `google-genai` (new SDK with `genai.Client`), while `app.py` and `tailor.py` use `google-generativeai` (legacy SDK with `genai.configure`). Two different SDKs in the same project. |
| 6 | **No Supabase RLS policies defined** | `tier1_rejections` has RLS enabled but no policies are visible — may block access in certain contexts. |
| 7 | **No cover letter generation** | The system only tailors resumes, doesn't generate cover letters. |
| 8 | **Single resume format** | Scout uses `resume.txt` (plain text). There's no mechanism to use different resume versions for different job types. |
| 9 | **Apify token unused** | `APIFY_TOKEN` is in secrets but not referenced by any code — possible leftover from a planned feature. |
| 10 | **Seniority filter false positives** | "Associate Vice President" or "Associate Director" would pass, but "Associate Engineer" is blocked. This is correct, but the filter could be more nuanced. |

---

## 10. Future Roadmap (What Could Be Built Next)

### High Priority
- [ ] **Cover Letter Generator** — Use Gemini to generate tailored cover letters based on JD + resume
- [ ] **Email Drafter** — Auto-draft follow-up or application emails
- [ ] **Application Status Automation** — Auto-update status from "Not Applied" → "Applied" after resume download
- [ ] **Multi-Resume Support** — Maintain multiple resume versions, auto-select best match per JD domain

### Medium Priority
- [ ] **Interview Prep Module** — Given a JD, generate likely interview questions and talking points
- [ ] **Analytics Dashboard** — Track conversion rates (applied → interview → offer), time-series charts
- [ ] **Apify Integration** — Use the existing Apify token for more reliable LinkedIn scraping (official API)
- [ ] **Naukri / Indeed Scraping** — Expand beyond LinkedIn to other job boards
- [ ] **Consolidate Gemini SDKs** — Standardize on one SDK (`google-genai` or `google-generativeai`)

### Low Priority / Nice-to-Have
- [ ] **Authentication** — Add user login for multi-user support
- [ ] **Notifications** — Email/Slack alerts when new high-score jobs are found
- [ ] **Resume Version Control** — Track which version of resume was sent to each company
- [ ] **Bulk Apply Assistant** — Batch-process multiple "Not Applied" jobs
- [ ] **Docker Deployment** — Containerize the Streamlit app for cloud deployment
- [ ] **Checkpoint/Resume for Scout** — Save pipeline progress so it can resume after crashes

---

## 11. How to Run

### Streamlit Dashboard (Local)
```bash
cd my-ats
pip install -r requirements.txt
streamlit run app.py
```
Credentials are loaded from `.streamlit/secrets.toml` or entered manually in the sidebar.

### Scout Pipeline (Local)
```bash
cd my-ats
export GEMINI_API_KEY="..."
export AIRTABLE_TOKEN="..."
export SUPABASE_URL="..."
export SUPABASE_KEY="..."
pip install -r scout_requirements.txt
python scout.py
```

### Tests
```bash
cd my-ats
pip install pytest python-docx
pytest test_scout_refactor.py test_tailor.py -v
```

---

## 12. Key Design Decisions

1. **Two-Tier Scoring:** Tier 1 (local keyword matching) is fast and free, filtering out obvious mismatches. Only jobs passing Tier 1 burn a Gemini API call in Tier 2. This saves significant API costs.

2. **Supabase Rejection Cache:** Instead of re-evaluating previously rejected jobs, they're cached in Supabase for 36 hours. This prevents wasted Gemini calls on the same jobs across consecutive scout runs.

3. **Dual Dedup Strategy:** Both URL-based and company+role-based dedup are used. URL handles exact matches; company+role (with normalization) catches same job reposted under different URLs.

4. **DOCX In-Place Editing:** The resume tailoring preserves original formatting by modifying runs within existing paragraphs rather than regenerating the document. This keeps fonts, sizes, colors, and bullet styles intact.

5. **Model Cascade for Tailoring:** Uses cheapest model first, falls back to more expensive ones only on 429 rate limits. This maximizes throughput within free-tier limits.

6. **IST Timestamps:** All dates use India Standard Time (IST, UTC+5:30) since the job search is India-focused.

---

## 13. Dependency Summary

### Streamlit App (`requirements.txt` — 67 packages)
Key packages: `streamlit`, `google-generativeai`, `pdfplumber`, `python-docx`, `requests`, `pandas`, `supabase`, `pytz`

### Scout Pipeline (`scout_requirements.txt` — 4 packages)
`requests`, `beautifulsoup4`, `google-genai`, `supabase`

> **Note:** The scout pipeline uses `google-genai` (new SDK) while the Streamlit app uses `google-generativeai` (legacy SDK). These are different packages with different APIs.
