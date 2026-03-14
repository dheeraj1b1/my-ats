"""
scout.py — Two-Tier ATS Job Scout Pipeline
============================================
Tier 1: Local Python keyword scoring (>= 50 to advance)
Tier 2: Gemini 2.5 Flash deep scan  (>= 80 to push)

Scrapes LinkedIn → Dedup via Airtable → Tier 1 → Tier 2 → Push to Airtable.
No login required. Runs headlessly via GitHub Actions every 12 hours.
"""

import os
import re
import time
import datetime
import urllib.parse
import requests
from bs4 import BeautifulSoup
from google import genai

# ─── Configuration ───────────────────────────────────────────────────────────

SEARCH_KEYWORDS = "QA Automation OR SDET"
TARGET_CITIES = ["Bangalore", "Chennai", "Hyderabad"]
MAX_JOBS = 60
TIER1_THRESHOLD = 50
TIER2_THRESHOLD = 80
GEMINI_SLEEP = 120      # Sleep before each Gemini call (5 RPM free tier)
GEMINI_RETRY_SLEEP = 180  # Sleep on 429 before retry

AIRTABLE_BASE_ID = "appABPMwKgXkr8Rgn"
AIRTABLE_TABLE_NAME = "Applications"

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

PRIMARY_SKILLS = [
    "Java", "Selenium", "REST Assured", "TestNG", "API Testing",
    "CI/CD", "Jenkins", "Microservices", "JMeter", "SQL",
]

SECONDARY_SKILLS = [
    "Python", "Playwright", "Appium",
]


# ─── Read Resume ────────────────────────────────────────────────────────────

def read_resume(filepath="resume.txt"):
    print(f"📄 Reading resume from: {filepath}")
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Resume file '{filepath}' not found.")
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        raise ValueError(f"Resume file '{filepath}' is empty.")
    print(f"   ✅ Loaded {len(text)} characters")
    return text


# ─── Scrape LinkedIn ────────────────────────────────────────────────────────

def build_search_url(keywords, location):
    params = {
        "keywords": keywords,
        "location": location,
        "f_TPR": "r86400",
        "sortBy": "DD",
    }
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)


def scrape_linkedin_jobs():
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
        job_cards = soup.find_all("div", class_="base-card")

        if not job_cards:
            job_cards = soup.find_all("li", class_=re.compile(r"result-card"))

        print(f"   Found {len(job_cards)} job cards")

        for card in job_cards:
            if len(all_jobs) >= MAX_JOBS:
                break
            try:
                title_tag = (
                    card.find("h3", class_=re.compile(r"base-search-card__title"))
                    or card.find("h3")
                )
                title = title_tag.get_text(strip=True) if title_tag else "Unknown"

                company_tag = (
                    card.find("h4", class_=re.compile(r"base-search-card__subtitle"))
                    or card.find("a", class_=re.compile(r"hidden-nested-link"))
                )
                company = company_tag.get_text(strip=True) if company_tag else "Unknown"

                link_tag = card.find("a", class_=re.compile(r"base-card__full-link"))
                if not link_tag:
                    link_tag = card.find("a", href=True)
                apply_link = link_tag["href"].split("?")[0] if link_tag else ""

                all_jobs.append({
                    "title": title,
                    "company": company,
                    "apply_link": apply_link,
                    "city": city,
                    "description": "",
                })
            except Exception as e:
                print(f"   ⚠️ Error parsing a job card: {e}")
                continue

        time.sleep(2)

    # Fetch full descriptions
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


# ─── Tier 1: Local Keyword Bouncer ──────────────────────────────────────────

def calculate_ats_score(resume_text, jd_text):
    """
    Primary Skills: 10 pts each | Secondary Skills: 2 pts each
    Skill earns points only if found in BOTH resume AND JD.
    Score capped at 100.
    """
    jd_lower = jd_text.lower()
    resume_lower = resume_text.lower()

    score = 0
    matched_primary = []
    matched_secondary = []

    for skill in PRIMARY_SKILLS:
        if skill.lower() in jd_lower and skill.lower() in resume_lower:
            score += 10
            matched_primary.append(skill)

    for skill in SECONDARY_SKILLS:
        if skill.lower() in jd_lower and skill.lower() in resume_lower:
            score += 2
            matched_secondary.append(skill)

    final_score = min(score, 100)

    if matched_primary or matched_secondary:
        print(f"      Primary ({len(matched_primary)}): {', '.join(matched_primary) if matched_primary else 'None'}")
        print(f"      Secondary ({len(matched_secondary)}): {', '.join(matched_secondary) if matched_secondary else 'None'}")

    return final_score


# ─── Tier 2: Gemini 2.5 Flash Deep Scan ─────────────────────────────────────

def gemini_deep_scan(jd_text, resume_text, client):
    """
    Sends JD + Resume to gemini-2.5-flash for contextual ATS scoring.
    Returns an integer score 0-100.

    STRICT TIMERS:
      - time.sleep(120) before every call (5 RPM free tier)
      - time.sleep(180) + 1 retry on 429 errors
    """
    prompt = f"""You are a strict Applicant Tracking System (ATS).
Compare the following Job Description against the Resume.
Evaluate ONLY based on hard skills, tools, technologies, and years of experience.
DO NOT assume skills. Only count skills explicitly mentioned in the resume.

Return ONLY a single integer from 0 to 100 representing the match percentage.
Do not return any other text, explanation, or formatting. Just the number.

--- JOB DESCRIPTION ---
{jd_text}

--- RESUME ---
{resume_text}
"""

    # STRICT: 120s sleep before every Gemini call
    print(f"      ⏳ Rate limit pause ({GEMINI_SLEEP}s)...", end="", flush=True)
    time.sleep(GEMINI_SLEEP)
    print(" done")

    # First attempt
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        score_text = response.text.strip()
        match = re.search(r"\d+", score_text)
        if match:
            return min(int(match.group()), 100)
        return 0

    except Exception as e:
        error_str = str(e)

        # Handle 429 rate limit — sleep 180s and retry once
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            print(f"      ⚠️ Hit 429 rate limit. Sleeping {GEMINI_RETRY_SLEEP}s and retrying...")
            time.sleep(GEMINI_RETRY_SLEEP)
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                score_text = response.text.strip()
                match = re.search(r"\d+", score_text)
                if match:
                    return min(int(match.group()), 100)
                return 0
            except Exception as retry_err:
                print(f"      ❌ Retry also failed: {retry_err}")
                return 0
        else:
            print(f"      ❌ Gemini error: {e}")
            return 0


# ─── Airtable: Duplicate Checker ────────────────────────────────────────────

def get_existing_jobs(airtable_token):
    """Fetch existing Apply Link URLs from Airtable (Not Applied + Applied)."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {airtable_token}"}

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
            if apply_link and status in ("Not Applied", "Applied"):
                existing_urls.add(apply_link)

        offset = data.get("offset")
        if not offset:
            break

    print(f"   ✅ Found {len(existing_urls)} existing jobs in Airtable")
    return existing_urls


# ─── Airtable: Push ─────────────────────────────────────────────────────────

def push_to_airtable(job, score, airtable_token):
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
            print(f"      ✅ Logged to Airtable: {job['title']} at {job['company']} (AI Score: {score}%)")
        else:
            print(f"      ❌ Airtable error ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"      ❌ Airtable request failed: {e}")


# ─── Main Pipeline ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🚀 TWO-TIER ATS JOB SCOUT PIPELINE")
    print(f"   Time: {datetime.datetime.now().isoformat()}")
    print(f"   Max Jobs: {MAX_JOBS}")
    print(f"   Tier 1 (Local):  >= {TIER1_THRESHOLD}% to advance")
    print(f"   Tier 2 (Gemini): >= {TIER2_THRESHOLD}% to push")
    print(f"   Model: gemini-2.5-flash")
    print(f"   Gemini sleep: {GEMINI_SLEEP}s | Retry sleep: {GEMINI_RETRY_SLEEP}s")
    print("=" * 60)

    # Load environment variables
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    airtable_token = os.environ.get("AIRTABLE_TOKEN")

    if not gemini_api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    if not airtable_token:
        raise EnvironmentError("AIRTABLE_TOKEN environment variable is not set.")

    # Set up Gemini client (google-genai SDK)
    client = genai.Client(api_key=gemini_api_key)

    # Step 1: Read Resume
    resume_text = read_resume("resume.txt")

    # Step 2: Scrape LinkedIn
    jobs = scrape_linkedin_jobs()
    if not jobs:
        print("\n⚠️ No jobs were scraped. Exiting pipeline.")
        return

    # Step 3: Fetch existing Airtable records for deduplication
    existing_urls = get_existing_jobs(airtable_token)

    # Step 4: Two-Tier Scoring
    print("\n" + "=" * 60)
    print("🏗️  TWO-TIER SCORING")
    print("=" * 60)

    stats = {
        "total": len(jobs),
        "duplicates": 0,
        "tier1_fail": 0,
        "tier2_fail": 0,
        "pushed": 0,
    }

    for i, job in enumerate(jobs):
        print(f"\n── [{i+1}/{len(jobs)}] {job['title']} at {job['company']} ({job['city']})")

        # ── Dedup Check ──
        if job["apply_link"] in existing_urls:
            print(f"   ⏭️ Skipping {job['company']} - Already logged")
            stats["duplicates"] += 1
            continue

        # ── Tier 1: Local Keyword Bouncer ──
        print(f"   🔸 Tier 1 (Local Keyword Scan)...")
        tier1_score = calculate_ats_score(resume_text, job["description"])
        print(f"      Tier 1 Score: {tier1_score}%", end="")

        if tier1_score < TIER1_THRESHOLD:
            print(f" ❌ REJECTED (below {TIER1_THRESHOLD}%)")
            stats["tier1_fail"] += 1
            continue
        else:
            print(f" ✅ PASSED → advancing to Tier 2")

        # ── Tier 2: Gemini 2.5 Flash Deep Scan ──
        print(f"   🔹 Tier 2 (Gemini 2.5 Flash Deep Scan)...")
        tier2_score = gemini_deep_scan(job["description"], resume_text, client)
        print(f"      Tier 2 Score: {tier2_score}%", end="")

        if tier2_score >= TIER2_THRESHOLD:
            print(f" ✅ QUALIFIED (>={TIER2_THRESHOLD}%)")
            push_to_airtable(job, tier2_score, airtable_token)
            stats["pushed"] += 1
        else:
            print(f" ❌ REJECTED by AI (below {TIER2_THRESHOLD}%)")
            stats["tier2_fail"] += 1

    # Summary
    print(f"\n{'=' * 60}")
    print(f"📊 PIPELINE SUMMARY")
    print(f"   Total scraped:        {stats['total']}")
    print(f"   Duplicates skipped:   {stats['duplicates']}")
    print(f"   Tier 1 rejected:      {stats['tier1_fail']}")
    print(f"   Tier 2 rejected:      {stats['tier2_fail']}")
    print(f"   Pushed to Airtable:   {stats['pushed']}")
    print(f"{'=' * 60}")
    print("✅ PIPELINE COMPLETE")


if __name__ == "__main__":
    main()
