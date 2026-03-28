# # app/config.py

# # =========================
# # ROLE INCLUSION (CASE-INSENSITIVE)
# # =========================
# ACCEPTED_ROLES = [
#     "qa engineer", "sdet", "test analyst", "test engineer",
#     "automation tester", "manual tester", "api tester", "mobile tester",
#     "database tester", "qc analyst", "performance tester",
#     "quality assurance", "quality analyst", "software test engineer",
#     "software tester"
# ]

# # =========================
# # REQUIRED TECH (Safeguards)
# # =========================
# REQUIRED_TECH = [
#     "selenium", "java", "testng", "maven", "jenkins",
#     "rest assured", "restassured", "postman", "api testing",
#     "sql", "manual testing", "functional testing",
#     "regression testing", "jmeter", "loadrunner", "gatling",
#     "bdd", "kafka", "appium", "ci/cd", "webdriverio",
#     "cypress", "playwright"
# ]

# # =========================
# # EXCLUSION LISTS
# # =========================

# # 1. CONDITIONAL TECH: Reject ONLY if appearing WITHOUT any REQUIRED_TECH
# CONDITIONAL_TECH_EXCLUSIONS = [
#     "python", "playwright", "cypress", "tosca",
#     "robot framework", "rpa", "uipath"
# ]

# # 2. HARD TECH: Reject ALWAYS (Non-QA roles)
# HARD_TECH_EXCLUSIONS = [
#     "salesforce developer", "dotnet developer", "java developer",
#     "data scientist", "data engineer"
# ]

# # 3. HIRING MODE: Reject if these phrases appear (unless negated)
# HIRING_EXCLUSIONS = [
#     "walk-in", "walk in", "face-to-face", "face to face",
#     "drive", "hiring event", "on-site hiring"
# ]

# # =========================
# # EMPLOYMENT & EXPERIENCE
# # =========================
# EMPLOYMENT_ALLOWED = ["permanent", "full time",
#                       "full-time", "immediate joiner"]
# EMPLOYMENT_EXCLUSIONS = ["contract", "c2h", "contract to hire",
#                          "freelance", "internship", "intern", "trainee"]

# ALLOW_IMMEDIATE_JOINER = True
# MIN_EXP_REQUIRED = 1
# MAX_EXP_ALLOWED = 5
# MAX_UPLOAD_FILES = 6

# app/config.py

# =========================
# ROLE DEFINITIONS
# =========================
ACCEPTED_ROLES = [
    "qa engineer", "sdet", "test analyst", "test engineer",
    "automation tester", "manual tester", "api tester", "mobile tester",
    "database tester", "qc analyst", "performance tester",
    "software test engineer", "software tester", "quality assurance",
    "quality analyst", "automation engineer", "test lead", "qa lead"
]

PREFIXES_TO_STRIP = [
    "senior", "sr.", "sr ", "junior", "jr.", "jr ",
    "lead", "trainee", "intern", "principal", "associate"
]

# =========================
# TECH STACK
# =========================
REQUIRED_TECH = [
    "selenium", "java", "testng", "maven", "jenkins",
    "rest assured", "restassured", "postman", "api testing",
    "sql", "manual testing", "functional testing",
    "regression testing", "jmeter", "loadrunner", "gatling",
    "bdd", "kafka", "appium", "ci/cd", "webdriverio"
]

# Safeguard: These are rejected ONLY if appearing without any REQUIRED_TECH
CONDITIONAL_TECH_EXCLUSIONS = [
    "python", "playwright", "cypress", "tosca",
    "robot framework", "rpa", "uipath"
]

HARD_TECH_EXCLUSIONS = [
    "salesforce developer", "dotnet developer", "java developer",
    "data scientist", "data engineer", "full stack developer",
    "frontend developer", "backend developer"
]

# =========================
# EXCLUSIONS
# =========================
# Reject if "Walk-in" is found AND it's not negated (handled in rules.py)
HIRING_EXCLUSIONS = [
    "walk-in", "walk in", "face-to-face", "face to face",
    "drive", "hiring event"
]

EMPLOYMENT_EXCLUSIONS = [
    "contract", "c2h", "contract to hire",
    "freelance", "internship", "intern", "trainee"
]

# =========================
# EXPERIENCE LOGIC
# =========================
# "4-9 years" is ACCEPTED because start (4) <= 5
# "6-10 years" is REJECTED because start (6) > 5
MAX_START_EXP_ALLOWED = 5  # The absolute limit for exp_min
MIN_EXP_REQUIRED = 1       # Reject freshers

# =========================
# EXTRACTION SETTINGS
# =========================
IGNORE_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "jobcurator.in", "telegram.org", "jobcurator.com", "rediffmail.com"
}

INDIAN_CITIES = [
    "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Pune",
    "Mumbai", "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata",
    "Ahmedabad", "Thiruvananthapuram", "Kochi", "Indore", "Jaipur",
    "Chandigarh", "Coimbatore"
]

FOREIGN_LOCATIONS = [
    "USA", "United States", "UK", "London", "Canada", "Singapore",
    "Dubai", "UAE", "Australia", "Germany", "Remote - US"
]

MAX_UPLOAD_FILES = 6
