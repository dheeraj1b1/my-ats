import pandas as pd
import io


def load_previous_df(file_bytes: bytes) -> pd.DataFrame:
    """
    Loads the previous Excel file into a pandas DataFrame.
    Standardizes column names to ensure reliable key extraction.
    """
    try:
        df = pd.read_excel(io.BytesIO(file_bytes))
        # Strip whitespace from column headers
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


def get_start_sno(df: pd.DataFrame) -> int:
    """
    Determines the next S.No based on the maximum value in the existing DataFrame.
    Defaults to 1 if empty or invalid.
    """
    if df.empty or "S.No" not in df.columns:
        return 1

    try:
        # Force numeric conversion, coerce errors to NaN
        max_val = pd.to_numeric(df["S.No"], errors='coerce').max()
        if pd.isna(max_val):
            return 1
        return int(max_val) + 1
    except:
        return 1


def get_existing_keys(df: pd.DataFrame) -> set:
    """
    Extracts a set of composite keys (Company + Role + Email) from the dataframe
    for fast deduplication lookup.
    """
    if df.empty:
        return set()

    # Ensure required columns exist
    req_cols = ["Company", "Role", "Email"]
    if not all(col in df.columns for col in req_cols):
        return set()

    keys = set()
    for _, row in df.iterrows():
        # Create normalized key: (company, role, email)
        # using strict lowercase and stripping
        comp = str(row["Company"]).strip().lower()
        role = str(row["Role"]).strip().lower()
        email = str(row["Email"]).strip().lower()

        keys.add((comp, role, email))

    return keys


def is_duplicate(new_job: dict, existing_keys: set) -> bool:
    """
    Checks if the new job's composite key exists in the set of existing keys.
    """
    comp = str(new_job.get("Company", "")).strip().lower()
    role = str(new_job.get("Role", "")).strip().lower()
    email = str(new_job.get("Email", "")).strip().lower()

    key = (comp, role, email)

    return key in existing_keys
