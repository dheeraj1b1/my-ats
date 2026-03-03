import streamlit as st
import pdfplumber
import docx
import google.generativeai as genai

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

# API Key Input
api_key = st.text_input("Enter your Gemini API Key", type="password")

st.header("1. Paste Job Description")
jd_text = st.text_area("Paste the JD here...", height=200)

st.header("2. Upload Resume")
uploaded_file = st.file_uploader(
    "Upload your Resume (PDF or DOCX)", type=["pdf", "docx"])

if st.button("Scan & Match"):
    if not api_key:
        st.warning("Please enter your Gemini API Key first.")
    elif jd_text and uploaded_file:
        with st.spinner("Scanning..."):
            # Setup Gemini
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')

            resume_text = extract_text_from_file(uploaded_file)

            # The Prompt Instructions
            prompt = f"""
            You are a highly calibrated Applicant Tracking System (ATS). Your goal is to objectively evaluate if the Resume meets the strict technical and experience requirements of the Job Description.
            
            CRITICAL RULES:
            - DO NOT assume skills. Only count skills explicitly present in the text.
            - DO perform smart matching (ignore case sensitivity, e.g., 'python' matches 'Python'; match common abbreviations like 'AWS' and 'Amazon Web Services').
            - Focus exclusively on hard skills, technical tools, frameworks, and required years of experience.
            
            Job Description: {jd_text}
            Resume: {resume_text}
            
            Output strictly in this JSON-like format for easy reading:
            
            ### Match Score: [Percentage]%
            **Verdict:** ["🟢 Good to Apply" (if >=85%) or "🔴 Needs Improvement" (if <85%)]
            
            ### Critical Missing Elements
            * [Bullet points of completely missing hard skills or experience gaps]
            
            ### Targeted Improvements (By Section)
            * **Summary/Objective:** [Actionable advice if missing key tools here]
            * **Skills/Core Competencies:** [Specific tools/keywords to add explicitly]
            * **Experience/Projects:** [Where to add explicit years of experience or tool usage context]
            * **Education/Certifications:** [Any missing certs or degrees mentioned in JD]
            """

            try:
                response = model.generate_content(prompt)
                st.subheader("ATS Results")
                st.markdown(response.text)
            except Exception as e:
                st.error(f"Error calling API: {e}")
    else:
        st.warning("Please provide both a Job Description and a Resume.")
