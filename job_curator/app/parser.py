# # app/parser.py
# import pdfplumber
# import io
# import re


# def extract_blocks_from_pdf(file_bytes: bytes, filename: str) -> list[str]:
#     """
#     Extracts text and splits it into logical 'Job Blocks'.
#     Strategy:
#     1. Extract full text.
#     2. Split by common visual delimiters (===, ---, ___).
#     3. If no delimiters, return full text as 1 block.
#     """
#     full_text = ""
#     try:
#         with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
#             text_pages = []
#             for page in pdf.pages:
#                 extracted = page.extract_text()
#                 if extracted:
#                     text_pages.append(extracted)
#             full_text = "\n".join(text_pages)
#     except Exception as e:
#         print(f"[ERROR] Failed to parse {filename}: {e}")
#         return []

#     if not full_text.strip():
#         return []

#     # --- BLOCK SPLITTING STRATEGY ---
#     # Look for sequences of 3+ equals, dashes, or underscores
#     # Examples: "======", "------", "_______"
#     # We use a regex split to capture these boundaries.

#     # Pattern: Newline + (3 or more =, -, or _) + Newline
#     # We allow optional whitespace around the delimiter
#     delimiter_pattern = r'\n\s*[=\-_]{3,}\s*\n'

#     blocks = re.split(delimiter_pattern, full_text)

#     # Filter out empty or too-short blocks (noise)
#     valid_blocks = [b.strip() for b in blocks if len(b.strip()) > 50]

#     print(f"[DEBUG] {filename}: Extracted {len(valid_blocks)} blocks.")
#     return valid_blocks


# app/parser.py
import pdfplumber
import io
import re


def extract_blocks_from_pdf(file_bytes: bytes, filename: str) -> list[str]:
    """
    Splits PDF text into logical job blocks using visual delimiters.
    """
    full_text = ""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text_pages = []
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text_pages.append(extracted)
            full_text = "\n".join(text_pages)
    except Exception as e:
        print(f"[ERROR] Failed to parse {filename}: {e}")
        return []

    if not full_text.strip():
        return []

    # Delimiters: 3+ equals, dashes, or underscores (e.g., ===, ---, ___)
    delimiter_pattern = r'\n\s*[=\-_]{3,}\s*\n'

    blocks = re.split(delimiter_pattern, full_text)

    # Filter noise (blocks too short to be a JD)
    valid_blocks = [b.strip() for b in blocks if len(b.strip()) > 50]

    return valid_blocks
