import streamlit as st
import pdfplumber
import docx
import google.generativeai as genai
import datetime
import pytz
import requests

# --- Helper Function ---


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


# --- UI Layout ---
st.set_page_config(page_title="My ATS", page_icon="📄")
st.title("My Personal ATS Matcher")

# --- SECURE CREDENTIALS SECTION ---
st.header("🔑 Secure Credentials")

# 1. Gemini API Key
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.success("✅ Gemini API Key securely loaded from backend secrets")
else:
    api_key = st.text_input("1. Enter your Gemini API Key", type="password")
    st.warning("⚠️ API Key not found in secrets. Please enter it manually.")

# 2. Airtable Base ID (This is just an ID, not a password, so it's safe to show)
airtable_base_id = st.text_input(
    "2. Enter Airtable Base ID",
    value="appABPMwKgXkr8Rgn"
)

# 3. Airtable Token
if "AIRTABLE_TOKEN" in st.secrets:
    airtable_token = st.secrets["AIRTABLE_TOKEN"]
    st.success("✅ Airtable Token securely loaded from backend secrets")
else:
    airtable_token = st.text_input(
        "3. Enter Airtable Personal Access Token", type="password")
    st.warning("⚠️ Airtable Token not found in secrets. Please enter it manually.")

# --- 1. Application Details Section ---
st.header("1. Application Details")
manual_company = st.text_input(
    "Company Name (Optional: AI will detect if left blank)",
    placeholder="e.g., Google, Infosys"
)
jd_text = st.text_area("Paste the Job Description here...", height=200)

st.header("2. Upload Resume")
uploaded_file = st.file_uploader(
    "Upload your Resume (PDF or DOCX)",
    type=["pdf", "docx"]
)

if st.button("Scan & Match"):
    if not api_key:
        st.warning("Please enter your Gemini API Key first.")
    elif jd_text and uploaded_file:
        with st.spinner("Scanning..."):
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')
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

                # Extract score strictly as a number for Airtable (Fixed bare except)
                match_score = 0
                if "MATCH_SCORE:" in full_response:
                    try:
                        score_str = full_response.split("MATCH_SCORE:")[
                            1].split("%")[0].strip()
                        match_score = int(score_str)
                    except ValueError:
                        match_score = 0

                if "MATCH_SCORE:" in full_response:
                    clean_display = full_response.split(
                        "MATCH_SCORE:")[1].split("\n", 1)[1]
                else:
                    clean_display = full_response

                # Display Results
                st.subheader(
                    f"ATS Results for {final_company} - {extracted_role}")
                st.markdown(clean_display)

                # Capture exact IST time for Airtable
                ist_tz = pytz.timezone('Asia/Kolkata')
                exact_time = datetime.datetime.now(ist_tz).isoformat()

                # Save data to session state
                st.session_state['last_scan'] = {
                    "company": final_company,
                    "role": extracted_role,
                    "jd": jd_text,
                    "score": match_score,
                    "date": exact_time
                }

            except Exception as e:
                st.error(f"Error calling API: {e}")
    else:
        st.warning("Please provide both a Job Description and a Resume.")

# =====================================================================
# FUTURE ENHANCEMENT NOTE / KNOWN AMBIGUITY (Tracked via Git)
# ---------------------------------------------------------------------
# Scenario: Applying to the same company multiple times (e.g., Eurofins).
# Current Behavior: The app blindly creates a new row in Airtable for every scan.
# Potential Future Fix: Prompt AI to extract "Job ID" to make roles unique.
# =====================================================================

# --- 3. Airtable Integration ---
st.header("3. Log Application")
if 'last_scan' in st.session_state:
    scan_data = st.session_state['last_scan']
    current_role = scan_data.get('role', 'QA Engineer')

    role_note = "*(Defaulted)*" if current_role == "QA Engineer" else ""
    st.info(
        f"Ready to log application for: **{scan_data['company']}** | Role: **{current_role}** {role_note} | Score: **{scan_data['score']}%**")

    if st.button("🚀 Send to Airtable Tracker"):
        if not airtable_base_id or not airtable_token:
            st.warning("⚠️ Please enter Airtable credentials above.")
        else:
            url = f"https://api.airtable.com/v0/{airtable_base_id}/Applications"
            headers = {
                "Authorization": f"Bearer {airtable_token}",
                "Content-Type": "application/json"
            }

            data = {
                "fields": {
                    "Company": scan_data['company'],
                    "Role": current_role,
                    "Match Score": scan_data['score'],
                    "Status": "Not Applied",
                    "Applied Date": scan_data['date'],
                    "JD Description": scan_data['jd']
                }
            }

            with st.spinner("Logging to Airtable..."):
                resp = requests.post(url, json=data, headers=headers)
                if resp.status_code == 200:
                    st.success(f"Successfully logged {scan_data['company']}!")
                    st.balloons()
                else:
                    st.error(f"Failed to log: {resp.text}")
