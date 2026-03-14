"""
scout.py — Automated LinkedIn Job Scout Pipeline (Local ATS Engine)
====================================================================
Scrapes LinkedIn public job board for QA/SDET roles,
scores each JD against resume.txt using a local keyword-based ATS engine,
and pushes high-scoring matches (>=80%) to Airtable.

No external AI APIs required. Runs headlessly via GitHub Actions every 12 hours.
"""

import os
import re
import time
import datetime
import urllib.parse
from collections import Counter
import requests
from bs4 import BeautifulSoup

# ─── Configuration ───────────────────────────────────────────────────────────

SEARCH_KEYWORDS = "QA Automation OR SDET"
TARGET_CITIES = ["Bangalore", "Chennai", "Hyderabad"]
MAX_JOBS = 15
SCORE_THRESHOLD = 80

AIRTABLE_BASE_ID = "appABPMwKgXkr8Rgn"
AIRTABLE_TABLE_NAME = "Applications"

# Anti-blocking headers for LinkedIn public pages
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
}

# ─── Stop Words ──────────────────────────────────────────────────────────────
# Common English words filtered out during keyword extraction

STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "must", "ought", "i", "me", "my", "we", "our", "you", "your", "he",
    "him", "his", "she", "her", "it", "its", "they", "them", "their",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "am", "if", "then", "else", "when", "where", "why", "how", "all",
    "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "so", "than", "too",
    "very", "just", "about", "above", "after", "again", "also", "any",
    "because", "before", "below", "between", "during", "here", "into",
    "out", "over", "same", "through", "under", "until", "up", "while",
    "able", "across", "etc", "e", "g", "eg", "ie", "vs", "via",
    "work", "working", "worked", "experience", "experienced", "using",
    "used", "use", "including", "include", "includes", "ensure",
    "strong", "good", "well", "new", "role", "team", "based",
    "within", "along", "like", "knowledge", "understanding",
    "years", "year", "required", "preferred", "minimum", "plus",
    "looking", "join", "opportunity", "responsible", "responsibilities",
    "candidate", "candidates", "ideal", "key", "skills", "skill",
    "requirements", "qualification", "qualifications", "description",
    "job", "position", "apply", "company", "application",
}

# ─── Technical Terms (Higher Weight) ─────────────────────────────────────────
# These keywords get 3x weight when matched

TECHNICAL_TERMS = {
    # Languages & Core
    "java", "python", "javascript", "typescript", "sql", "core java",
    # Frameworks & Libraries
    "selenium", "selenium webdriver", "testng", "junit", "cucumber",
    "rest assured", "playwright", "cypress", "appium", "karate",
    "page object model", "pom", "bdd", "tdd",
    # API & Web Services
    "api testing", "api", "rest", "restful", "soap", "postman",
    "swagger", "graphql", "web services", "microservices",
    # CI/CD & DevOps
    "jenkins", "github actions", "docker", "kubernetes", "harness",
    "ci/cd", "cicd", "ci cd", "gradle", "maven", "git",
    "aws", "ec2", "cloudwatch", "rds", "azure", "gcp",
    # Testing Types
    "automation testing", "manual testing", "regression testing",
    "performance testing", "load testing", "integration testing",
    "system testing", "functional testing", "smoke testing",
    "sanity testing", "uat", "e2e", "end to end",
    # Tools
    "jmeter", "loadrunner", "splunk", "kafka", "jira", "confluence",
    "testrail", "bugzilla", "qtest", "artifactory", "dbeaver",
    "intellij", "vs code", "webdrivermanager", "xpath",
    # Databases
    "mysql", "postgresql", "mongodb", "oracle", "sql server",
    # Methodologies
    "agile", "scrum", "sdlc", "stlc", "kanban",
    # Roles
    "sdet", "qa", "qe", "qa engineer", "qa automation",
    "automation engineer", "test engineer", "quality engineer",
    "quality assurance",
}

# ─── Technical Phrases for Exact Match Bonus ─────────────────────────────────
# Multi-word phrases that get bonus points when found as exact matches

TECHNICAL_PHRASES = [
    "selenium webdriver", "rest assured", "page object model",
    "github actions", "ci/cd", "api testing", "automation testing",
    "regression testing", "performance testing", "integration testing",
    "system testing", "manual testing", "load testing",
    "qa automation", "core java", "web services", "microservices",
    "end to end", "automation engineer", "qa engineer",
    "test engineer", "quality assurance", "automation framework",
    "test framework", "continuous integration", "continuous delivery",
    "service virtualization", "functional testing",
]


# ─── Step 1: Read Resume ────────────────────────────────────────────────────

def read_resume(filepath="resume.txt"):
    """Read the user's resume from a plain text file."""
    print(f"📄 Reading resume from: {filepath}")
    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"Resume file '{filepath}' not found. "
            "Please create it in the repo root with your resume text."
        )
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        raise ValueError(f"Resume file '{filepath}' is empty.")
    print(f"   ✅ Loaded {len(text)} characters")
    return text


# ─── Step 2: Scrape LinkedIn ────────────────────────────────────────────────

def build_search_url(keywords, location):
    """
    Build a LinkedIn public job search URL.
    f_TPR=r86400 = past 24 hours only.
    sortBy=DD    = sort by most recent.
    """
    params = {
        "keywords": keywords,
        "location": location,
        "f_TPR": "r86400",
        "sortBy": "DD",
    }
    base = "https://www.linkedin.com/jobs/search/?"
    return base + urllib.parse.urlencode(params)


def scrape_linkedin_jobs():
    """
    Scrape LinkedIn's public job board for each target city.
    Returns a list of job dicts up to MAX_JOBS total.
    """
    all_jobs = []

    for city in TARGET_CITIES:
        if len(all_jobs) >= MAX_JOBS:
            break

        url = build_search_url(SEARCH_KEYWORDS, city)
        print(f"\n🔎 Scraping jobs in {city}...")
        print(f"   URL: {url}")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"   ⚠️ Failed to fetch {city}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        # LinkedIn public pages use <div class="base-card"> for each job card
        job_cards = soup.find_all("div", class_="base-card")

        if not job_cards:
            # Fallback: try alternate selectors
            job_cards = soup.find_all("li", class_=re.compile(r"result-card"))

        print(f"   Found {len(job_cards)} job cards")

        for card in job_cards:
            if len(all_jobs) >= MAX_JOBS:
                break

            try:
                # Extract job title
                title_tag = (
                    card.find("h3", class_=re.compile(r"base-search-card__title"))
                    or card.find("h3")
                )
                title = title_tag.get_text(strip=True) if title_tag else "Unknown"

                # Extract company name
                company_tag = (
                    card.find("h4", class_=re.compile(r"base-search-card__subtitle"))
                    or card.find("a", class_=re.compile(r"hidden-nested-link"))
                )
                company = company_tag.get_text(strip=True) if company_tag else "Unknown"

                # Extract apply link
                link_tag = card.find("a", class_=re.compile(r"base-card__full-link"))
                if not link_tag:
                    link_tag = card.find("a", href=True)
                apply_link = link_tag["href"].split("?")[0] if link_tag else ""

                all_jobs.append({
                    "title": title,
                    "company": company,
                    "apply_link": apply_link,
                    "city": city,
                    "description": "",  # Will be filled by scraping detail page
                })

            except Exception as e:
                print(f"   ⚠️ Error parsing a job card: {e}")
                continue

        # Be polite between city requests
        time.sleep(2)

    # Step 2b: Scrape individual job detail pages for full descriptions
    print(f"\n📝 Fetching full descriptions for {len(all_jobs)} jobs...")
    for i, job in enumerate(all_jobs):
        if not job["apply_link"]:
            continue
        try:
            resp = requests.get(job["apply_link"], headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # LinkedIn detail pages put the JD in a specific section
            desc_section = (
                soup.find("div", class_=re.compile(r"show-more-less-html__markup"))
                or soup.find("div", class_=re.compile(r"description__text"))
                or soup.find("section", class_=re.compile(r"description"))
            )
            if desc_section:
                job["description"] = desc_section.get_text(separator="\n", strip=True)
                print(f"   [{i+1}/{len(all_jobs)}] ✅ {job['title']} at {job['company']} — {len(job['description'])} chars")
            else:
                job["description"] = f"Role: {job['title']} at {job['company']}"
                print(f"   [{i+1}/{len(all_jobs)}] ⚠️ No description found for {job['title']}")

        except Exception as e:
            print(f"   [{i+1}/{len(all_jobs)}] ⚠️ Failed to get description: {e}")
            job["description"] = f"Role: {job['title']} at {job['company']}"

        time.sleep(1)  # Be polite

    print(f"\n✅ Total jobs scraped: {len(all_jobs)}")
    return all_jobs


# ─── Step 3: Local ATS Scoring Engine ───────────────────────────────────────

def extract_keywords(text):
    """
    Extract meaningful keywords from text.
    Returns a Counter of lowercase keywords with stop words removed.
    """
    # Normalize text
    text_lower = text.lower()
    # Extract words (alphanumeric + some special chars like / for CI/CD)
    words = re.findall(r"[a-z][a-z0-9/#+.-]*[a-z0-9+#]|[a-z]", text_lower)
    # Filter out stop words and very short words
    filtered = [w for w in words if w not in STOP_WORDS and len(w) >= 2]
    return Counter(filtered)


def find_phrase_matches(text, phrases):
    """
    Count how many technical phrases are found in the text.
    Returns (matches_found, total_phrases_checked).
    """
    text_lower = text.lower()
    matches = 0
    for phrase in phrases:
        if phrase in text_lower:
            matches += 1
    return matches


def calculate_ats_score(resume_text, jd_text):
    """
    Calculate an ATS match score (0–100) between resume and job description.
    
    Scoring breakdown:
      - 50% weight: Single keyword overlap (resume keywords found in JD)
      - 30% weight: Technical term matching (weighted 3x)
      - 20% weight: Exact phrase matching bonus
    """
    # Extract keywords from both texts
    resume_keywords = extract_keywords(resume_text)
    jd_keywords = extract_keywords(jd_text)

    if not resume_keywords or not jd_keywords:
        return 0

    # ── Component 1: General Keyword Overlap (50% weight) ──
    # What % of the JD's keywords appear in the resume?
    jd_unique = set(jd_keywords.keys())
    resume_unique = set(resume_keywords.keys())
    
    if jd_unique:
        keyword_overlap = len(jd_unique & resume_unique) / len(jd_unique)
    else:
        keyword_overlap = 0

    # ── Component 2: Technical Term Matching (30% weight, 3x boost) ──
    # How many technical terms from the JD are in the resume?
    jd_lower = jd_text.lower()
    resume_lower = resume_text.lower()

    jd_tech_terms = [t for t in TECHNICAL_TERMS if t in jd_lower]
    if jd_tech_terms:
        matched_tech = sum(1 for t in jd_tech_terms if t in resume_lower)
        tech_score = matched_tech / len(jd_tech_terms)
    else:
        tech_score = 0

    # ── Component 3: Exact Phrase Matching (20% weight) ──
    # How many technical phrases from the JD are in the resume?
    jd_phrases = [p for p in TECHNICAL_PHRASES if p in jd_lower]
    if jd_phrases:
        matched_phrases = sum(1 for p in jd_phrases if p in resume_lower)
        phrase_score = matched_phrases / len(jd_phrases)
    else:
        phrase_score = 0

    # ── Final Weighted Score ──
    final_score = (
        (keyword_overlap * 50) +
        (tech_score * 30) +
        (phrase_score * 20)
    )

    return round(min(final_score, 100))


# ─── Step 4: Airtable Push ──────────────────────────────────────────────────

def push_to_airtable(job, score, airtable_token):
    """POST a qualifying job to Airtable."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {airtable_token}",
        "Content-Type": "application/json",
    }
    data = {
        "fields": {
            "Company": job["company"],
            "Role": job["title"],
            "Match Score": score,
            "Status": "Not Applied",
            "Apply Link": job["apply_link"],
            "Applied Date": datetime.datetime.now().strftime("%Y-%m-%d"),
        }
    }

    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        if resp.status_code == 200:
            print(f"      ✅ Logged to Airtable: {job['title']} at {job['company']}")
        else:
            print(f"      ❌ Airtable error ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"      ❌ Airtable request failed: {e}")


# ─── Main Pipeline ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🚀 AUTOMATED JOB SCOUT PIPELINE (Local ATS Engine)")
    print(f"   Time: {datetime.datetime.now().isoformat()}")
    print(f"   Threshold: {SCORE_THRESHOLD}%")
    print("=" * 60)

    # Load environment variables
    airtable_token = os.environ.get("AIRTABLE_TOKEN")
    if not airtable_token:
        raise EnvironmentError("AIRTABLE_TOKEN environment variable is not set.")

    # Step 1: Read Resume
    resume_text = read_resume("resume.txt")

    # Step 2: Scrape LinkedIn
    jobs = scrape_linkedin_jobs()
    if not jobs:
        print("\n⚠️ No jobs were scraped. Exiting pipeline.")
        return

    # Step 3: Local ATS Scoring
    print("\n🤖 Scoring jobs with Local ATS Engine...")
    print(f"   Scoring method: Keyword overlap (50%) + Technical terms (30%) + Phrase match (20%)")

    qualified_jobs = []
    for i, job in enumerate(jobs):
        print(f"\n   [{i+1}/{len(jobs)}] Scoring: {job['title']} at {job['company']}...")
        score = calculate_ats_score(resume_text, job["description"])
        print(f"      Score: {score}%", end="")

        if score >= SCORE_THRESHOLD:
            print(f" ✅ QUALIFIED (>={SCORE_THRESHOLD}%)")
            qualified_jobs.append((job, score))
        else:
            print(f" ❌ Below threshold")

    # Step 4: Push qualified jobs to Airtable
    print(f"\n📊 Results: {len(qualified_jobs)}/{len(jobs)} jobs scored >= {SCORE_THRESHOLD}%")

    if qualified_jobs:
        print("\n📤 Pushing qualified jobs to Airtable...")
        for job, score in qualified_jobs:
            push_to_airtable(job, score, airtable_token)
    else:
        print("\n📭 No jobs met the threshold. Nothing to push.")

    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
