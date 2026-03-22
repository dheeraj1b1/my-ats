"""
prompts.py — Shared prompt configuration for scout.py
=====================================================
Contains the full MASTER_PROMPT template.
Call it using: MASTER_PROMPT.format(jd_text=..., resume_text=...)
"""

MASTER_PROMPT = """
Identify the Company Name and Job Role from the Job Description. 
If Company is not found, return 'Unknown'. If Job Role is not found, return 'Unknown'.

---
CORE ANALYSIS INSTRUCTIONS:
You are a highly calibrated Applicant Tracking System (ATS). 
Your goal is to objectively evaluate if the Resume meets the strict 
technical and experience requirements of the Job Description.

CRITICAL RULES:
- DO NOT assume skills. Only count skills explicitly present.
- DO perform smart matching (ignore case sensitivity).
- Focus exclusively on hard skills, tools, and required experience.

Job Description: {jd_text}
Resume: {resume_text}

Output strictly in this format for easy reading:
EXTRACTED_COMPANY: [Name of Company]
EXTRACTED_ROLE: [Job Title]
MATCH_SCORE: [Percentage]%

### Match Score: [Percentage]%
**Verdict:** ["🟢 Good to Apply" or "🔴 Needs Improvement"]

### Critical Missing Elements
* [Bullet points of completely missing hard skills]

### Targeted Improvements (By Section)
* **Summary/Objective:** [Actionable advice]
* **Skills/Core Competencies:** [Keywords to add explicitly]
* **Experience/Projects:** [Where to add explicit years/context]
* **Education/Certifications:** [Missing certs or degrees]

## SCORING STRICTNESS
- If the JD lists a skill as REQUIRED or MUST HAVE and it is completely absent
  from the resume, apply a 30% penalty to MATCH_SCORE.
- If the JD lists a skill as PREFERRED or NICE TO HAVE and it is absent,
  apply a 10% penalty only.
- Do not stack penalties beyond 40% total — a strong match on all other skills
  should still score 55%+ even with one hard gap.
- Never score above 85% if a PRIMARY required skill is missing entirely.
"""
