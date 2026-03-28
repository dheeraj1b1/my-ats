import streamlit as st
import pdfplumber
import docx
import google.generativeai as genai
import datetime
import pytz
import requests
import pandas as pd
import re
import os
import base64
import importlib.util
import sys
from pathlib import Path
from email.mime.text import MIMEText
from supabase import create_client, Client

from tailor import (
    fetch_not_applied_jobs,
    parse_docx_sections,
    get_texts,
    build_tailor_prompt,
    generate_with_fallback,
    apply_tailored_sections,
    save_doc_to_bytes,
    is_rate_limited,
    DAILY_LIMIT,
)

# ─── Helper ─────────────────────────────────────────────────────────────────

def extract_text_from_file(file):
    text = ""
    if file.name.endswith(".pdf"):
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
    elif file.name.endswith(".docx"):
        doc = docx.Document(file)
        for para in doc.paragraphs:
            text += para.text + "\n"
    return text


def load_module_from_path(module_name: str, module_path: Path):
    """Load a Python module from a specific file path."""
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@st.cache_resource(show_spinner=False)
def load_job_curator_pipeline():
    """Load job_curator modules without changing files inside job_curator/."""
    job_curator_app_dir = Path(__file__).resolve().parent / "job_curator" / "app"
    if not job_curator_app_dir.exists():
        raise FileNotFoundError(f"Missing directory: {job_curator_app_dir}")

    config_mod = load_module_from_path("app.config", job_curator_app_dir / "config.py")
    parser_mod = load_module_from_path("app.parser", job_curator_app_dir / "parser.py")
    exp_mod = load_module_from_path("app.experience_parser", job_curator_app_dir / "experience_parser.py")
    rules_mod = load_module_from_path("app.rules", job_curator_app_dir / "rules.py")
    refiner_mod = load_module_from_path("app.refiner", job_curator_app_dir / "refiner.py")

    return {
        "max_upload_files": getattr(config_mod, "MAX_UPLOAD_FILES", 6),
        "extract_blocks_from_pdf": parser_mod.extract_blocks_from_pdf,
        "extract_experience_years": exp_mod.extract_experience_years,
        "evaluate_job_block": rules_mod.evaluate_job_block,
        "refine_job_batch": refiner_mod.refine_job_batch,
    }


# ─── Page Config ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="My ATS — Master Studio", page_icon="🧠", layout="wide")

# ─── Sidebar: Credentials (global, always visible) ──────────────────────────

with st.sidebar:
    st.title("🧠 Master Studio")
    st.divider()

    st.subheader("🔑 Credentials")

    # Gemini
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("✅ Gemini Key loaded")
    else:
        api_key = st.text_input("Gemini API Key", type="password", key="sidebar_gemini")
        if not api_key:
            st.warning("⚠️ Enter Gemini key")

    # Airtable
    airtable_base_id = st.text_input(
        "Airtable Base ID",
        value="appABPMwKgXkr8Rgn",
        key="sidebar_airtable_base",
    )
    if "AIRTABLE_TOKEN" in st.secrets:
        airtable_token = st.secrets["AIRTABLE_TOKEN"]
        st.success("✅ Airtable Token loaded")
    else:
        airtable_token = st.text_input("Airtable Token", type="password", key="sidebar_airtable_token")
        if not airtable_token:
            st.warning("⚠️ Enter Airtable token")

    # Supabase
    if "SUPABASE_URL" in st.secrets:
        supabase_url = st.secrets["SUPABASE_URL"]
        st.success("✅ Supabase URL loaded")
    else:
        supabase_url = st.text_input("Supabase URL", key="sidebar_supabase_url")

    if "SUPABASE_KEY" in st.secrets:
        supabase_key = st.secrets["SUPABASE_KEY"]
        st.success("✅ Supabase Key loaded")
    else:
        supabase_key = st.text_input("Supabase Key", type="password", key="sidebar_supabase_key")

    supabase_client: Client | None = None
    if supabase_url and supabase_key:
        try:
            supabase_client = create_client(supabase_url, supabase_key)
        except Exception as e:
            st.sidebar.error(f"Failed to init Supabase client: {e}")

    st.divider()

    # Navigation
    page = st.radio(
        "Navigate",
        [
            "🏠 Command Center",
            "📊 Airtable Tracker",
            "🗄️ Supabase Viewer",
            "✂️ Resume Studio",
            "📄 Job Curator",
            "☁️ Document Vault",
            "📧 Mail Drafter",
        ],
        key="nav_radio",
    )


# ─── Airtable PATCH Helper ───────────────────────────────────────────────────

STATUS_OPTIONS = ["Not Applied", "Applied", "Interviewing", "Rejected"]


def update_airtable_record(base_id: str, token: str, record_id: str, updated_fields: dict):
    """PATCH a single Airtable record to update specific fields."""
    url = f"https://api.airtable.com/v0/{base_id}/Applications/{record_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"fields": updated_fields}
    resp = requests.patch(url, json=payload, headers=headers, timeout=30)
    return resp.status_code == 200, resp.text


def create_airtable_record(base_id: str, token: str, fields: dict):
    """POST a new Airtable record."""
    url = f"https://api.airtable.com/v0/{base_id}/Applications"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"fields": fields}
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    return resp.status_code == 200, resp.text


def delete_airtable_record(base_id: str, token: str, record_id: str):
    """DELETE an Airtable record."""
    url = f"https://api.airtable.com/v0/{base_id}/Applications/{record_id}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.delete(url, headers=headers, timeout=30)
    return resp.status_code == 200, resp.text

# ═════════════════════════════════════════════════════════════════════════════
# 🏠 COMMAND CENTER — Manual Scan & Match + Airtable Logging
# ═════════════════════════════════════════════════════════════════════════════

if page == "🏠 Command Center":
    st.header("🏠 Command Center — Scan & Match")
    st.caption("Paste a JD, upload your resume, get an ATS match score, and log the result to Airtable.")

    col1, col2 = st.columns([1, 1])

    with col1:
        manual_company = st.text_input(
            "Company Name (Optional: AI will detect if blank)",
            placeholder="e.g., Google, Infosys",
            key="cmd_company",
        )
        jd_text = st.text_area("Paste the Job Description here...", height=250, key="cmd_jd")

    with col2:
        uploaded_file = st.file_uploader(
            "Upload your Resume (PDF or DOCX)",
            type=["pdf", "docx"],
            key="cmd_resume_upload",
        )

    if st.button("🚀 Scan & Match", key="cmd_scan_btn"):
        if not api_key:
            st.warning("Please enter your Gemini API Key in the sidebar.")
        elif jd_text and uploaded_file:
            with st.spinner("Scanning..."):
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel("gemini-2.5-flash")
                resume_text = extract_text_from_file(uploaded_file)

                prompt = f"""
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
                """

                try:
                    response = model.generate_content(prompt)
                    full_response = response.text

                    # Extract Company
                    extracted_company = "Unknown"
                    if "EXTRACTED_COMPANY:" in full_response:
                        comp_line = full_response.split("EXTRACTED_COMPANY:")[1]
                        extracted_company = comp_line.split("\n")[0].strip()
                    final_company = manual_company if manual_company else extracted_company

                    # Extract Role with a default fallback
                    extracted_role = "QA Engineer"
                    if "EXTRACTED_ROLE:" in full_response:
                        role_line = full_response.split("EXTRACTED_ROLE:")[1]
                        parsed_role = role_line.split("\n")[0].strip()
                        if parsed_role.lower() != "unknown" and parsed_role != "":
                            extracted_role = parsed_role

                    # Extract score strictly as a number for Airtable
                    match_score = 0
                    if "MATCH_SCORE:" in full_response:
                        try:
                            score_str = full_response.split("MATCH_SCORE:")[1].split("%")[0].strip()
                            match_score = int(score_str)
                        except ValueError:
                            match_score = 0

                    if "MATCH_SCORE:" in full_response:
                        clean_display = full_response.split("MATCH_SCORE:")[1].split("\n", 1)[1]
                    else:
                        clean_display = full_response

                    # Display Results
                    st.subheader(f"ATS Results for {final_company} - {extracted_role}")
                    st.markdown(clean_display)

                    # Capture exact IST time for Airtable
                    ist_tz = pytz.timezone("Asia/Kolkata")
                    exact_time = datetime.datetime.now(ist_tz).isoformat()

                    # Save data to session state
                    st.session_state["last_scan"] = {
                        "company": final_company,
                        "role": extracted_role,
                        "jd": jd_text,
                        "score": match_score,
                        "date": exact_time,
                        "resume_name": uploaded_file.name,
                    }

                except Exception as e:
                    st.error(f"Error calling API: {e}")
        else:
            st.warning("Please provide both a Job Description and a Resume.")

    # ── Log to Airtable ──
    st.divider()
    st.subheader("📝 Log Application to Airtable")

    if "last_scan" in st.session_state:
        scan_data = st.session_state["last_scan"]
        current_role = scan_data.get("role", "QA Engineer")

        role_note = "*(Defaulted)*" if current_role == "QA Engineer" else ""
        st.info(
            f"Ready to log: **{scan_data['company']}** | "
            f"Role: **{current_role}** {role_note} | "
            f"Score: **{scan_data['score']}%**"
        )

        if st.button("🚀 Send to Airtable Tracker", key="cmd_airtable_btn"):
            if not airtable_base_id or not airtable_token:
                st.warning("⚠️ Please enter Airtable credentials in the sidebar.")
            else:
                url = f"https://api.airtable.com/v0/{airtable_base_id}/Applications"
                headers = {
                    "Authorization": f"Bearer {airtable_token}",
                    "Content-Type": "application/json",
                }

                data = {
                    "fields": {
                        "Company": scan_data["company"],
                        "Role": current_role,
                        "Match Score": scan_data["score"],
                        "Status": "Not Applied",
                        "Applied Date": scan_data["date"],
                        "JD Description": scan_data["jd"],
                        "Resume Name": scan_data.get("resume_name", "Unknown"),
                    }
                }

                with st.spinner("Logging to Airtable..."):
                    resp = requests.post(url, json=data, headers=headers)
                    if resp.status_code == 200:
                        st.success(f"Successfully logged {scan_data['company']}!")
                        st.balloons()
                    else:
                        st.error(f"Failed to log: {resp.text}")
    else:
        st.info("Run a Scan & Match first to see the logging option here.")


elif page == "📊 Airtable Tracker":
    st.header("📊 Airtable Tracker — All Applications")
    st.caption("Interactive ATS dashboard with two-way Airtable sync.")

    if not airtable_base_id or not airtable_token:
        st.warning("⚠️ Airtable credentials are required. Set them in the sidebar.")
    else:
        # ── Fetch with record_id ──
        @st.cache_data(ttl=120, show_spinner="Fetching Airtable records...")
        def fetch_all_airtable_records(_base_id, _token):
            url = f"https://api.airtable.com/v0/{_base_id}/Applications"
            headers = {"Authorization": f"Bearer {_token}"}
            all_records = []
            offset = None
            while True:
                params = {}
                if offset:
                    params["offset"] = offset
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                for rec in data.get("records", []):
                    fields = rec.get("fields", {})
                    all_records.append({
                        "record_id": rec.get("id", ""),
                        "Company": fields.get("Company", ""),
                        "Role": fields.get("Role", ""),
                        "Match Score": fields.get("Match Score", 0) or 0,
                        "Status": fields.get("Status", "Not Applied"),
                        "Applied Date": fields.get("Applied Date", ""),
                        "Resume Name": fields.get("Resume Name", ""),
                        "JD Description": fields.get("JD Description", ""),
                        "Apply Link": fields.get("Apply Link", ""),
                    })
                offset = data.get("offset")
                if not offset:
                    break
            return all_records

        # ── Controls row ──
        ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([1, 1, 2])
        with ctrl_col1:
            if st.button("🔄 Refresh Data", key="airtable_refresh"):
                fetch_all_airtable_records.clear()
                st.rerun()
        with ctrl_col2:
            view_mode = st.radio(
                "View",
                ["📋 Grid", "📌 Kanban"],
                horizontal=True,
                key="tracker_view_mode",
            )

        try:
            records = fetch_all_airtable_records(airtable_base_id, airtable_token)
        except Exception as e:
            st.error(f"Failed to fetch Airtable records: {e}")
            records = []

        if not records:
            st.info("No records found in Airtable.")
        else:
            df = pd.DataFrame(records)
            df["Match Score"] = pd.to_numeric(df["Match Score"], errors="coerce").fillna(0).astype(int)

            # ── Filters & Sorting ──
            with st.expander("🔍 Filter & Sort Applications", expanded=False):
                f_col1, f_col2, f_col3 = st.columns([2, 2, 1])
                with f_col1:
                    search_query = st.text_input("Search Company or Role", key="tracker_search")
                with f_col2:
                    status_filter = st.multiselect(
                        "Filter by Status", 
                        options=STATUS_OPTIONS, 
                        default=STATUS_OPTIONS,
                        key="tracker_status_filter"
                    )
                with f_col3:
                    sort_by = st.selectbox(
                        "Sort By",
                        options=[
                            "Date (Newest)", 
                            "Date (Oldest)", 
                            "Company (A-Z)", 
                            "Company (Z-A)", 
                            "Score (High-Low)", 
                            "Score (Low-High)"
                        ],
                        index=0,
                        key="tracker_sort"
                    )

            if search_query:
                df = df[
                    df["Company"].str.contains(search_query, case=False, na=False) |
                    df["Role"].str.contains(search_query, case=False, na=False)
                ]
            if status_filter:
                df = df[df["Status"].isin(status_filter)]

            # ── Sorting ──
            if sort_by == "Date (Newest)":
                df = df.sort_values(by="Applied Date", ascending=False)
            elif sort_by == "Date (Oldest)":
                df = df.sort_values(by="Applied Date", ascending=True)
            elif sort_by == "Company (A-Z)":
                df = df.sort_values(by="Company", ascending=True)
            elif sort_by == "Company (Z-A)":
                df = df.sort_values(by="Company", ascending=False)
            elif sort_by == "Score (High-Low)":
                df = df.sort_values(by="Match Score", ascending=False)
            elif sort_by == "Score (Low-High)":
                df = df.sort_values(by="Match Score", ascending=True)

            df = df.reset_index(drop=True)
            st.metric("Showing Applications", len(df))


            # ════════════════════════════════════════════════════════════════
            # 📋 GRID VIEW — Interactive st.data_editor
            # ════════════════════════════════════════════════════════════════
            if view_mode == "📋 Grid":
                st.subheader("📋 Grid View")
                st.caption("Edit, add, or delete rows directly like a spreadsheet, then click **💾 Save Changes**.")

                # Prepare display dataframe (keep record_id but hide it)
                display_df = df.drop(columns=["record_id"])

                edited_df = st.data_editor(
                    display_df,
                    column_config={
                        "Status": st.column_config.SelectboxColumn(
                            "Status",
                            options=STATUS_OPTIONS,
                            required=True,
                        ),
                        "Match Score": st.column_config.NumberColumn(
                            "Match Score",
                            format="%d%%",
                        ),
                        "JD Description": st.column_config.TextColumn(
                            "JD Description",
                            width="medium",
                        ),
                        "Apply Link": st.column_config.LinkColumn(
                            "Apply Link",
                            width="medium",
                        ),
                    },
                    num_rows="dynamic",
                    hide_index=True,
                    key="airtable_editor",
                    width="stretch",
                )

                # ── Save Changes button ──
                if st.button("💾 Save Changes to Airtable", key="save_grid_btn"):
                    state = st.session_state.get("airtable_editor", {})
                    added = state.get("added_rows", [])
                    deleted = state.get("deleted_rows", [])
                    edited = state.get("edited_rows", {})

                    changes_made = 0
                    errors = []

                    # 1. Process Deletes
                    for idx in deleted:
                        rec_id = df.iloc[idx]["record_id"]
                        if rec_id:
                            ok, msg = delete_airtable_record(airtable_base_id, airtable_token, rec_id)
                            if ok: changes_made += 1
                            else: errors.append(f"Delete Failed on {df.iloc[idx]['Company']}: {msg}")

                    # 2. Process Edits
                    for idx_str, edited_fields in edited.items():
                        idx = int(idx_str)
                        if idx in deleted: continue  # Skip if we just deleted it
                        rec_id = df.iloc[idx]["record_id"]
                        if rec_id:
                            ok, msg = update_airtable_record(airtable_base_id, airtable_token, rec_id, edited_fields)
                            if ok: changes_made += 1
                            else: errors.append(f"Edit Failed on {df.iloc[idx]['Company']}: {msg}")

                    # 3. Process Adds
                    for row in added:
                        new_fields = {k: v for k, v in row.items() if v is not None and v != ""}
                        if "Status" not in new_fields:
                            new_fields["Status"] = "Not Applied"

                        ok, msg = create_airtable_record(airtable_base_id, airtable_token, new_fields)
                        if ok: changes_made += 1
                        else: errors.append(f"Create Failed: {msg}")

                    if changes_made > 0:
                        st.success(f"✅ Executed {changes_made} operation(s) to Airtable!")
                        fetch_all_airtable_records.clear()
                        st.rerun()
                    if errors:
                        for err in errors:
                            st.error(f"❌ {err}")
                    if changes_made == 0 and not errors:
                        st.info("No changes detected.")

            # ════════════════════════════════════════════════════════════════
            # 📌 KANBAN VIEW — Status-grouped cards
            # ════════════════════════════════════════════════════════════════
            else:
                st.subheader("📌 Kanban Board")
                st.caption("Change a card's status, delete cards, or add new applications quickly.")

                with st.expander("➕ Quick Add Application"):
                    with st.form("kanban_add_form", clear_on_submit=True):
                        n_col1, n_col2, n_col3 = st.columns([2, 2, 1])
                        with n_col1: new_co = st.text_input("Company")
                        with n_col2: new_role = st.text_input("Role")
                        with n_col3: new_stat = st.selectbox("Status", options=STATUS_OPTIONS)
                        if st.form_submit_button("Add to Board"):
                            if new_co and new_role:
                                ok, msg = create_airtable_record(
                                    airtable_base_id, airtable_token, 
                                    {"Company": new_co, "Role": new_role, "Status": new_stat}
                                )
                                if ok:
                                    st.success("✅ Added!")
                                    fetch_all_airtable_records.clear()
                                    st.rerun()
                                else:
                                    st.error(f"Failed: {msg}")
                            else:
                                st.warning("Company and Role are required.")

                kanban_cols = st.columns(len(STATUS_OPTIONS))

                for col_idx, status in enumerate(STATUS_OPTIONS):
                    with kanban_cols[col_idx]:
                        status_df = df[df["Status"] == status]
                        # Column header with count badge
                        st.markdown(f"### {status} ({len(status_df)})")
                        st.divider()

                        if status_df.empty:
                            st.caption("No jobs here.")
                        else:
                            for _, row in status_df.iterrows():
                                record_id = row["record_id"]
                                with st.container(border=True):
                                    hc1, hc2 = st.columns([4, 1])
                                    with hc1: st.markdown(f"**{row['Company']}**")
                                    with hc2:
                                        if st.button("🗑️", key=f"del_{record_id}", help="Delete"):
                                            ok, msg = delete_airtable_record(airtable_base_id, airtable_token, record_id)
                                            if ok:
                                                fetch_all_airtable_records.clear()
                                                st.rerun()
                                            else:
                                                st.error(f"Delete failed: {msg}")
                                                
                                    st.caption(row["Role"])
                                    st.markdown(f"Score: **{row['Match Score']}%**")
                                    if row["Applied Date"]:
                                        date_str = str(row["Applied Date"])[:10]
                                        st.caption(f"📅 {date_str}")
                                        
                                    if row.get("Apply Link"):
                                        st.markdown(f"🔗 **[Apply Here]({row['Apply Link']})**")
                                        
                                    if row.get("JD Description"):
                                        with st.expander("📄 View JD", expanded=False):
                                            st.text(row["JD Description"])

                                    # Status changer
                                    current_idx = STATUS_OPTIONS.index(status) if status in STATUS_OPTIONS else 0
                                    new_status = st.selectbox(
                                        "Move to",
                                        STATUS_OPTIONS,
                                        index=current_idx,
                                        key=f"kanban_status_{record_id}",
                                        label_visibility="collapsed",
                                    )

                                    if new_status != status:
                                        ok, msg = update_airtable_record(
                                            airtable_base_id, airtable_token,
                                            record_id, {"Status": new_status}
                                        )
                                        if ok:
                                            fetch_all_airtable_records.clear()
                                            st.rerun()
                                        else:
                                            st.error(f"Failed: {msg}")


# ═════════════════════════════════════════════════════════════════════════════
# 🗄️ SUPABASE VIEWER — tier1_rejections monitor
# ═════════════════════════════════════════════════════════════════════════════

elif page == "🗄️ Supabase Viewer":
    st.header("🗄️ Supabase Viewer — Tier 1 Rejections")
    st.caption("Monitor the automated Scout's rejection cache from the tier1_rejections table.")

    if not supabase_client:
        st.warning("⚠️ Valid Supabase credentials are required. Set them in the sidebar.")
    else:
        @st.cache_data(ttl=120, show_spinner="Fetching Supabase rejections...")
        def fetch_tier1_rejections(_client: Client):
            """Fetch all rows from tier1_rejections via Supabase official client."""
            response = _client.table("tier1_rejections").select("*").execute()
            return response.data

        if st.button("🔄 Refresh Data", key="supabase_refresh"):
            fetch_tier1_rejections.clear()

        try:
            rows = fetch_tier1_rejections(supabase_client)
            if rows:
                df = pd.DataFrame(rows)
                    
                # ── Filters & Sorting ──
                with st.expander("🔍 Filter & Sort Rejections", expanded=False):
                    f_col1, f_col2 = st.columns([2, 1])
                    with f_col1:
                        search_query = st.text_input("Search Company, Title, or Reason", key="supa_search")
                    with f_col2:
                        sort_by_supa = st.selectbox(
                            "Sort By",
                            options=[
                                "Date (Newest)", 
                                "Date (Oldest)", 
                                "Company (A-Z)", 
                                "Company (Z-A)"
                            ],
                            index=0,
                            key="supa_sort"
                        )
                
                if search_query:
                    # we do a combined mask across standard columns if they exist
                    mask = pd.Series(False, index=df.index)
                    for col in ["company", "company_name", "title", "job_title", "reason"]:
                        if col in df.columns:
                            mask = mask | df[col].str.contains(search_query, case=False, na=False)
                    df = df[mask]

                # ── Sorting ──
                date_col = "created_at" if "created_at" in df.columns else ("rejected_at" if "rejected_at" in df.columns else None)
                comp_col = "company_name" if "company_name" in df.columns else ("company" if "company" in df.columns else None)
                
                if sort_by_supa == "Date (Newest)" and date_col:
                    df = df.sort_values(by=date_col, ascending=False)
                elif sort_by_supa == "Date (Oldest)" and date_col:
                    df = df.sort_values(by=date_col, ascending=True)
                elif sort_by_supa == "Company (A-Z)" and comp_col:
                    df = df.sort_values(by=comp_col, ascending=True)
                elif sort_by_supa == "Company (Z-A)" and comp_col:
                    df = df.sort_values(by=comp_col, ascending=False)

                df = df.reset_index(drop=True)
                st.metric("Showing Rejections", len(df))
                
                st.caption("Select rows to delete and click **💾 Save Changes** to clear them from Supabase cache.")
                
                # Interactive data editor to allow deletions
                edited_df = st.data_editor(
                    df,
                    num_rows="dynamic",
                    key="supa_editor",
                    width="stretch"
                )

                if st.button("💾 Save Deletions to Supabase", key="supa_save_btn"):
                    state = st.session_state.get("supa_editor", {})
                    deleted = state.get("deleted_rows", [])
                    
                    changes = 0
                    errors = []
                    
                    for idx in deleted:
                        if "id" in df.columns:
                            row_id = df.iloc[idx]["id"]
                            try:
                                supabase_client.table("tier1_rejections").delete().eq("id", row_id).execute()
                                changes += 1
                            except Exception as ex:
                                errors.append(f"Failed to delete ID {row_id}: {ex}")
                        elif "job_url" in df.columns: # Fallback if no specific 'id'
                            j_url = df.iloc[idx]["job_url"]
                            try:
                                supabase_client.table("tier1_rejections").delete().eq("job_url", j_url).execute()
                                changes += 1
                            except Exception as ex:
                                errors.append(f"Failed to delete {j_url}: {ex}")

                    if changes > 0:
                        st.success(f"✅ Deleted {changes} rejection(s) from Supabase!")
                        fetch_tier1_rejections.clear()
                        st.rerun()
                    if errors:
                        for err in errors: st.error(f"❌ {err}")
                    if changes == 0 and not errors:
                        st.info("No deletions detected.")
            else:
                st.info("No rejections found in Supabase. The cache is empty.")
        except Exception as e:
            st.error(f"Failed to fetch Supabase data: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# ✂️ RESUME STUDIO — Truthful JD-Matched Resume Optimization
# ═════════════════════════════════════════════════════════════════════════════

elif page == "✂️ Resume Studio":
    st.header("✂️ Resume Studio")
    st.caption(
        "Rephrase your Summary, Experience, Projects & Skills to mirror a target JD's vocabulary. "
        "No skills are fabricated — only existing content is reworded and reordered."
    )

    if not airtable_base_id or not airtable_token:
        st.warning("⚠️ Airtable credentials are required for the Resume Studio. Set them in the sidebar.")
    else:
        with st.spinner("Fetching 'Not Applied' jobs from Airtable..."):
            tailor_jobs = fetch_not_applied_jobs(airtable_base_id, airtable_token)

        if tailor_jobs:
            job_labels = [f"{j['company']} — {j['role']}" for j in tailor_jobs]
            selected_index = st.selectbox(
                "Select a job to tailor your resume for:",
                range(len(job_labels)),
                format_func=lambda i: job_labels[i],
                key="tailor_job_select",
            )

            selected_job = tailor_jobs[selected_index]

            # Show JD preview
            with st.expander("📋 View Job Description", expanded=False):
                if selected_job["jd_description"]:
                    st.text(selected_job["jd_description"])
                else:
                    st.warning("No JD Description stored for this job.")

            # Upload .docx resume
            st.subheader("Upload your .docx Resume")
            tailor_file = st.file_uploader(
                "Upload the DOCX resume to tailor",
                type=["docx"],
                key="tailor_docx_upload",
            )

            if tailor_file and selected_job["jd_description"]:
                # Parse the DOCX
                doc, summary_paras, exp_paras, proj_paras, skills_paras = parse_docx_sections(tailor_file)
                summary_texts = get_texts(summary_paras)
                exp_texts = get_texts(exp_paras)
                proj_texts = get_texts(proj_paras)
                skills_texts = get_texts(skills_paras)

                with st.expander("🔍 Extracted Sections Preview", expanded=False):
                    st.markdown(f"**Summary lines:** {len(summary_texts)}")
                    st.markdown(f"**Experience bullets:** {len(exp_texts)}")
                    st.markdown(f"**Projects bullets:** {len(proj_texts)}")
                    st.markdown(f"**Skills lines:** {len(skills_texts)}")

                if not exp_texts and not proj_texts:
                    st.warning(
                        "⚠️ No Experience or Projects bullets detected. "
                        "Make sure your DOCX has 'EXPERIENCE' and 'PROJECTS' section headers."
                    )
                else:
                    # Rate-limit guard
                    if is_rate_limited():
                        st.warning(
                            f"⚠️ Daily API Limit Reached ({DAILY_LIMIT} requests). "
                            "Try again tomorrow."
                        )
                    else:
                        remaining = DAILY_LIMIT - st.session_state.get("tailor_api_calls", 0)
                        st.info(
                            f"📊 API calls today: {st.session_state.get('tailor_api_calls', 0)} "
                            f"/ {DAILY_LIMIT} ({remaining} remaining)"
                        )

                        save_to_vault = st.checkbox("☁️ Automatically save tailored resume to Supabase Vault", value=True)

                        # Tailor button
                        if st.button("✂️ Tailor Resume", key="tailor_btn"):
                            if not api_key:
                                st.warning("Please enter your Gemini API Key in the sidebar.")
                            else:
                                with st.spinner("🤖 Tailoring your resume with AI..."):
                                    prompt = build_tailor_prompt(
                                        selected_job["jd_description"],
                                        summary_texts,
                                        exp_texts,
                                        proj_texts,
                                        skills_texts,
                                    )
                                    ai_result = generate_with_fallback(prompt, api_key)

                                if ai_result:
                                    # Show AI output for transparency
                                    with st.expander("🧠 AI Response (raw)", expanded=False):
                                        st.text(ai_result)

                                    # Apply changes in-place on the DOCX paragraphs
                                    total_replaced = apply_tailored_sections(
                                        summary_paras, exp_paras, proj_paras, skills_paras, ai_result
                                    )
                                    st.success(
                                        f"✅ Replaced {total_replaced} total elements across all 4 sections."
                                    )

                                    # Save modified DOCX and offer download
                                    doc_bytes = save_doc_to_bytes(doc)
                                    company_slug = selected_job["company"].replace(" ", "_").replace("/", "-")[:20]
                                    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
                                    filename = f"Tailored_Resume_{company_slug}_{timestamp_str}.docx"

                                    st.download_button(
                                        label="⬇️ Download Tailored Resume (.docx)",
                                        data=doc_bytes,
                                        file_name=filename,
                                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                        key="tailor_download",
                                    )

                                    if save_to_vault:
                                        if not supabase_client:
                                            st.warning("⚠️ Supabase credentials needed to save to Vault. Set them in the sidebar.")
                                        else:
                                            with st.spinner(f"Uploading {filename} to Supabase..."):
                                                try:
                                                    supabase_client.storage.from_("tailored_resumes").upload(
                                                        file=doc_bytes,
                                                        path=filename,
                                                        file_options={
                                                            "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 
                                                            "x-upsert": "true"
                                                        }
                                                    )
                                                    public_url = supabase_client.storage.from_("tailored_resumes").get_public_url(filename)
                                                    st.success(f"✅ Saved to Vault as **{filename}**!")
                                                    st.markdown(f"🔗 **[Download from Vault]({public_url})**")
                                                except Exception as e:
                                                    st.error(f"Failed to upload to Supabase: {e}")
                                else:
                                    st.error("Tailoring failed. See error messages above.")
            elif tailor_file and not selected_job["jd_description"]:
                st.warning("⚠️ The selected job has no JD Description. Cannot tailor without a JD.")
        else:
            st.info("No 'Not Applied' jobs found in Airtable. Run the Scout pipeline first.")


# ═════════════════════════════════════════════════════════════════════════════
# 📄 JOB CURATOR — PDF Batch Curation + Airtable Push
# ═════════════════════════════════════════════════════════════════════════════

elif page == "📄 Job Curator":
    st.header("📄 Job Curator")
    st.caption("Upload PDF job dumps, apply deterministic QA/SDET filters, and push selected jobs to Airtable.")

    try:
        jc_pipeline = load_job_curator_pipeline()
        max_upload_files = jc_pipeline["max_upload_files"]
        extract_blocks_from_pdf = jc_pipeline["extract_blocks_from_pdf"]
        extract_experience_years = jc_pipeline["extract_experience_years"]
        evaluate_job_block = jc_pipeline["evaluate_job_block"]
        refine_job_batch = jc_pipeline["refine_job_batch"]
    except Exception as e:
        st.error(f"Failed to load Job Curator modules: {e}")
        st.stop()

    upload_col, info_col = st.columns([2, 1])
    with upload_col:
        uploaded_pdfs = st.file_uploader(
            "Upload up to 6 PDF files",
            type=["pdf"],
            accept_multiple_files=True,
            key="jc_pdf_uploads",
        )
    with info_col:
        st.metric("Max PDFs", max_upload_files)
        st.metric("Uploaded", len(uploaded_pdfs) if uploaded_pdfs else 0)

    if uploaded_pdfs and len(uploaded_pdfs) > max_upload_files:
        st.error(f"Too many PDFs uploaded. Maximum allowed is {max_upload_files}.")

    parse_col, clear_col = st.columns(2)
    with parse_col:
        parse_clicked = st.button("Parse & Filter Jobs", key="jc_parse_btn", use_container_width=True)
    with clear_col:
        clear_clicked = st.button("Clear Curated Results", key="jc_clear_btn", use_container_width=True)

    if clear_clicked:
        st.session_state.pop("jc_results_df", None)
        st.session_state.pop("jc_stage1_total", None)
        st.session_state.pop("jc_selected_total", None)
        st.rerun()

    if parse_clicked:
        if not uploaded_pdfs:
            st.warning("Upload at least 1 PDF file to continue.")
        elif len(uploaded_pdfs) > max_upload_files:
            st.warning(f"Please keep uploads within {max_upload_files} PDFs.")
        else:
            stage1_results = []
            total_blocks = 0

            with st.spinner("Parsing PDFs and applying filters..."):
                for pdf_file in uploaded_pdfs:
                    pdf_bytes = pdf_file.getvalue()
                    blocks = extract_blocks_from_pdf(pdf_bytes, pdf_file.name)
                    total_blocks += len(blocks)

                    for idx, block_text in enumerate(blocks, 1):
                        exp_min, exp_max = extract_experience_years(block_text)
                        evaluation = evaluate_job_block(block_text, exp_min, exp_max)
                        stage1_results.append(
                            {
                                "Source_PDF": pdf_file.name,
                                "Block_ID": idx,
                                "Exp_Min": exp_min,
                                "Exp_Max": exp_max,
                                "Raw_Text": block_text,
                                **evaluation,
                            }
                        )

                refined_batch = refine_job_batch(stage1_results)
                selected_stage1 = [row for row in stage1_results if row.get("status") == "Selected"]

                enriched_jobs = []
                for idx, job in enumerate(refined_batch):
                    enriched = dict(job)
                    if idx < len(selected_stage1):
                        enriched["Raw_Text"] = selected_stage1[idx].get("Raw_Text", "")
                    enriched_jobs.append(enriched)

                deduped_jobs = []
                seen_keys = set()
                for job in enriched_jobs:
                    key = (
                        str(job.get("Company", "")).strip().lower(),
                        str(job.get("Role", "")).strip().lower(),
                        str(job.get("Email", "")).strip().lower(),
                    )
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    deduped_jobs.append(job)

                curated_rows = []
                for job in deduped_jobs:
                    curated_rows.append(
                        {
                            "Company": job.get("Company", ""),
                            "Role": job.get("Role", ""),
                            "Exp": job.get("Exp", ""),
                            "Location": job.get("Location", ""),
                            "Email": job.get("Email", ""),
                            "Notes": job.get("Notes", ""),
                            "Domain": job.get("Domain", ""),
                            "Raw_Text": job.get("Raw_Text", ""),
                            "Source_PDF": job.get("Source_PDF", ""),
                        }
                    )

            st.session_state["jc_results_df"] = pd.DataFrame(curated_rows).reset_index(drop=True)
            st.session_state["jc_stage1_total"] = total_blocks
            st.session_state["jc_selected_total"] = len(curated_rows)

    results_df = st.session_state.get("jc_results_df")
    if isinstance(results_df, pd.DataFrame):
        display_cols = ["Company", "Role", "Exp", "Location", "Email", "Notes", "Domain"]

        metrics_col1, metrics_col2, metrics_col3 = st.columns(3)
        with metrics_col1:
            st.metric("Total Blocks Parsed", int(st.session_state.get("jc_stage1_total", 0)))
        with metrics_col2:
            st.metric("Filtered Jobs", int(st.session_state.get("jc_selected_total", len(results_df))))
        with metrics_col3:
            st.metric("Ready to Push", int(len(results_df)))

        if results_df.empty:
            st.info("No jobs matched the filter criteria.")
        else:
            st.subheader("Filtered Jobs")
            st.dataframe(results_df[display_cols], use_container_width=True, hide_index=True)

            st.subheader("Select Jobs to Push")
            selection_df = results_df[display_cols].copy()
            selection_df.insert(0, "Select", False)

            edited_selection = st.data_editor(
                selection_df,
                column_config={
                    "Select": st.column_config.CheckboxColumn("Push", default=False),
                },
                width="stretch",
                hide_index=True,
                key="jc_selection_editor",
            )

            selected_indices = edited_selection.index[edited_selection["Select"]].tolist()
            push_col1, push_col2 = st.columns([1, 2])
            with push_col1:
                st.caption(f"Selected: {len(selected_indices)}")
            with push_col2:
                push_clicked = st.button(
                    "Push Selected to Airtable",
                    key="jc_push_btn",
                    use_container_width=True,
                    disabled=len(selected_indices) == 0,
                )

            if push_clicked:
                if not airtable_base_id or not airtable_token:
                    st.warning("Airtable credentials are required. Set them in the sidebar.")
                else:
                    selected_jobs = results_df.iloc[selected_indices]
                    ist_tz = pytz.timezone("Asia/Kolkata")
                    applied_time = datetime.datetime.now(ist_tz).isoformat()
                    success_count = 0
                    fail_messages = []

                    with st.spinner("Pushing selected jobs to Airtable..."):
                        for _, row in selected_jobs.iterrows():
                            recruiter_email = row.get("Email", "")
                            job_exp = row.get("Exp", "")
                            job_location = row.get("Location", "")
                            job_domain = row.get("Domain", "")
                            job_notes = row.get("Notes", "")
                            fields = {
                                "Company": row.get("Company", ""),
                                "Role": row.get("Role", ""),
                                "Status": "Not Applied",
                                "Applied Date": applied_time,
                                "JD Description": (
                                    f"Recruiter: {recruiter_email}\n"
                                    f"Exp: {job_exp}\n"
                                    f"Location: {job_location}\n"
                                    f"Domain: {job_domain}\n"
                                    f"Notes: {job_notes}"
                                ),
                                "Resume Name": "Job Curator Import",
                            }
                            ok, msg = create_airtable_record(airtable_base_id, airtable_token, fields)
                            if ok:
                                success_count += 1
                            else:
                                fail_messages.append(f"{row.get('Company', 'Unknown')} - {row.get('Role', 'Unknown')}: {msg}")

                    if success_count:
                        st.success(f"Pushed {success_count} job(s) to Airtable with status 'Not Applied'.")
                    if fail_messages:
                        st.error("Some jobs could not be pushed.")
                        for err in fail_messages[:5]:
                            st.error(err)
                        if len(fail_messages) > 5:
                            st.error(f"...and {len(fail_messages) - 5} more failure(s).")


# ═════════════════════════════════════════════════════════════════════════════
# ☁️ DOCUMENT VAULT — Placeholder for PDF storage
# ═════════════════════════════════════════════════════════════════════════════

elif page == "☁️ Document Vault":
    st.header("☁️ Supabase PDF Vault")
    st.caption("Upload and store finalized resumes and cover letters in the `tailored_resumes` bucket.")

    if not supabase_client:
        st.warning("⚠️ Valid Supabase credentials are required. Set them in the sidebar.")
    else:
        GLOBAL_STORAGE_LIMIT = 50 * 1024 * 1024  # 50 MB

        # Helper to fetch bucket file list
        @st.cache_data(ttl=60, show_spinner="Fetching Vault files...")
        def fetch_vault_files(_client: Client):
            try:
                res = _client.storage.from_("tailored_resumes").list()
                # filter out empty folder placeholder if any
                files = [f for f in res if f.get("name") and f["name"] != ".emptyFolderPlaceholder"]
                return files
            except Exception:
                # If bucket doesn't exist or is empty, this might throw or return empty
                return []

        if st.button("🔄 Refresh Vault", key="vault_refresh_btn"):
            fetch_vault_files.clear()

        files_list = fetch_vault_files(supabase_client)
        
        # Calculate usage
        total_used_bytes = sum([f.get("metadata", {}).get("size", 0) for f in files_list])
        used_mb = total_used_bytes / (1024 * 1024)
        pct_used = (total_used_bytes / GLOBAL_STORAGE_LIMIT) * 100

        # Display usage metric
        st.write(f"**Storage Usage:** {used_mb:.2f} MB / 50.00 MB ({pct_used:.1f}%)")
        st.progress(min(pct_used / 100.0, 1.0))

        st.divider()

        vault_file = st.file_uploader(
            "Upload Finalized PDF",
            type=["pdf"],
            key="vault_pdf_upload",
        )

        if vault_file:
            file_size = vault_file.size
            if total_used_bytes + file_size > GLOBAL_STORAGE_LIMIT:
                st.error("❌ Storage full! Delete some files before uploading a new one.")
            else:
                # Overwrite behavior via upsert=true
                filename = vault_file.name
                
                if st.button("⬆️ Upload to Supabase", key="vault_upload_btn"):
                    with st.spinner(f"Uploading {filename}..."):
                        try:
                            file_bytes = vault_file.read()
                            supabase_client.storage.from_("tailored_resumes").upload(
                                file=file_bytes,
                                path=filename,
                                file_options={"content-type": "application/pdf", "x-upsert": "true"}
                            )
                            # Get public URL
                            public_url = supabase_client.storage.from_("tailored_resumes").get_public_url(filename)
                            st.success(f"✅ Successfully uploaded **{filename}**!")
                            st.markdown(f"🔗 **[Click here to view/download the PDF]({public_url})**")
                            
                            # Clear cache to show new file
                            fetch_vault_files.clear()
                        except Exception as e:
                            st.error(f"Failed to upload: {e}")

        # Display Existing Files
        st.subheader("📂 Existing Documents")
        if not files_list:
            st.info("No documents found in the vault.")
        else:
            # Prepare dataframe
            file_data = []
            for f in files_list:
                fname = f.get("name")
                fsize = f.get("metadata", {}).get("size", 0) / 1024  # KB
                created = f.get("created_at", "")
                pub_url = supabase_client.storage.from_("tailored_resumes").get_public_url(fname)
                file_data.append({
                    "Filename": fname,
                    "Size (KB)": round(fsize, 1),
                    "Created At": created[:10] if created else "",
                    "Link": pub_url
                })
            
            vault_df = pd.DataFrame(file_data)
            
            # ── Sorting ──
            sort_by_vault = st.selectbox(
                "Sort Documents By",
                options=[
                    "Date (Newest)", 
                    "Date (Oldest)", 
                    "Filename (A-Z)", 
                    "Filename (Z-A)", 
                    "Size (Largest)", 
                    "Size (Smallest)"
                ],
                index=0,
                key="vault_sort"
            )

            if sort_by_vault == "Date (Newest)":
                vault_df = vault_df.sort_values(by="Created At", ascending=False)
            elif sort_by_vault == "Date (Oldest)":
                vault_df = vault_df.sort_values(by="Created At", ascending=True)
            elif sort_by_vault == "Filename (A-Z)":
                vault_df = vault_df.sort_values(by="Filename", ascending=True)
            elif sort_by_vault == "Filename (Z-A)":
                vault_df = vault_df.sort_values(by="Filename", ascending=False)
            elif sort_by_vault == "Size (Largest)":
                vault_df = vault_df.sort_values(by="Size (KB)", ascending=False)
            elif sort_by_vault == "Size (Smallest)":
                vault_df = vault_df.sort_values(by="Size (KB)", ascending=True)

            vault_df = vault_df.reset_index(drop=True)
            
            st.caption("Select rows to delete and click **💾 Delete Selected Files** to free up storage.")
            
            # Show interactive dataframe where Link is clickable
            edited_vault = st.data_editor(
                vault_df,
                column_config={
                    "Link": st.column_config.LinkColumn("Public URL")
                },
                width="stretch",
                hide_index=True,
                num_rows="dynamic",
                key="vault_editor"
            )

            if st.button("💾 Delete Selected Files", key="vault_del_btn"):
                state = st.session_state.get("vault_editor", {})
                deleted = state.get("deleted_rows", [])
                
                if deleted:
                    files_to_delete = []
                    for idx in deleted:
                        files_to_delete.append(vault_df.iloc[idx]["Filename"])
                    
                    try:
                        res = supabase_client.storage.from_("tailored_resumes").remove(files_to_delete)
                        st.success(f"✅ Deleted {len(res)} file(s) from Supabase!")
                        fetch_vault_files.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to delete files: {e}")
                else:
                    st.info("No files selected for deletion.")


# ═════════════════════════════════════════════════════════════════════════════
# 📧 MAIL DRAFTER — AI Cold Outreach Email Generator + Gmail Drafts
# ═════════════════════════════════════════════════════════════════════════════

elif page == "📧 Mail Drafter":
    st.header("📧 Mail Drafter")
    st.caption("Draft professional cold outreach emails for 'Not Applied' jobs and save them to Gmail Drafts.")

    if not airtable_base_id or not airtable_token:
        st.warning("⚠️ Airtable credentials are required. Set them in the sidebar.")
    else:
        # ── Fetch 'Not Applied' jobs from Airtable ──
        @st.cache_data(ttl=60, show_spinner="Fetching 'Not Applied' jobs...")
        def fetch_not_applied_for_mail(_base_id, _token):
            url = f"https://api.airtable.com/v0/{_base_id}/Applications"
            headers = {"Authorization": f"Bearer {_token}"}
            all_records = []
            offset = None
            while True:
                params = {"filterByFormula": "{Status} = 'Not Applied'"}
                if offset:
                    params["offset"] = offset
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                for rec in data.get("records", []):
                    fields = rec.get("fields", {})
                    all_records.append({
                        "record_id": rec.get("id", ""),
                        "Company": fields.get("Company", ""),
                        "Role": fields.get("Role", ""),
                        "JD Description": fields.get("JD Description", ""),
                        "Apply Link": fields.get("Apply Link", ""),
                    })
                offset = data.get("offset")
                if not offset:
                    break
            return all_records

        if st.button("🔄 Refresh Jobs", key="mail_refresh"):
            fetch_not_applied_for_mail.clear()
            st.rerun()

        try:
            mail_jobs = fetch_not_applied_for_mail(airtable_base_id, airtable_token)
        except Exception as e:
            st.error(f"Failed to fetch Airtable records: {e}")
            mail_jobs = []

        if not mail_jobs:
            st.info("No 'Not Applied' jobs found in Airtable. Run the Scout pipeline first.")
        else:
            job_labels = [f"{j['Company']} — {j['Role']}" for j in mail_jobs]
            selected_idx = st.selectbox(
                "Select a job to draft an email for:",
                range(len(job_labels)),
                format_func=lambda i: job_labels[i],
                key="mail_job_select",
            )

            selected_job = mail_jobs[selected_idx]
            jd_desc = selected_job.get("JD Description", "")

            # ── Extract recruiter email from JD Description ──
            recruiter_email = ""
            email_match = re.search(r'Recruiter:\s*([\w.+-]+@[\w-]+\.[\w.-]+)', jd_desc)
            if email_match:
                recruiter_email = email_match.group(1)

            # ── Show job details ──
            st.subheader(f"{selected_job['Company']} — {selected_job['Role']}")

            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown(f"**Company:** {selected_job['Company']}")
                st.markdown(f"**Role:** {selected_job['Role']}")
                if recruiter_email:
                    st.success(f"📧 Recruiter Email: **{recruiter_email}**")
                else:
                    st.warning("⚠️ No recruiter email found in JD Description")
            with col2:
                if selected_job.get("Apply Link"):
                    st.markdown(f"🔗 **[Apply Link]({selected_job['Apply Link']})**")

            with st.expander("📋 JD Description Preview", expanded=False):
                if jd_desc:
                    st.text(jd_desc[:2000])
                else:
                    st.info("No JD Description available.")

            # ── Model call counters (session-scoped) ──
            if "mail_model2_calls" not in st.session_state:
                st.session_state["mail_model2_calls"] = 0
            if "mail_model3_calls" not in st.session_state:
                st.session_state["mail_model3_calls"] = 0

            # ── Draft Email Button ──
            if st.button("✍️ Draft Email", key="mail_draft_btn"):
                if not api_key:
                    st.warning("Please enter your Gemini API Key in the sidebar.")
                elif not jd_desc:
                    st.warning("No JD Description available for this job.")
                else:
                    # Load resume.txt
                    resume_text = ""
                    resume_path = os.path.join(os.path.dirname(__file__), "resume.txt")
                    if os.path.exists(resume_path):
                        with open(resume_path, "r", encoding="utf-8") as f:
                            resume_text = f.read().strip()
                    else:
                        st.warning("⚠️ resume.txt not found. Drafting without resume context.")

                    email_prompt = f"""You are a professional email writer for job applications.
Write a short, professional cold outreach email for the following job.

Rules:
- Maximum 150 words
- Mention the role "{selected_job['Role']}" and company "{selected_job['Company']}" by name
- Highlight 2-3 relevant skills from the resume that match the job description
- End with a clear call to action asking them to review the attached resume
- Include a subject line at the top in the format: Subject: [subject text]
- Tone: confident, concise, and professional
- Do NOT use placeholder brackets like [Your Name] — use "Dheeraj" as the name

Job Description:
{jd_desc[:3000]}

Resume:
{resume_text[:3000]}
"""

                    genai.configure(api_key=api_key)
                    drafted_email = None

                    with st.spinner("🤖 Drafting email with AI..."):
                        # Attempt 1: Primary Model
                        try:
                            st.toast("🚀 Using Model 1: gemini-3.1-flash-lite-preview")
                            model = genai.GenerativeModel("gemini-3.1-flash-lite-preview")
                            response = model.generate_content(email_prompt)
                            drafted_email = response.text
                        except Exception as e1:
                            st.toast(f"⚠️ Model 1 failed: {e1}")

                            # Attempt 2: Fallback Model 2
                            if st.session_state["mail_model2_calls"] < 19:
                                try:
                                    st.toast("🔄 Using Model 2: gemini-2.5-flash")
                                    model = genai.GenerativeModel("gemini-2.5-flash")
                                    response = model.generate_content(email_prompt)
                                    drafted_email = response.text
                                except Exception as e2:
                                    st.toast(f"❌ Model 2 failed: {e2}")
                                finally:
                                    st.session_state["mail_model2_calls"] += 1
                            else:
                                st.toast("⚠️ Model 2 limit (19) reached.")

                            # Attempt 3: Fallback Model 3
                            if drafted_email is None and st.session_state["mail_model3_calls"] < 19:
                                try:
                                    st.toast("🔄 Using Model 3: gemini-2.0-flash")
                                    model = genai.GenerativeModel("gemini-2.0-flash")
                                    response = model.generate_content(email_prompt)
                                    drafted_email = response.text
                                except Exception as e3:
                                    st.toast(f"❌ Model 3 failed: {e3}")
                                finally:
                                    st.session_state["mail_model3_calls"] += 1
                            elif drafted_email is None:
                                st.toast("⚠️ Model 3 limit (19) reached.")

                    if drafted_email:
                        st.session_state["drafted_email"] = drafted_email
                        st.session_state["draft_recruiter_email"] = recruiter_email
                    else:
                        st.error("❌ All models exhausted. Could not draft email.")

            # ── Show drafted email for editing ──
            if "drafted_email" in st.session_state:
                st.subheader("📝 Drafted Email")
                edited_email = st.text_area(
                    "Edit the drafted email below:",
                    value=st.session_state["drafted_email"],
                    height=350,
                    key="mail_email_editor",
                )

                draft_to = st.session_state.get("draft_recruiter_email", "")
                if draft_to:
                    st.info(f"📧 To: **{draft_to}**")
                else:
                    st.warning("⚠️ No recruiter email found in JD Description. You can manually enter one below.")
                    draft_to = st.text_input("Enter recruiter email:", key="mail_manual_email")

                # ── Save to Gmail Drafts ──
                if st.button("📨 Save to Gmail Drafts", key="mail_gmail_btn"):
                    if not draft_to:
                        st.error("❌ No recruiter email provided. Cannot create Gmail draft.")
                    else:
                        try:
                            import json
                            from google.oauth2.credentials import Credentials
                            from googleapiclient.discovery import build

                            creds_dict = json.loads(st.secrets["GMAIL_CREDENTIALS"])
                            creds = Credentials(
                                token=creds_dict.get("token"),
                                refresh_token=creds_dict.get("refresh_token"),
                                token_uri=creds_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
                                client_id=creds_dict.get("client_id"),
                                client_secret=creds_dict.get("client_secret"),
                                scopes=creds_dict.get("scopes", ["https://www.googleapis.com/auth/gmail.compose"]),
                            )

                            service = build("gmail", "v1", credentials=creds)

                            # Extract subject line from email body
                            email_body = edited_email
                            subject = "Job Application"
                            subject_match = re.search(r'Subject:\s*(.+)', edited_email)
                            if subject_match:
                                subject = subject_match.group(1).strip()
                                # Remove subject line from body
                                email_body = edited_email.replace(subject_match.group(0), "").strip()

                            message = MIMEText(email_body)
                            message["to"] = draft_to
                            message["subject"] = subject

                            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

                            draft = service.users().drafts().create(
                                userId="me",
                                body={"message": {"raw": raw_message}}
                            ).execute()

                            st.success(f"✅ Draft saved to Gmail! (Draft ID: {draft['id']})")
                            st.balloons()

                        except KeyError:
                            st.error("❌ GMAIL_CREDENTIALS not found in Streamlit secrets. Add OAuth credentials to .streamlit/secrets.toml")
                        except Exception as e:
                            st.error(f"❌ Failed to save Gmail draft: {e}")
