"""
scout.py — Two-Tier ATS Job Scout Pipeline (Stateful)
======================================================
Tier 1: Local Python keyword scoring (>= 35 to advance)
Tier 2: Gemini 3.1 Flash Lite deep scan (>= 80 to push)

Scrapes LinkedIn → Dedup via Airtable + Supabase → Tier 1 → Tier 2 → Push to Airtable.
Rejected jobs are cached in Supabase for 36 hours to avoid re-evaluation.
No login required. Runs headlessly via GitHub Actions every 12 hours.
"""

import os
import re
import time
import datetime
import random
import urllib.parse
import json
import requests
from bs4 import BeautifulSoup
from google import genai
from supabase import create_client

from prompts import MASTER_PROMPT

# ─── Configuration ───────────────────────────────────────────────────────────

SEARCH_KEYWORDS = "SDET OR Automation Engineer OR Quality Assurance Engineer OR Software Engineer in Test OR Test Automation Engineer OR QA Automation Engineer OR QA Engineer OR Test Engineer OR QA Engineer OR Test Engineer NOT Junior NOT Fresher NOT Intern NOT Trainee"
TARGET_CITIES = ["Bangalore"]
JOBS_PER_CITY = 80
TIER1_THRESHOLD = 28
TIER2_THRESHOLD = 80
GEMINI_SLEEP = 90         # Sleep before each Gemini call (1 minute)
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

# ─── Company Normalization ──────────────────────────────────────────────────

COMPANY_STRIP_SUFFIXES = [
    "private limited", "pvt ltd", "ltd", "inc", "llc",
    "solutions", "technologies", "tech", "services", "india", "group",
]


def normalize_company(name: str) -> str:
    """Lowercase, strip trailing punctuation, and remove common corporate suffixes."""
    name = name.lower().strip().rstrip(".,;:")
    changed = True
    while changed:
        changed = False
        for suffix in COMPANY_STRIP_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip().rstrip(".,;:")
                changed = True
    return name


def normalize_title(title: str) -> str:
    """Lowercase, remove punctuation, and strip noise words."""
    title = title.lower()
    title = re.sub(r'[^\w\s]', ' ', title)
    
    noise_words = [
        "senior", "sr", "junior", "jr", "lead", "associate", "contract", 
        "remote", "onsite", "hybrid", "walkin", "urgent", "dna", "atc", "gig", "now"
    ]
    
    for word in noise_words:
        title = re.sub(r'\b' + re.escape(word) + r'\b', ' ', title)
        
    title = re.sub(r'\s+', ' ', title).strip()
    return title


def extract_linkedin_job_id(url: str) -> str:
    """Extract trailing numeric LinkedIn Job ID from URL."""
    if not url:
        return ""
    match = re.search(r'-(\d{9,12})(?:[/?]|$)', url)
    return match.group(1) if match else ""

RECRUITER_BLACKLIST = [
    "viraaj hr", "grid career", "vidpro hr",
    "sourcingxpress", "peopleprime", "people prime",
    "qualitest", "talent hub", "workforce",
]

SENIORITY_REJECT = [
    "junior", "jr.", "entry level", "fresher",
    "trainee", "intern", "graduate trainee",
    "associate engineer", "associate developer",
    "associate qa", "associate test",
]

DOMAIN_REJECT_KEYWORDS = [
    "gaming", "casino", "gambling", "game studio",
    "travel", "hospitality", "hotel",
    "real estate", "property",
    "industrial automation", "embedded systems",
    "oil", "mining", "defence", "defense",
]

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


def read_resume_version(filepath="resume.txt"):
    """Read the first 5 lines of resume.txt to find the version identifier."""
    if not os.path.exists(filepath):
        return "Unknown Version"
    with open(filepath, "r", encoding="utf-8") as f:
        for _ in range(5):
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if line.lower().startswith("version:"):
                return line
    return "Unknown Version"


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
        city_count = 0
        url = build_search_url(SEARCH_KEYWORDS, city)
        print(f"\n🔎 Scraping jobs in {city} (max {JOBS_PER_CITY})...")
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
            if city_count >= JOBS_PER_CITY:
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
                city_count += 1
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
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                print(f"   [{i+1}/{len(all_jobs)}] ⚠️ Hit LinkedIn 429 Rate Limit. Sleeping 30s...")
                time.sleep(30)
                job["description"] = f"Role: {job['title']} at {job['company']}"
                continue
            else:
                print(f"   [{i+1}/{len(all_jobs)}] ⚠️ Failed to get description: {e}")
                job["description"] = f"Role: {job['title']} at {job['company']}"
        except Exception as e:
            print(f"   [{i+1}/{len(all_jobs)}] ⚠️ Failed to get description: {e}")
            job["description"] = f"Role: {job['title']} at {job['company']}"
            
        time.sleep(random.uniform(3.5, 5.5))

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


# ─── Tier 2: Gemini 3.1 Flash Lite Deep Scan ────────────────────────────────

def gemini_deep_scan(jd_text, resume_text, client):
    """
    Sends JD + Resume to gemini-3.1-flash-lite for contextual ATS scoring.
    Returns a tuple: (score, missing_details)

    STRICT TIMERS:
      - time.sleep(60) before every call (1 minute)
      - time.sleep(180) + 1 retry on 429 errors
    """
    prompt = MASTER_PROMPT.format(jd_text=jd_text, resume_text=resume_text)

    # STRICT: 60s sleep before every Gemini call
    print(f"      ⏳ Rate limit pause ({GEMINI_SLEEP}s / 1m)...", end="", flush=True)
    time.sleep(GEMINI_SLEEP)
    print(" done")

    def parse_gemini_response(text):
        try:
            score = 0
            # Extract Score using the same text format from app.py
            if "MATCH_SCORE:" in text:
                score_str = text.split("MATCH_SCORE:")[1].split("%")[0].strip()
                score = int(score_str)

            # Extract Missing Details as the rest of the text for Airtable Logging
            missing_details = text
            if "### Critical Missing Elements" in text:
                missing_details = "### Critical Missing Elements" + text.split("### Critical Missing Elements")[1]

            return min(score, 100), missing_details.strip()
        except Exception as e:
            print(f"      ⚠️ Parse error: {e}")
            return 0, ""

    # First attempt
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
        )
        return parse_gemini_response(response.text)

    except Exception as e:
        error_str = str(e)

        # Handle 429 rate limit — sleep 180s and retry once
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            print(f"      ⚠️ Hit 429 rate limit. Sleeping {GEMINI_RETRY_SLEEP}s and retrying...")
            time.sleep(GEMINI_RETRY_SLEEP)
            try:
                response = client.models.generate_content(
                    model="gemini-3.1-flash-lite-preview",
                    contents=prompt,
                )
                return parse_gemini_response(response.text)
            except Exception as retry_err:
                print(f"      ❌ Retry also failed: {retry_err}")
                return 0, ""
        else:
            print(f"      ❌ Gemini error: {e}")
            return 0, ""


# ─── Supabase: Rejection Cache ──────────────────────────────────────────────

def purge_old_rejections(supabase_client):
    """Delete rejection records older than 36 hours."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=36)
    cutoff_iso = cutoff.isoformat()
    try:
        result = supabase_client.table("tier1_rejections").delete().lt("rejected_at", cutoff_iso).execute()
        deleted = len(result.data) if result.data else 0
        print(f"   🗑️ Purged {deleted} stale rejections (older than 36h)")
    except Exception as e:
        print(f"   ⚠️ Failed to purge old rejections: {e}")


def get_rejected_jobs(supabase_client):
    """Fetch all rejected jobs from Supabase into sets of URLs, roles, and job IDs."""
    rejected_urls = set()
    rejected_roles = set()
    rejected_job_ids = set()
    try:
        result = supabase_client.table("tier1_rejections").select("job_url, company_name, job_title").execute()
        for row in result.data or []:
            url = row.get("job_url", "")
            if url:
                rejected_urls.add(url.split('?')[0])
                ext_id = extract_linkedin_job_id(url)
                if ext_id:
                    rejected_job_ids.add(ext_id)
                
            comp = row.get("company_name", "")
            title = row.get("job_title", "")
            if comp and title:
                rejected_roles.add((normalize_company(comp), normalize_title(title)))
                
        print(f"   ✅ Found {len(result.data or [])} previously rejected jobs in Supabase")
    except Exception as e:
        print(f"   ⚠️ Failed to fetch rejected jobs: {e}")
    return rejected_urls, rejected_roles, rejected_job_ids


def insert_rejection(supabase_client, job):
    """Insert a rejected job into the Supabase cache."""
    try:
        supabase_client.table("tier1_rejections").insert({
            "job_url": job["apply_link"],
            "company_name": job["company"],
            "job_title": job["title"],
        }).execute()
    except Exception as e:
        print(f"      ⚠️ Failed to cache rejection: {e}")


# ─── Airtable: Duplicate Checker ────────────────────────────────────────────

def get_airtable_jobs(airtable_token):
    """Fetch existing Apply Link URLs, Job IDs, and Roles from Airtable (ALL statuses)."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {airtable_token}"}

    existing_urls = set()
    existing_roles = set()
    existing_job_ids = set()
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
            company = fields.get("Company", "")
            role = fields.get("Role", "")
            job_id_field = str(fields.get("Job ID", "")).strip()

            if status in ("Not Applied", "Applied", "Rejected", "In Progress", "Selected", "closed"):
                if apply_link:
                    existing_urls.add(apply_link.split('?')[0])
                    ext_id = extract_linkedin_job_id(apply_link)
                    if ext_id:
                        existing_job_ids.add(ext_id)
                if company and role:
                    existing_roles.add((normalize_company(company), normalize_title(role)))
                if job_id_field:
                    existing_job_ids.add(job_id_field)

        offset = data.get("offset")
        if not offset:
            break

    print(f"   ✅ Found Airtable: {len(existing_urls)} URLs, {len(existing_roles)} Roles, {len(existing_job_ids)} Job IDs")
    return existing_urls, existing_roles, existing_job_ids


# ─── Airtable: Push ─────────────────────────────────────────────────────────

def push_to_airtable(job, score, missing_details, airtable_token, resume_version):
    """POST a qualifying job to Airtable with full payload including JD and resume version."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {airtable_token}",
        "Content-Type": "application/json",
    }

    # Calculate IST timestamp (UTC+5:30) — GitHub Actions defaults to UTC
    IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    ist_now = datetime.datetime.now(IST)
    applied_date_ist = ist_now.isoformat()

    job_id = extract_linkedin_job_id(job["apply_link"])
    data = {
        "fields": {
            "Job ID": job_id,
            "Company": job["company"],
            "Role": job["title"],
            "Match Score": score,
            "Missing Details": missing_details,
            "Status": "Not Applied",
            "Apply Link": job["apply_link"],
            "Applied Date": applied_date_ist,
            "JD Description": job.get("description", ""),
            "Resume Name": resume_version,
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
    print("🚀 TWO-TIER ATS JOB SCOUT PIPELINE (Stateful)")
    print(f"   Time: {datetime.datetime.now().isoformat()}")
    print(f"   Jobs per city: {JOBS_PER_CITY} × {len(TARGET_CITIES)} cities")
    print(f"   Tier 1 (Local):  >= {TIER1_THRESHOLD}% to advance")
    print(f"   Tier 2 (Gemini): >= {TIER2_THRESHOLD}% to push")
    print(f"   Model: gemini-3.1-flash-lite-preview")
    print(f"   Gemini sleep: {GEMINI_SLEEP}s | Retry sleep: {GEMINI_RETRY_SLEEP}s")
    print("=" * 60)

    # Load environment variables
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    airtable_token = os.environ.get("AIRTABLE_TOKEN")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if not gemini_api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    if not airtable_token:
        raise EnvironmentError("AIRTABLE_TOKEN environment variable is not set.")
    if not supabase_url or not supabase_key:
        raise EnvironmentError("SUPABASE_URL and SUPABASE_KEY environment variables must be set.")

    # Set up clients
    client = genai.Client(api_key=gemini_api_key)
    supabase = create_client(supabase_url, supabase_key)

    # Step 0: Purge stale rejections (older than 36 hours)
    print("\n🧹 Purging stale Supabase rejections...")
    purge_old_rejections(supabase)

    # Step 1: Read Resume
    resume_text = read_resume("resume.txt")
    resume_version = read_resume_version("resume.txt")
    print(f"   Resume version: {resume_version}")

    # Step 2: Scrape LinkedIn
    jobs = scrape_linkedin_jobs()
    if not jobs:
        print("\n⚠️ No jobs were scraped. Exiting pipeline.")
        return

    # Step 3: Fetch existing Airtable records for deduplication
    existing_urls, existing_roles, existing_job_ids = get_airtable_jobs(airtable_token)

    # Step 3b: Fetch Supabase rejection cache
    print("\n🔍 Checking Supabase rejection cache...")
    rejected_urls, rejected_roles, rejected_job_ids = get_rejected_jobs(supabase)

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

    seen_this_run = set()

    for i, job in enumerate(jobs):
        print(f"\n── [{i+1}/{len(jobs)}] {job['title']} at {job['company']} ({job['city']})")

        # ── Dedup Check (Airtable + Supabase) ──
        clean_url = job["apply_link"].split('?')[0] if job["apply_link"] else ""
        job_id = extract_linkedin_job_id(job["apply_link"])
        role_key = (normalize_company(job["company"]), normalize_title(job["title"]))
        
        # Layer 1-3
        is_dup_id = bool(job_id and (job_id in existing_job_ids or job_id in rejected_job_ids))
        is_dup_url = clean_url and (clean_url in existing_urls or clean_url in rejected_urls)
        is_dup_role = role_key[0] != "unknown" and role_key[1] != "" and (role_key in existing_roles or role_key in rejected_roles)

        if is_dup_id or is_dup_url or is_dup_role:
            if is_dup_id:
                reason = "Job ID matched"
            elif is_dup_url:
                reason = "URL matched"
            else:
                reason = "Company & Role matched"
            print(f"   ⏭️ Skipping {job['company']} - Already logged/rejected ({reason})")
            stats["duplicates"] += 1
            continue

        # Layer 4 (In-run dedup)
        if (job_id and job_id in seen_this_run) or role_key in seen_this_run:
            print(f"   ⏭️ Skipping {job['company']} - Already seen this run")
            stats["duplicates"] += 1
            continue
            
        if job_id:
            seen_this_run.add(job_id)
        if role_key[1]:
            seen_this_run.add(role_key)

        # ── Recruiter / Agency Blacklist ──
        if normalize_company(job["company"]) in RECRUITER_BLACKLIST:
            print(f"   ⏭️ Skipping {job['company']} — recruiter/agency")
            stats["duplicates"] += 1
            continue

        # ── Seniority Filter ──
        title_lower = job["title"].lower()
        if any(kw in title_lower for kw in SENIORITY_REJECT):
            print(f"   ⏭️ Skipping {job['title']} — below seniority level")
            insert_rejection(supabase, job)
            stats["tier1_fail"] += 1
            continue

        # ── Domain Mismatch Filter ──
        jd_text_lower = job.get("description", "").lower()
        if any(kw in jd_text_lower for kw in DOMAIN_REJECT_KEYWORDS):
            print(f"   ⏭️ Skipping {job['title']} — domain mismatch")
            insert_rejection(supabase, job)
            stats["tier1_fail"] += 1
            continue

        # ── Tier 1: Local Keyword Bouncer ──
        print(f"   🔸 Tier 1 (Local Keyword Scan)...")
        tier1_score = calculate_ats_score(resume_text, job["description"])
        print(f"      Tier 1 Score: {tier1_score}%", end="")

        if tier1_score < TIER1_THRESHOLD:
            print(f" ❌ REJECTED (below {TIER1_THRESHOLD}%)")
            insert_rejection(supabase, job)
            stats["tier1_fail"] += 1
            continue
        else:
            print(f" ✅ PASSED → advancing to Tier 2")

        # ── Tier 2: Gemini 3.1 Flash Lite Deep Scan ──
        print(f"   🔹 Tier 2 (Gemini 3.1 Flash Lite Deep Scan)...")
        tier2_score, missing_details = gemini_deep_scan(job["description"], resume_text, client)
        print(f"      Tier 2 Score: {tier2_score}%", end="")

        if tier2_score >= TIER2_THRESHOLD:
            print(f" ✅ QUALIFIED (>={TIER2_THRESHOLD}%)")
            push_to_airtable(job, tier2_score, missing_details, airtable_token, resume_version)
            stats["pushed"] += 1
        else:
            print(f" ❌ REJECTED by AI (below {TIER2_THRESHOLD}%)")
            insert_rejection(supabase, job)
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
