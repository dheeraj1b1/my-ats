# Job Curator (Deterministic)

A private, single-user tool to curate job listings from PDF compilations. It parses PDFs, applies deterministic filtering rules (QA/SDET focus), and maintains a single "Master Tracker" Excel file with intelligent deduplication.

## Features
- **PDF Parsing:** Extracts job blocks from multiple PDFs.
- **Smart Filtering:** Accepts 1-5 years exp, QA roles; rejects freshers, tool-only roles.
- **Refinement:** Auto-extracts Company, Email, Role, and Location.
- **Append Mode:** Upload your previous Master Excel to append new unique jobs without duplicates.
- **Privacy:** No database. All processing happens in memory.

## Local Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_USERNAME/job-curator.git](https://github.com/YOUR_USERNAME/job-curator.git)
   cd job-curator