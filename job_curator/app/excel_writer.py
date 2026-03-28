# import pandas as pd
# import io
# import zipfile
# from datetime import datetime


# def generate_multi_output(stage1_data: list, final_master_df: pd.DataFrame) -> io.BytesIO:
#     """
#     Generates a ZIP file containing:
#     1. Stage1_Diagnostic_Log.xlsx (Current batch diagnostics)
#     2. Final_Master_Tracker_<date>.xlsx (Merged History + New Batch)
#     """

#     # --- 1. STAGE 1: DIAGNOSTIC LOG (Current Batch Only) ---
#     df_stage1 = pd.DataFrame(stage1_data)
#     if not df_stage1.empty and 'debug_log' in df_stage1.columns:
#         df_stage1['debug_log'] = df_stage1['debug_log'].apply(
#             lambda x: " | ".join(x) if isinstance(x, list) else str(x)
#         )

#     out_stage1 = io.BytesIO()
#     with pd.ExcelWriter(out_stage1, engine='openpyxl') as writer:
#         df_stage1.to_excel(writer, index=False,
#                            sheet_name="Stage1_Diagnostics")
#     out_stage1.seek(0)

#     # --- 2. STAGE 2: FINAL MASTER TRACKER (Merged) ---
#     # Enforce exact column order
#     cols = [
#         "S.No", "Company", "Role", "Exp", "Location",
#         "Mode", "Email", "Source_PDF", "Notes", "Domain", "Last Updated"
#     ]

#     # Ensure all columns exist in the final dataframe
#     if not final_master_df.empty:
#         for c in cols:
#             if c not in final_master_df.columns:
#                 final_master_df[c] = "N/A"
#         # Reorder and filter columns
#         final_master_df = final_master_df[cols]
#     else:
#         # Create empty DF with correct columns if nothing exists
#         final_master_df = pd.DataFrame(columns=cols)

#     out_master = io.BytesIO()
#     with pd.ExcelWriter(out_master, engine='openpyxl') as writer:
#         final_master_df.to_excel(
#             writer, index=False, sheet_name="Master_Tracker")

#         # Auto-adjust column widths
#         ws = writer.sheets['Master_Tracker']
#         for column in ws.columns:
#             max_len = 0
#             col_letter = column[0].column_letter
#             for cell in column:
#                 try:
#                     val_len = len(str(cell.value))
#                     if val_len > max_len:
#                         max_len = val_len
#                 except:
#                     pass
#             ws.column_dimensions[col_letter].width = max_len + 2

#     out_master.seek(0)

#     # --- 3. ZIP GENERATION ---
#     zip_buffer = io.BytesIO()
#     date_str = datetime.now().strftime('%Y-%m-%d')

#     with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zf:
#         zf.writestr("Stage1_Diagnostic_Log.xlsx", out_stage1.getvalue())
#         zf.writestr(
#             f"Final_Master_Tracker_{date_str}.xlsx", out_master.getvalue())

#     zip_buffer.seek(0)
#     return zip_buffer


import pandas as pd
import io


def generate_master_excel(final_df: pd.DataFrame) -> io.BytesIO:
    """
    Generates a SINGLE Excel file containing the Final Master Tracker data.
    """
    # Enforce exact column order
    cols = [
        "S.No", "Company", "Role", "Exp", "Location",
        "Mode", "Email", "Source_PDF", "Notes", "Domain", "Last Updated"
    ]

    # Ensure all columns exist in the dataframe
    if not final_df.empty:
        for c in cols:
            if c not in final_df.columns:
                final_df[c] = "N/A"
        # Reorder and filter columns to match strict requirement
        final_df = final_df[cols]
    else:
        # Create empty DataFrame with correct columns if no data
        final_df = pd.DataFrame(columns=cols)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        final_df.to_excel(writer, index=False, sheet_name="Master_Tracker")

        # Auto-adjust column widths
        ws = writer.sheets['Master_Tracker']
        for column in ws.columns:
            max_len = 0
            col_letter = column[0].column_letter
            for cell in column:
                try:
                    val_len = len(str(cell.value))
                    if val_len > max_len:
                        max_len = val_len
                except:
                    pass
            ws.column_dimensions[col_letter].width = max_len + 2

    output.seek(0)
    return output
