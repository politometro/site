import os
import json
import re

# Load parsed excel matrix
with open("scripts/excel_parsed.json", "r", encoding="utf-8") as f:
    excel_data = json.load(f)

# Let's map files on disk
# We will check extracted_chunks.json and extracted_chunks_ocr.json to see what we actually processed
chunks = []
if os.path.exists("scripts/extracted_chunks.json"):
    with open("scripts/extracted_chunks.json", "r", encoding="utf-8") as f:
        chunks.extend(json.load(f))
if os.path.exists("scripts/extracted_chunks_ocr.json"):
    with open("scripts/extracted_chunks_ocr.json", "r", encoding="utf-8") as f:
        chunks.extend(json.load(f))

# Create a set of (party, category, year) of files we actually have
disk_docs = set()
for c in chunks:
    party = c.get("party")
    category = c.get("category")
    year = c.get("year")
    
    # Normalize category name to match Excel columns (e.g. "Legislativas", "Madeira", "Açores")
    cat_norm = category
    if category == "Orçamentos de Estado" or category == "Oramentos de Estado":
        cat_norm = "Orçamento de Estado"
        
    disk_docs.add((party, cat_norm, year))

print(f"Total unique documents on disk: {len(disk_docs)}")

# Let's do the cross-reference
matrix_updated = []
found_new = []
missing_files = []

EXTRA_PRE_1999_COLS = [
    "Legislativas - 1997",
    "Legislativas - 1995",
    "Legislativas - 1991",
    "Legislativas - 1987",
    "Legislativas - 1985",
    "Legislativas - 1983",
    "Legislativas - 1980",
    "Legislativas - 1979",
    "Legislativas - 1976",
    "Legislativas - 1975"
]

for row in excel_data:
    party = row["party"]
    # Skip rows that are "Extras" or nan
    if party in ["Extras", "nan"] or not isinstance(party, str):
        continue
        
    updated_cells = []
    
    # Inject pre-1999 columns dynamically
    cells = list(row["cells"])
    eur_idx = -1
    for idx, c in enumerate(cells):
        if c["col"] == "Europeias 1999":
            eur_idx = idx
            break
            
    if eur_idx != -1:
        for col_name in reversed(EXTRA_PRE_1999_COLS):
            cells.insert(eur_idx + 1, {
                "col": col_name,
                "value": None,
                "color": None
            })
            
    for cell in cells:
        col = cell["col"]
        val = cell["value"]
        color = cell["color"]
        
        # Determine year and category from column header
        # Header formats: "Legislativas - 2025", "Orçamento de Estado", "Declaração de Princípios", etc.
        year = None
        category = col
        if " - " in col:
            parts = col.split(" - ")
            category = parts[0]
            try:
                year = int(parts[1])
            except:
                pass
        elif " 1999" in col: # Europeias 1999
            category = "Europeias"
            year = 1999
            
        # Determine current Excel status
        excel_status = "not_searched"
        if val == "Sim" and color != "FFFF0000":
            excel_status = "available"
        elif color == "FFFF0000" or val == "Não":
            excel_status = "not_found"
            
        # Special columns
        if col == "Declaração de Princípios" or col == "Declaraço de Princípios":
            # Check if we have this document
            # Usually has year in metadata, but Declaração de Princípios is categorized as "Declaração de Princípios" on disk
            category = "Declaração de Princípios"
            
        # Cross reference with disk
        on_disk = False
        # We try to match:
        # 1. Exact match (party, category, year)
        # 2. Or, if it's "Declaração de Princípios", match by party and category
        # 3. Or, if it's "Orçamento de Estado", match by category and year
        if category == "Declaração de Princípios":
            on_disk = any(d[0] == party and (d[1] == "Declaração de Princípios" or d[1] == "Declaraço de Princípios") for d in disk_docs)
        elif category == "Orçamento de Estado" and year is not None:
            # OE is not party-specific on disk, it's categorized under Outro or similar, so match by category and year
            on_disk = any(d[1] == "Orçamento de Estado" and d[2] == year for d in disk_docs)
        else:
            # Standard party program: match party, category, and year
            # Party normalization: Excel might have "CDU - PCP/PEV", on disk it might be "CDU"
            # Excel "ADN/PDR", on disk "ADN" or "PDR"
            on_disk = (party, category, year) in disk_docs
            if not on_disk:
                COALITION_MEMBERS = {
                    "AD": {"PSD", "CDS", "PPM"},
                    "A21": {"MPT/ALTERNATIVA 21", "MPT"},
                    "ALTERNATIVA 21": {"MPT/ALTERNATIVA 21", "MPT"},
                    "BASTA": {"PPM", "CHEGA", "PPV"},
                    "PAF": {"PSD", "CDS"},
                    "PORTUGAL À FRENTE": {"PSD", "CDS"},
                    "PORTUGAL A FRENTE": {"PSD", "CDS"},
                    "ALIANÇA PORTUGAL": {"PSD", "CDS"},
                    "ALIANCA PORTUGAL": {"PSD", "CDS"},
                    "SOMOS MADEIRA": {"PSD", "CDS"}
                }
                
                def check_match(dp, ep):
                    dp_upper = dp.strip().upper()
                    ep_upper = ep.strip().upper()
                    if dp_upper == "PLS" and ep_upper == "PARTIDO LIBERAL SOCIAL":
                        return True
                    if dp_upper == "PARTIDO LIBERAL SOCIAL" and ep_upper == "PLS":
                        return True
                    dp_tokens = set(t.strip().upper() for t in re.split(r'[\s/-]+', dp) if t.strip())
                    ep_tokens = set(t.strip().upper() for t in re.split(r'[\s/-]+', ep) if t.strip())
                    if len(dp_tokens.intersection(ep_tokens)) > 0:
                        return True
                    for coal, members in COALITION_MEMBERS.items():
                        if coal in dp_upper or dp_upper in coal:
                            for m in members:
                                m_tokens = set(t.strip().upper() for t in re.split(r'[\s/-]+', m) if t.strip())
                                if len(m_tokens.intersection(ep_tokens)) > 0:
                                    return True
                    return False
                
                on_disk = any(
                    check_match(d[0], party)
                    and d[1] == category
                    and d[2] == year
                    for d in disk_docs
                )

        # Determine corrected status
        corrected_status = "not_searched"
        if on_disk:
            corrected_status = "available"
            if excel_status != "available":
                found_new.append({
                    "party": party,
                    "col": col,
                    "excel_status": excel_status
                })
        else:
            if excel_status == "available":
                # Excel says available, but we don't have it on disk!
                missing_files.append({
                    "party": party,
                    "col": col
                })
                # Keep it as "available" or mark as "not_found"?
                # The user says "confirm if it is really correct", so if we don't have it on disk, it's not correct!
                # But wait, could it be in the data folder but not processed?
                # We processed all files successfully, so if it's not in chunks, it's not on disk!
                corrected_status = "not_found"
            elif excel_status == "not_found":
                corrected_status = "not_found"
                
        updated_cells.append({
            "col": col,
            "excel_status": excel_status,
            "status": corrected_status,
            "value": val,
            "on_disk": on_disk
        })
        
    matrix_updated.append({
        "party": party,
        "cells": updated_cells
    })

print(f"\n--- Cross Reference Summary ---")
print(f"Found on disk but marked Red/Gray in Excel (Found New): {len(found_new)}")
for item in found_new[:15]:
    print(f" - {item['party']} ({item['col']}) [Excel was: {item['excel_status']}]")
if len(found_new) > 15:
    print(f"   ... and {len(found_new)-15} more.")

print(f"\nMarked Green in Excel but MISSING from disk: {len(missing_files)}")
for item in missing_files[:15]:
    print(f" - {item['party']} ({item['col']})")
if len(missing_files) > 15:
    print(f"   ... and {len(missing_files)-15} more.")

# Write updated matrix for website
output_dir = "website/src/data"
os.makedirs(output_dir, exist_ok=True)
headers = [cell["col"] for cell in matrix_updated[0]["cells"]]
with open(os.path.join(output_dir, "political_docs.json"), "w", encoding="utf-8") as f:
    json.dump({
        "headers": [h for h in headers if h not in ["Unnamed_45", "Unnamed_47", "Unnamed_48"]],
        "rows": matrix_updated
    }, f, indent=2, ensure_ascii=False)

print("\nSaved updated matrix to website/src/data/political_docs.json")
