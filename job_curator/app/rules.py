# # app/rules.py
# import re
# from app.config import (
#     ACCEPTED_ROLES, REQUIRED_TECH, CONDITIONAL_TECH_EXCLUSIONS,
#     HARD_TECH_EXCLUSIONS, HIRING_EXCLUSIONS, EMPLOYMENT_EXCLUSIONS,
#     MIN_EXP_REQUIRED, MAX_START_EXP_ALLOWED
# )


# def evaluate_job_block(text: str, exp_min: int, exp_max: int) -> dict:
#     t = text.lower()
#     logs = []

#     # --- 1. ROLE CHECK ---
#     # We accept if ANY accepted role keyword is present.
#     # Refiner will normalize the title later.
#     if not any(role in t for role in ACCEPTED_ROLES):
#         # Fallback for generic QA terms
#         if not any(k in t for k in ["qa", "quality", "test", "sdet"]):
#             logs.append("Role: No valid QA/SDET role found.")
#             return _reject("Role Mismatch", logs)
#     logs.append("Role: Valid keyword found.")

#     # --- 2. HARD TECH EXCLUSION ---
#     for excl in HARD_TECH_EXCLUSIONS:
#         if excl in t:
#             logs.append(f"Exclusion: Found hard block '{excl}'")
#             return _reject(f"Hard Exclusion ({excl})", logs)

#     # --- 3. CONDITIONAL TECH EXCLUSION ---
#     # Reject "Python" ONLY if no safe tech (Selenium/Java/Manual) is present
#     bad_techs = [excl for excl in CONDITIONAL_TECH_EXCLUSIONS if excl in t]
#     if bad_techs:
#         has_safeguard = any(req in t for req in REQUIRED_TECH)
#         if not has_safeguard:
#             logs.append(
#                 f"Exclusion: '{bad_techs[0]}' found without safeguards.")
#             return _reject(f"Tool-Only Exclusion ({bad_techs[0]})", logs)
#         else:
#             logs.append(
#                 f"Safeguard: '{bad_techs[0]}' allowed due to required tech.")

#     # --- 4. HIRING MODE (Context Aware) ---
#     # Reject "Walk-in" only if NOT negated ("No Walk-in")
#     for term in HIRING_EXCLUSIONS:
#         if term in t:
#             # Negative lookbehind: matches term if NOT preceded by "no " or "not "
#             pattern = fr'(?<!no\s)(?<!not\s){re.escape(term)}'
#             if re.search(pattern, t):
#                 logs.append(f"Exclusion: Found '{term}'.")
#                 return _reject(f"Hiring Mode ({term})", logs)

#     # --- 5. EMPLOYMENT TYPE ---
#     for excl in EMPLOYMENT_EXCLUSIONS:
#         if excl in t:
#             logs.append(f"Exclusion: Found '{excl}'.")
#             return _reject(f"Employment Type ({excl})", logs)

#     # --- 6. EXPERIENCE LOGIC (STRICT) ---
#     if exp_min is None:
#         logs.append("Exp: None found.")
#         return _reject("No Experience Found", logs)

#     # Rule A: Reject Freshers
#     if exp_min < MIN_EXP_REQUIRED:
#         logs.append(f"Exp: Too low ({exp_min} < {MIN_EXP_REQUIRED}).")
#         return _reject("Fresher/Low Exp", logs)

#     # Rule B: Reject Senior Starts
#     # We only care about the START of the range.
#     # 4-9 years -> Start is 4. 4 <= 5. ACCEPT.
#     # 6-10 years -> Start is 6. 6 > 5. REJECT.
#     if exp_min > MAX_START_EXP_ALLOWED:
#         logs.append(
#             f"Exp: Starts too high ({exp_min} > {MAX_START_EXP_ALLOWED}).")
#         return _reject("Senior/High Exp", logs)

#     logs.append(f"Exp: Valid ({exp_min}-{exp_max}).")
#     return {"status": "Selected", "reason": "Matches Criteria", "debug_log": logs}


# def _reject(reason, logs):
#     return {"status": "Rejected", "reason": reason, "debug_log": logs}

# app/rules.py
import re
from app.config import (
    ACCEPTED_ROLES, REQUIRED_TECH, CONDITIONAL_TECH_EXCLUSIONS,
    HARD_TECH_EXCLUSIONS, HIRING_EXCLUSIONS, EMPLOYMENT_EXCLUSIONS,
    MIN_EXP_REQUIRED, MAX_START_EXP_ALLOWED
)


def evaluate_job_block(text: str, exp_min: int, exp_max: int) -> dict:
    """
    Evaluates a specific text block against Master Rules.
    Strictly deterministic Stage-1 evaluation.
    """
    t = text.lower()
    logs = []

    # --- 1. ROLE RELEVANCE (STRICT) ---
    # Must match one of the explicitly allowed roles in app/config.py
    # Removed generic fallback to prevent loose matches.
    if not any(role in t for role in ACCEPTED_ROLES):
        logs.append("Role: No valid QA/SDET specific keyword found.")
        return _reject("Role Mismatch (Strict)", logs)

    logs.append("Role: Valid keyword found.")

    # --- 2. HARD TECH EXCLUSION ---
    # Reject Developer, DevOps, Data, etc.
    for excl in HARD_TECH_EXCLUSIONS:
        if excl in t:
            logs.append(f"Exclusion: Found prohibited term '{excl}'")
            return _reject(f"Hard Exclusion ({excl})", logs)

    # --- 3. CONDITIONAL TECH EXCLUSION (Tool-Only) ---
    # Reject Python/Playwright/etc. ONLY if no Safe Tech (Java/Selenium) exists
    bad_techs = [excl for excl in CONDITIONAL_TECH_EXCLUSIONS if excl in t]
    if bad_techs:
        has_safeguard = any(req in t for req in REQUIRED_TECH)
        if not has_safeguard:
            logs.append(
                f"Exclusion: '{bad_techs[0]}' found without safeguards.")
            return _reject(f"Tool-Only Exclusion ({bad_techs[0]})", logs)
        else:
            logs.append(
                f"Safeguard: '{bad_techs[0]}' allowed due to required tech.")

    # --- 4. REQUIRED TECHNOLOGY (AT LEAST ONE) ---
    # Must have Selenium, Java, SQL, Manual, etc.
    if not any(req in t for req in REQUIRED_TECH):
        logs.append(
            "Tech: No required tech stack found (Selenium/Java/Manual/API/SQL).")
        return _reject("Missing Required Tech", logs)

    # --- 5. HIRING MODE (Context Aware) ---
    # Reject Walk-in/Drive unless negated ("No Walk-in")
    for term in HIRING_EXCLUSIONS:
        if term in t:
            # Negative lookbehind: matches term if NOT preceded by "no " or "not "
            pattern = fr'(?<!no\s)(?<!not\s){re.escape(term)}'
            if re.search(pattern, t):
                logs.append(f"Exclusion: Found hiring mode '{term}'.")
                return _reject(f"Hiring Mode ({term})", logs)

    # --- 6. EMPLOYMENT TYPE ---
    # Reject Contract, Internship, etc.
    for excl in EMPLOYMENT_EXCLUSIONS:
        if excl in t:
            logs.append(f"Exclusion: Found employment type '{excl}'.")
            return _reject(f"Employment Type ({excl})", logs)

    # --- 7. EXPERIENCE LOGIC (LOWER BOUND DOMINANCE) ---
    if exp_min is None:
        logs.append("Exp: None found.")
        return _reject("No Experience Found", logs)

    # Rule A: Reject Freshers (e.g. 0-1 years)
    if exp_min < MIN_EXP_REQUIRED:
        logs.append(f"Exp: Too low ({exp_min} < {MIN_EXP_REQUIRED}).")
        return _reject(f"Fresher/Low Exp ({exp_min} yr)", logs)

    # Rule B: Reject Senior Starts (>5 years)
    # Logic: 4-9 is Accepted (4 <= 5). 6-10 is Rejected (6 > 5).
    # This automatically filters "Senior/Lead" roles if their requirements exceed 5 years.
    if exp_min > MAX_START_EXP_ALLOWED:
        logs.append(
            f"Exp: Starts too high ({exp_min} > {MAX_START_EXP_ALLOWED}).")
        return _reject(f"Senior/High Exp (Start > {MAX_START_EXP_ALLOWED})", logs)

    logs.append(f"Exp: Valid range ({exp_min}-{exp_max}).")
    return {"status": "Selected", "reason": "Matches Criteria", "debug_log": logs}


def _reject(reason, logs):
    return {"status": "Rejected", "reason": reason, "debug_log": logs}
