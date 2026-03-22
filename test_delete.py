import os
import json
import toml
from supabase import create_client

secrets = toml.load(".streamlit/secrets.toml")
url = secrets["SUPABASE_URL"]
key = secrets["SUPABASE_KEY"]

supabase = create_client(url, key)

print("Listing files before delete:")
files = supabase.storage.from_("tailored_resumes").list()
print([f["name"] for f in files])

if files and files[0]["name"] != ".emptyFolderPlaceholder":
    fname = files[0]["name"]
    print(f"Attempting to delete {fname}...")
    # This is exactly what app.py does
    res = supabase.storage.from_("tailored_resumes").remove([fname])
    print("Delete response:", res)

    print("Listing files after delete:")
    files2 = supabase.storage.from_("tailored_resumes").list()
    print([f["name"] for f in files2])
