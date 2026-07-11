import openpyxl
import os

excel_path = "Polítólogo Português.xlsx"
if not os.path.exists(excel_path):
    # Try finding with glob or listing
    print("Excel file not found in root. Checking directory:")
    for f in os.listdir("."):
        if "xlsx" in f.lower():
            excel_path = f
            print(f"Found alternative excel path: {excel_path}")
            break

if not os.path.exists(excel_path):
    print("No excel file found at all.")
    exit(1)

wb = openpyxl.load_workbook(excel_path, data_only=True)
sheet = wb.active
print(f"Sheet Title: {sheet.title}")
print(f"Dimensions: {sheet.dimensions}")

# Print headers
headers = [cell.value for cell in sheet[1]]
print("Headers:", headers)

# Inspect first few rows and their colors
for row_idx in range(2, 20):  # first 18 rows
    row_cells = sheet[row_idx]
    party = row_cells[0].value
    if party is None:
        continue
    
    print(f"\nParty: {party}")
    for col_idx, cell in enumerate(row_cells):
        val = cell.value
        fill = cell.fill
        color = None
        if fill and fill.fill_type == "solid" and fill.start_color:
            color = fill.start_color.rgb
        if val is not None or color:
            col_name = headers[col_idx] if col_idx < len(headers) else f"Col {col_idx}"
            print(f"  [{col_name}]: value={val}, color={color}")
