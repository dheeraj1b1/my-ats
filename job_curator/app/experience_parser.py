

# # app/experience_parser.py
# import re
# from typing import Tuple, Optional


# def extract_experience_years(text: str) -> Tuple[Optional[int], Optional[int]]:
#     t = (text or "").lower()
#     t = t.replace("–", "-").replace("—", "-")

#     # 1. "3 to 5 years"
#     m = re.search(r'(\d+)\s*to\s*(\d+)\s*y', t)
#     if m:
#         return int(m.group(1)), int(m.group(2))

#     # 2. "3-5 years"
#     m = re.search(r'(\d+)\s*-\s*(\d+)\s*y', t)
#     if m:
#         return int(m.group(1)), int(m.group(2))

#     # 3. "3+ years"
#     m = re.search(r'(\d+)\+\s*y', t)
#     if m:
#         return int(m.group(1)), None

#     # 4. "3 years"
#     m = re.search(r'(\d+)\s*y', t)
#     if m:
#         v = int(m.group(1))
#         return v, v

#     return None, None


# app/experience_parser.py
import re
from typing import Tuple, Optional


def extract_experience_years(text: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Parses experience ranges.
    Returns (min_exp, max_exp).
    Example: "3-5 years" -> (3, 5). "4+ years" -> (4, None).
    """
    t = (text or "").lower()
    t = t.replace("–", "-").replace("—", "-")  # Normalize dashes

    # 1. "3 to 5 years"
    m = re.search(r'(\d+)\s*to\s*(\d+)\s*y', t)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 2. "3-5 years" or "3 - 5 years"
    m = re.search(r'(\d+)\s*-\s*(\d+)\s*y', t)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 3. "3+ years"
    m = re.search(r'(\d+)\+\s*y', t)
    if m:
        return int(m.group(1)), None

    # 4. "Minimum 3 years" or just "3 years"
    m = re.search(r'(?:min|minimum|at least)?\s*(\d+)\s*y', t)
    if m:
        v = int(m.group(1))
        return v, v

    return None, None
