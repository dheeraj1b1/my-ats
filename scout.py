"""
scout.py — Automated LinkedIn Job Scout Pipeline
=================================================
Scrapes LinkedIn public job board for QA/SDET roles,
scores each JD against resume.txt using Gemini AI,
and pushes high-scoring matches (>=80%) to Airtable.

Designed to run headlessly via GitHub Actions every 12 hours.
"""

import os
import re
import time
import datetime
import urllib.parse
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai

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


# ─── Step 3: AI Scoring ─────────────────────────────────────────────────────

def score_job(job_description, resume_text, model):
    """
    Send the JD + resume to Gemini and get back an integer score 0-100.
    """
    prompt = f"""You are a strict Applicant Tracking System (ATS).
Compare the following Job Description against the Resume.
Evaluate ONLY based on hard skills, tools, technologies, and years of experience.
DO NOT assume skills. Only count skills explicitly mentioned in the resume.

Return ONLY a single integer from 0 to 100 representing the match percentage.
Do not return any other text, explanation, or formatting. Just the number.

--- JOB DESCRIPTION ---
{job_description}

--- RESUME ---
{resume_text}
"""
    try:
        response = model.generate_content(prompt)
        score_text = response.text.strip()
        # Extract just the number
        match = re.search(r"\d+", score_text)
        if match:
            score = int(match.group())
            return min(score, 100)  # Cap at 100
        return 0
    except Exception as e:
        print(f"      ⚠️ Gemini scoring error: {e}")
        return 0


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
    print("🚀 AUTOMATED JOB SCOUT PIPELINE")
    print(f"   Time: {datetime.datetime.now().isoformat()}")
    print("=" * 60)

    # Load environment variables
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    airtable_token = os.environ.get("AIRTABLE_TOKEN")

    if not gemini_api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    if not airtable_token:
        raise EnvironmentError("AIRTABLE_TOKEN environment variable is not set.")

    # Step 1: Read Resume
    resume_text = read_resume("resume.txt")

    # Step 2: Scrape LinkedIn
    jobs = scrape_linkedin_jobs()
    if not jobs:
        print("\n⚠️ No jobs were scraped. Exiting pipeline.")
        return

    # Step 3: AI Scoring
    print("\n🤖 Scoring jobs with Gemini AI...")
    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    qualified_jobs = []
    for i, job in enumerate(jobs):
        print(f"\n   [{i+1}/{len(jobs)}] Scoring: {job['title']} at {job['company']}...")
        score = score_job(job["description"], resume_text, model)
        print(f"      Score: {score}%", end="")

        if score >= SCORE_THRESHOLD:
            print(f" ✅ QUALIFIED (>={SCORE_THRESHOLD}%)")
            qualified_jobs.append((job, score))
        else:
            print(f" ❌ Below threshold")

        time.sleep(1)  # Rate limiting for Gemini API

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
