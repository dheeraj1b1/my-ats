"""
scout.py — Automated LinkedIn Job Scout Pipeline (Local ATS Engine)
====================================================================
Scrapes LinkedIn public job board for QA/SDET roles,
scores each JD using a local skill-matching ATS engine,
checks for duplicates in Airtable, and pushes qualifying jobs.

No external AI APIs required. Runs headlessly via GitHub Actions.
"""

import os
import re
import time
import datetime
import urllib.parse
import requests
from bs4 import BeautifulSoup

# ─── Configuration ───────────────────────────────────────────────────────────

SEARCH_KEYWORDS = "QA Automation OR SDET"
TARGET_CITIES = ["Bangalore", "Chennai", "Hyderabad"]
MAX_JOBS = 50
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

# ─── Skill Definitions ──────────────────────────────────────────────────────

# Primary Core Skills — 10 points each (max 100 from these alone)
PRIMARY_SKILLS = [
    "Java",
    "Selenium",
    "REST Assured",
    "TestNG",
    "API Testing",
    "CI/CD",
    "Jenkins",
    "Microservices",
    "JMeter",
    "SQL",
]

# Secondary Skills — 2 points each
SECONDARY_SKILLS = [
    "Python",
    "Playwright",
    "Appium",
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

        time.sleep(1)

    print(f"\n✅ Total jobs scraped: {len(all_jobs)}")
    return all_jobs


# ─── Step 3: Local ATS Scoring Engine ───────────────────────────────────────

def calculate_ats_score(resume_text, jd_text):
    """
    Calculate an ATS match score (0–100) for a Java/Selenium SDET.

    Scoring:
      - Primary Core Skills: 10 points each (max 100 from 10 skills)
      - Secondary Skills:     2 points each (bonus on top)
      - Final score capped at 100.

    Both resume_text and jd_text are scanned case-insensitively.
    A skill must appear in BOTH the resume AND the JD to earn points.
    """
    jd_lower = jd_text.lower()
    resume_lower = resume_text.lower()

    score = 0
    matched_primary = []
    matched_secondary = []

    # Score primary skills (10 pts each)
    for skill in PRIMARY_SKILLS:
        skill_lower = skill.lower()
        if skill_lower in jd_lower and skill_lower in resume_lower:
            score += 10
            matched_primary.append(skill)

    # Score secondary skills (2 pts each)
    for skill in SECONDARY_SKILLS:
        skill_lower = skill.lower()
        if skill_lower in jd_lower and skill_lower in resume_lower:
            score += 2
            matched_secondary.append(skill)

    # Cap at 100
    final_score = min(score, 100)

    # Log breakdown
    if matched_primary or matched_secondary:
        print(f"      Primary matches ({len(matched_primary)}): {', '.join(matched_primary) if matched_primary else 'None'}")
        print(f"      Secondary matches ({len(matched_secondary)}): {', '.join(matched_secondary) if matched_secondary else 'None'}")

    return final_score


# ─── Step 4: Airtable Duplicate Checker ─────────────────────────────────────

def get_existing_jobs(airtable_token):
    """
    Fetch all existing records from Airtable Applications table.
    Returns a set of Apply Link URLs that are already logged,
    filtering for records with Status "Not Applied" (not yet acted on).
    """
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {airtable_token}",
    }

    existing_urls = set()
    offset = None

    print("\n🔍 Checking Airtable for existing jobs...")

    while True:
        params = {}
        if offset:
            params["offset"] = offset

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"   ⚠️ Failed to fetch Airtable records: {e}")
            break

        for record in data.get("records", []):
            fields = record.get("fields", {})
            apply_link = fields.get("Apply Link", "")
            status = fields.get("Status", "")

            # Track jobs that are either "Not Applied" or already applied
            if apply_link and status in ("Not Applied", "Applied"):
                existing_urls.add(apply_link)

        # Airtable paginates with an offset token
        offset = data.get("offset")
        if not offset:
            break

    print(f"   ✅ Found {len(existing_urls)} existing jobs in Airtable")
    return existing_urls


# ─── Step 5: Airtable Push ──────────────────────────────────────────────────

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
    print(f"   Primary Skills: {len(PRIMARY_SKILLS)} × 10pts")
    print(f"   Secondary Skills: {len(SECONDARY_SKILLS)} × 2pts")
    print("=" * 60)

    # Load environment variable
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

    # Step 3: Fetch existing Airtable records for deduplication
    existing_urls = get_existing_jobs(airtable_token)

    # Step 4: Score and push
    print("\n🤖 Scoring jobs with Local ATS Engine...")
    print(f"   Primary: {', '.join(PRIMARY_SKILLS)}")
    print(f"   Secondary: {', '.join(SECONDARY_SKILLS)}")

    qualified_count = 0
    skipped_dupes = 0
    below_threshold = 0

    for i, job in enumerate(jobs):
        print(f"\n   [{i+1}/{len(jobs)}] {job['title']} at {job['company']}...")

        # Duplicate check
        if job["apply_link"] in existing_urls:
            print(f"      ⏭️ Skipping {job['company']} - Already logged")
            skipped_dupes += 1
            continue

        # Score the job
        score = calculate_ats_score(resume_text, job["description"])
        print(f"      Score: {score}%", end="")

        if score >= SCORE_THRESHOLD:
            print(f" ✅ QUALIFIED (>={SCORE_THRESHOLD}%)")
            push_to_airtable(job, score, airtable_token)
            qualified_count += 1
        else:
            print(f" ❌ Below threshold")
            below_threshold += 1

    # Summary
    print(f"\n{'=' * 60}")
    print(f"📊 PIPELINE SUMMARY")
    print(f"   Total scraped:      {len(jobs)}")
    print(f"   Duplicates skipped: {skipped_dupes}")
    print(f"   Below threshold:    {below_threshold}")
    print(f"   Pushed to Airtable: {qualified_count}")
    print(f"{'=' * 60}")
    print("✅ PIPELINE COMPLETE")


if __name__ == "__main__":
    main()
