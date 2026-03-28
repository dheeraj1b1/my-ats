# from fastapi import FastAPI, UploadFile, File, HTTPException, Request
# from fastapi.responses import StreamingResponse, HTMLResponse
# from fastapi.staticfiles import StaticFiles
# from fastapi.templating import Jinja2Templates
# from typing import List, Optional
# import pandas as pd
# import os

# from app.config import MAX_UPLOAD_FILES
# from app.parser import extract_blocks_from_pdf
# from app.experience_parser import extract_experience_years
# from app.rules import evaluate_job_block
# from app.refiner import refine_job_batch
# from app.dedup import load_previous_df, get_start_sno, get_existing_keys, is_duplicate
# from app.excel_writer import generate_multi_output

# app = FastAPI(title="Job Curator (UI Enabled)")

# # --- UI CONFIGURATION ---
# # Ensure directories exist
# os.makedirs("app/static", exist_ok=True)
# os.makedirs("app/templates", exist_ok=True)

# # Mount Static Files (CSS)
# app.mount("/static", StaticFiles(directory="app/static"), name="static")

# # Initialize Templates (HTML)
# templates = Jinja2Templates(directory="app/templates")


# @app.get("/", response_class=HTMLResponse)
# async def read_root(request: Request):
#     """Serve the single-page UI."""
#     return templates.TemplateResponse("index.html", {"request": request})

# # --- BACKEND LOGIC ---


# @app.post("/process")
# async def process_jobs(
#     files: List[UploadFile] = File(...),
#     previous_excel: Optional[UploadFile] = File(None)
# ):
#     # Validate PDF File Count
#     pdf_files = [f for f in files if f.filename.lower().endswith('.pdf')]
#     if len(pdf_files) > MAX_UPLOAD_FILES:
#         raise HTTPException(
#             status_code=400, detail=f"Too many PDFs. Max {MAX_UPLOAD_FILES} allowed.")

#     if not pdf_files:
#         raise HTTPException(
#             status_code=400, detail="No valid PDF files uploaded.")

# # --- PREPARE APPEND MODE DATA ---
#     previous_df = pd.DataFrame()
#     start_sno = 1
#     existing_keys = set()

#     # FIX: Check if previous_excel exists AND has a filename (avoids error on empty upload)
#     if previous_excel and previous_excel.filename:
#         if not previous_excel.filename.lower().endswith('.xlsx'):
#             raise HTTPException(
#                 status_code=400, detail="Previous file must be an Excel (.xlsx) file.")

#         content = await previous_excel.read()
#         previous_df = load_previous_df(content)
#         start_sno = get_start_sno(previous_df)
#         existing_keys = get_existing_keys(previous_df)

#     # --- STAGE 1: PARSING & DIAGNOSTICS ---
#     stage1_results = []

#     for file in pdf_files:
#         content = await file.read()
#         blocks = extract_blocks_from_pdf(content, file.filename)

#         if not blocks:
#             stage1_results.append({
#                 "Source_PDF": file.filename,
#                 "status": "Error",
#                 "reason": "Unreadable/Empty",
#                 "debug_log": ["Parser found 0 blocks"]
#             })
#             continue

#         for idx, block_text in enumerate(blocks, 1):
#             exp_min, exp_max = extract_experience_years(block_text)
#             evaluation = evaluate_job_block(block_text, exp_min, exp_max)

#             job_entry = {
#                 "Source_PDF": file.filename,
#                 "Block_ID": idx,
#                 "Exp_Min": exp_min,
#                 "Exp_Max": exp_max,
#                 "Raw_Text": block_text,
#                 **evaluation
#             }
#             stage1_results.append(job_entry)

#     # --- STAGE 2: REFINEMENT ---
#     refined_batch = refine_job_batch(stage1_results)

#     # --- DEDUPLICATION & APPEND LOGIC ---
#     final_new_jobs = []
#     current_sno_counter = start_sno

#     for job in refined_batch:
#         if is_duplicate(job, existing_keys):
#             continue

#         job["S.No"] = current_sno_counter
#         current_sno_counter += 1

#         final_new_jobs.append(job)

#         new_key = (
#             str(job.get("Company", "")).strip().lower(),
#             str(job.get("Role", "")).strip().lower(),
#             str(job.get("Email", "")).strip().lower()
#         )
#         existing_keys.add(new_key)

#     # Handle No Jobs Found Case
#     if not final_new_jobs and not previous_excel:
#         # If it's a fresh run and no jobs matched, we still want to give the diagnostic log
#         pass

#     # --- MERGE DATA ---
#     if final_new_jobs:
#         new_df = pd.DataFrame(final_new_jobs)
#         final_master_df = pd.concat([previous_df, new_df], ignore_index=True)
#     else:
#         final_master_df = previous_df

#     # --- OUTPUT ---
#     output_zip = generate_multi_output(stage1_results, final_master_df)

#     return StreamingResponse(
#         output_zip,
#         headers={
#             'Content-Disposition': 'attachment; filename="Job_Curator_Results.zip"'
#         },
#         media_type='application/zip'
#     )

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List, Optional
import pandas as pd
import os
from datetime import datetime

from app.config import MAX_UPLOAD_FILES
from app.parser import extract_blocks_from_pdf
from app.experience_parser import extract_experience_years
from app.rules import evaluate_job_block
from app.refiner import refine_job_batch
from app.dedup import load_previous_df, get_start_sno, get_existing_keys, is_duplicate
from app.excel_writer import generate_master_excel

app = FastAPI(title="Job Curator (Single Excel Output)")

# --- UI CONFIGURATION ---
os.makedirs("app/static", exist_ok=True)
os.makedirs("app/templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the single-page UI."""
    return templates.TemplateResponse("index.html", {"request": request})

# --- BACKEND LOGIC ---


@app.post("/process")
async def process_jobs(
    files: List[UploadFile] = File(...),
    previous_excel: Optional[UploadFile] = File(None)
):
    # Validate PDF File Count
    pdf_files = [f for f in files if f.filename.lower().endswith('.pdf')]
    if len(pdf_files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400, detail=f"Too many PDFs. Max {MAX_UPLOAD_FILES} allowed.")

    if not pdf_files:
        raise HTTPException(
            status_code=400, detail="No valid PDF files uploaded.")

    # --- PREPARE APPEND MODE DATA ---
    previous_df = pd.DataFrame()
    start_sno = 1
    existing_keys = set()

    # Check if previous_excel exists AND has a filename (Day-1 fix logic preserved)
    if previous_excel and previous_excel.filename:
        if not previous_excel.filename.lower().endswith('.xlsx'):
            raise HTTPException(
                status_code=400, detail="Previous file must be an Excel (.xlsx) file.")

        content = await previous_excel.read()
        previous_df = load_previous_df(content)
        start_sno = get_start_sno(previous_df)
        existing_keys = get_existing_keys(previous_df)

    # --- STAGE 1: PARSING & DIAGNOSTICS ---
    stage1_results = []

    for file in pdf_files:
        content = await file.read()
        blocks = extract_blocks_from_pdf(content, file.filename)

        if not blocks:
            # Skip empty files, proceed to next
            continue

        for idx, block_text in enumerate(blocks, 1):
            exp_min, exp_max = extract_experience_years(block_text)
            evaluation = evaluate_job_block(block_text, exp_min, exp_max)

            job_entry = {
                "Source_PDF": file.filename,
                "Block_ID": idx,
                "Exp_Min": exp_min,
                "Exp_Max": exp_max,
                "Raw_Text": block_text,
                **evaluation
            }
            stage1_results.append(job_entry)

    # --- STAGE 2: REFINEMENT ---
    refined_batch = refine_job_batch(stage1_results)

    # --- DEDUPLICATION & APPEND LOGIC ---
    final_new_jobs = []
    current_sno_counter = start_sno

    for job in refined_batch:
        if is_duplicate(job, existing_keys):
            continue

        job["S.No"] = current_sno_counter
        current_sno_counter += 1

        final_new_jobs.append(job)

        new_key = (
            str(job.get("Company", "")).strip().lower(),
            str(job.get("Role", "")).strip().lower(),
            str(job.get("Email", "")).strip().lower()
        )
        existing_keys.add(new_key)

    # --- MERGE DATA ---
    if final_new_jobs:
        new_df = pd.DataFrame(final_new_jobs)
        # Append new jobs to previous dataframe
        final_master_df = pd.concat([previous_df, new_df], ignore_index=True)
    else:
        final_master_df = previous_df

    # --- OUTPUT ---
    output_excel = generate_master_excel(final_master_df)

    date_str = datetime.now().strftime('%Y-%m-%d')
    filename = f"Final_Master_Tracker_{date_str}.xlsx"

    return StreamingResponse(
        output_excel,
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"'
        },
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
