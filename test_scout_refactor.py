"""
test_scout_refactor.py — Dry-run tests for scout.py refactor improvements.
No network/API calls. Duplicates pure config/logic from scout.py to avoid
importing heavy dependencies (bs4, supabase, genai).
"""

# ── Replicate the pure helpers & config from scout.py ────────────────────────

COMPANY_STRIP_SUFFIXES = [
    "private limited", "pvt ltd", "ltd", "inc", "llc",
    "solutions", "technologies", "tech", "services", "india", "group",
]

def normalize_company(name: str) -> str:
    name = name.lower().strip().rstrip(".,;:")
    changed = True
    while changed:
        changed = False
        for suffix in COMPANY_STRIP_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip().rstrip(".,;:")
                changed = True
    return name

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

# ── 1. normalize_company ─────────────────────────────────────────────────────

class TestNormalizeCompany:
    def test_basic_lowercase_and_strip(self):
        assert normalize_company("  Google  ") == "google"

    def test_trailing_punctuation(self):
        assert normalize_company("Acme Corp.,;:") == "acme corp"

    def test_single_suffix(self):
        assert normalize_company("Infosys Ltd") == "infosys"

    def test_multiple_suffixes(self):
        assert normalize_company("VidPro HR Solutions Pvt Ltd") == "vidpro hr"

    def test_private_limited(self):
        assert normalize_company("TCS Private Limited") == "tcs"

    def test_combo_suffixes_with_punctuation(self):
        assert normalize_company("Wipro Technologies India.") == "wipro"

    def test_idempotent_clean_name(self):
        assert normalize_company("google") == "google"

    def test_empty_string(self):
        assert normalize_company("") == ""

    def test_only_suffix(self):
        assert normalize_company("Tech") == ""


# ── 2. Recruiter Blacklist ───────────────────────────────────────────────────

class TestRecruiterBlacklist:
    def test_vidpro_with_suffixes(self):
        assert normalize_company("VidPro HR Solutions Pvt Ltd") in RECRUITER_BLACKLIST

    def test_clean_blacklisted(self):
        assert normalize_company("Qualitest") in RECRUITER_BLACKLIST

    def test_legitimate_company_not_blocked(self):
        assert normalize_company("Google") not in RECRUITER_BLACKLIST


# ── 3. Seniority Filter ─────────────────────────────────────────────────────

class TestSeniorityFilter:
    def test_reject_junior(self):
        title = "Junior QA Engineer"
        assert any(kw in title.lower() for kw in SENIORITY_REJECT)

    def test_reject_associate_engineer(self):
        title = "Associate Engineer — Testing"
        assert any(kw in title.lower() for kw in SENIORITY_REJECT)

    def test_reject_intern(self):
        title = "Intern - Software Testing"
        assert any(kw in title.lower() for kw in SENIORITY_REJECT)

    def test_safe_associate_vp(self):
        title = "Associate Vice President"
        assert not any(kw in title.lower() for kw in SENIORITY_REJECT)

    def test_safe_senior_sdet(self):
        title = "Senior SDET"
        assert not any(kw in title.lower() for kw in SENIORITY_REJECT)

    def test_safe_associate_director(self):
        title = "Associate Director of Engineering"
        assert not any(kw in title.lower() for kw in SENIORITY_REJECT)


# ── 5. Domain Reject ────────────────────────────────────────────────────────

class TestDomainReject:
    def test_gaming_flagged(self):
        jd = "We are a gaming company building next-gen experiences."
        assert any(kw in jd.lower() for kw in DOMAIN_REJECT_KEYWORDS)

    def test_real_estate_flagged(self):
        jd = "PropTech leader in real estate automation."
        assert any(kw in jd.lower() for kw in DOMAIN_REJECT_KEYWORDS)

    def test_defence_flagged(self):
        jd = "Working on defence systems and secure platforms."
        assert any(kw in jd.lower() for kw in DOMAIN_REJECT_KEYWORDS)

    def test_cloud_saas_safe(self):
        jd = "Cloud-native SaaS platform for enterprise collaboration."
        assert not any(kw in jd.lower() for kw in DOMAIN_REJECT_KEYWORDS)

    def test_fintech_safe(self):
        jd = "Fintech startup building payment processing APIs."
        assert not any(kw in jd.lower() for kw in DOMAIN_REJECT_KEYWORDS)
