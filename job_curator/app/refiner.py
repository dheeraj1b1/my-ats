# app/refiner.py
import re
from datetime import datetime
from app.config import (
    IGNORE_DOMAINS, ACCEPTED_ROLES, PREFIXES_TO_STRIP,
    INDIAN_CITIES, FOREIGN_LOCATIONS
)


def format_experience(exp_min: int, exp_max: int) -> str:
    """
    Normalizes experience display for the final Excel.
    Caps upper limit at 5 to match the tool's specific focus range.
    """
    # Rule 5: Fallback if None
    if exp_min is None:
        return "1 – 5 yrs"

    # Rule 2: Cap max experience at 5
    effective_max = exp_max
    if effective_max is not None and effective_max > 5:
        effective_max = 5

    # Rule 1: If max is None (e.g., "3+ years"), default top to 5
    if effective_max is None:
        effective_max = 5

    # Rule 3: Single value display
    if exp_min == effective_max:
        return f"{exp_min} yrs"

    # Rule 4: Standard range display
    return f"{exp_min} – {effective_max} yrs"


def refine_job_batch(raw_jobs: list) -> list:
    """
    Stage 2: Transforms raw blocks into Final Master Tracker rows.
    """
    refined = []

    for job in raw_jobs:
        if job.get("status") != "Selected":
            continue

        raw_text = job.get("Raw_Text", "")

        # 1. Email Extraction & Filtering
        valid_email = extract_valid_email(raw_text)

        # 2. Company Extraction (Priority Logic)
        company = extract_company(raw_text, valid_email)

        # 3. Role Normalization
        role = extract_role(raw_text)

        # 4. Location & Mode
        location = extract_location(raw_text)
        mode = extract_mode(raw_text, location)

        # 5. Experience Normalization
        exp_display = format_experience(job.get('Exp_Min'), job.get('Exp_Max'))

        entry = {
            "S.No": len(refined) + 1,
            "Company": company,
            "Role": role,
            "Exp": exp_display,  # Updated to use normalized helper
            "Location": location,
            "Mode": mode,
            "Email": valid_email,
            "Source_PDF": job.get("Source_PDF"),
            "Notes": generate_tech_notes(raw_text),
            "Domain": extract_domain(raw_text),
            "Last Updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        refined.append(entry)

    return refined

# --- HELPERS ---


def extract_valid_email(text: str) -> str:
    """
    Extracts the best contact email based on priority rules.
    Priority 1: Corporate/Company Domain (Immediate Return)
    Priority 2: Allowed Generic (Gmail/Outlook) - (Fallback)
    Priority 3: Blocked (JobCurator/Telegram/WhatsApp) - (Ignored)
    """
    # Regex to find all potential email addresses
    emails = re.findall(
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)

    fallback_candidates = []

    # Priority 3: Strictly Blocked Patterns (never use)
    blocked_keywords = ["jobcurator", "telegram",
                        "whatsapp", "noreply", "donotreply"]

    # Priority 2: Allowed Generic Domains (use only if no corporate email exists)
    allowed_generic_domains = {
        "gmail.com", "outlook.com", "yahoo.com",
        "hotmail.com", "rediffmail.com", "icloud.com"
    }

    for email in emails:
        domain = email.split('@')[1].lower()

        # 1. CHECK PRIORITY 3 (BLOCKED)
        if any(keyword in domain for keyword in blocked_keywords):
            continue

        # 2. CHECK PRIORITY 2 (ALLOWED GENERIC)
        if domain in allowed_generic_domains:
            fallback_candidates.append(email)
            continue

        # 3. CHECK PRIORITY 1 (CORPORATE)
        # If it's not blocked and not generic, we assume it's a priority corporate email.
        # We prefer the first corporate email found.
        return email

    # 4. FALLBACK SELECTION
    if fallback_candidates:
        return fallback_candidates[0]

    return "Apply via Company Portal"


def extract_company(text: str, email: str, filename: str = "") -> str:
    """
    Extracts Company Name based on strict priority:
    1. Email Domain (if corporate)
    2. Text Patterns ("Hiring for X", "Client: X")
    3. Filename Heuristic (Strips locations/numbers)
    4. Fallback
    """
    # --- PRIORITY 1: Email Domain Inference ---
    if email and "apply via company portal" not in email.lower():
        try:
            domain = email.split('@')[1]
            # Remove common TLDs to get the base name
            name = domain.split('.')[0]
            if len(name) > 2:
                return name.title()
        except IndexError:
            pass

    # --- PRIORITY 2: Text-Based Patterns ---
    # We look for Capitalized sequences associated with hiring phrases
    patterns = [
        # "Hiring for Zensar"
        r"(?:Hiring for|Client[:\-])\s+([A-Z][a-z0-9]+(?:\s[A-Z][a-z0-9]+)*)",
        # "Zensar is hiring"
        r"([A-Z][a-z0-9]+(?:\s[A-Z][a-z0-9]+)*)\s+(?:is hiring|is looking for)",
        # "Zensar Technologies"
        r"([A-Z][a-z0-9]+)\s+(?:Pvt\.?\s*Ltd|Technologies|Solutions|Systems|Private\s*Limited)"
    ]

    # Generic words to ignore if regex captures them
    ignore_words = {"The", "A", "An", "This", "Our",
                    "Client", "Company", "Organization", "We", "You"}

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            candidate = m.group(1).strip()
            # Validation: Length > 2 and not a generic word
            if len(candidate) > 2 and candidate.title() not in ignore_words:
                return candidate.title()

    # --- PRIORITY 3: PDF Filename Heuristic ---
    if filename:
        # Normalize: Remove extension, replace separators with space
        clean_name = filename.rsplit('.', 1)[0]
        clean_name = re.sub(r'[_\-]', ' ', clean_name)

        tokens = clean_name.split()
        filtered_tokens = []

        # Build strict ignore list (Cities + Common junk)
        # Assuming INDIAN_CITIES and FOREIGN_LOCATIONS are imported from config
        locations_lower = {loc.lower()
                           for loc in INDIAN_CITIES + FOREIGN_LOCATIONS}
        junk_lower = {"resume", "cv", "job", "jobs",
                      "jd", "hiring", "opening", "profile"}

        for token in tokens:
            t_lower = token.lower()
            # Filter out numbers, locations, and junk words
            if (not token.isdigit() and
                t_lower not in locations_lower and
                    t_lower not in junk_lower):
                filtered_tokens.append(token)

        if filtered_tokens:
            return " ".join(filtered_tokens).title()

    # --- PRIORITY 4: Fallback ---
    return "Confidential / Client via Consultancy"


def extract_role(text: str) -> str:
    text_lower = text.lower()

    # Find longest matching role
    best_match = ""
    for role in ACCEPTED_ROLES:
        if role in text_lower:
            if len(role) > len(best_match):
                best_match = role

    if not best_match:
        return "QA / SDET"

    # Strip prefixes from the FOUND role context?
    # Actually, we map the accepted keyword to a Title Case string.
    # But if text says "Senior QA Engineer", best_match is "QA Engineer".
    # The prompt says "Strip prefixes". So if we found "QA Engineer", we just return that.
    # We DO NOT prepend "Senior".

    return best_match.title()


def extract_location(text: str) -> str:
    t = text.lower()
    locs = set()

    # Check Foreign
    for f_loc in FOREIGN_LOCATIONS:
        if f_loc.lower() in t:
            locs.add(f_loc)

    # Check Indian Cities
    for city in INDIAN_CITIES:
        if city.lower() in t:
            locs.add(city)

    if "pan india" in t:
        locs.add("Pan India")
    elif "remote" in t and not locs:
        locs.add("Remote")

    if not locs:
        return "Not Specified"

    return ", ".join(sorted(locs))


def extract_mode(text: str, location_str: str) -> str:
    t = text.lower()
    if "remote" in t and "hybrid" not in t:
        return "Remote"
    if "hybrid" in t:
        return "Hybrid"
    if "wfo" in t or "work from office" in t or "on-site" in t:
        return "Work From Office"

    # Heuristic: If location is "Remote", mode is Remote
    if "Remote" in location_str:
        return "Remote"

    return "Full-time"


def extract_domain(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["bank", "fintech", "payment", "financial"]):
        return "FinTech"
    if any(k in t for k in ["health", "medical", "pharma"]):
        return "Healthcare"
    if any(k in t for k in ["ecommerce", "retail", "shopping"]):
        return "E-commerce"
    if "saas" in t:
        return "SaaS"
    return "IT Services"


def generate_tech_notes(text: str) -> str:
    """
    Extracts top 4 skills based on strict priority order.
    Returns 'Skill1 + Skill2...' or 'QA Role' if none found.
    """
    t = text.lower()

    # Priority Order: (Output Format, Search Keyword)
    # The order of this list enforces the priority requirement.
    priority_map = [
        ("Java", "java"),
        ("Python", "python"),
        ("Selenium", "selenium"),
        ("API", "api"),
        ("Manual", "manual"),
        ("SQL", "sql"),
        ("Appium", "appium"),
        ("Playwright", "playwright")
    ]

    found_skills = []

    for display_name, keyword in priority_map:
        if keyword in t:
            found_skills.append(display_name)

            # STRICT REQUIREMENT: Max 4 skills
            if len(found_skills) >= 4:
                break

    if not found_skills:
        return "QA Role"

    return " + ".join(found_skills)
