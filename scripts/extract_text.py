import os
import re
import json
import sys
import pypdf
import unicodedata

workspace = "."
data_dir = os.path.join(workspace, "data")
output_chunks_file = os.path.join(workspace, "scripts", "extracted_chunks.json")

os.makedirs(os.path.dirname(output_chunks_file), exist_ok=True)

# Helper to normalize and clean IDs to be strictly ASCII
def clean_vector_id(raw_id):
    # Convert accents to their base letters (e.g. Açores -> Acores)
    normalized = unicodedata.normalize('NFKD', raw_id).encode('ASCII', 'ignore').decode('ASCII')
    # Replace any character that is not a letter, digit, underscore, or dash with underscore
    cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '_', normalized)
    return cleaned

# Re-use our mapping logic to identify metadata for each chunk
def get_file_metadata(filepath, filename):
    rel_path = os.path.relpath(filepath, data_dir)
    parts = rel_path.split(os.sep)
    category = parts[0]
    
    # Get year
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", filename)
    if not year_match:
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", filepath)
    year = int(year_match.group(1)) if year_match else None
    
    # Special cases
    if "70-medidas-para-reerguer-portugal-chega" in filename.lower():
        year = 2024
        category = "Legislativas"
        
    # Get party
    lower = filename.lower()
    party = "Outro"
    if "ad " in lower or "ad_" in lower or lower.startswith("ad") or "aliança democrática" in lower:
        party = "AD"
    elif "paf" in lower or "portugal à frente" in lower:
        party = "PaF"
    elif "psd" in lower or "ppd" in lower:
        party = "PSD"
    elif "ps" in lower and not "psd" in lower and not "psn" in lower:
        party = "PS"
    elif "chega" in lower:
        party = "CHEGA"
    elif "il" in lower or "iniciativa liberal" in lower:
        party = "IL"
    elif "be" in lower or "bloco" in lower:
        party = "BE"
    elif "cdu" in lower or "pcp" in lower or "pev" in lower:
        party = "CDU"
    elif "livre" in lower:
        party = "LIVRE"
    elif "pan" in lower:
        party = "PAN"
    elif "cds" in lower:
        party = "CDS"
    elif "adn" in lower or "pdr" in lower:
        party = "ADN"
    elif "rir" in lower:
        party = "RIR"
    elif "jpp" in lower:
        party = "JPP"
    elif "nova direita" in lower or "nd" in lower or "nd " in lower:
        party = "NOVA DIREITA"
    elif "pctp" in lower or "mrpp" in lower:
        party = "PCTP/MRPP"
    elif "volt" in lower:
        party = "VOLT"
    elif "ergue-te" in lower or "pnr" in lower:
        party = "ERGUE-TE/PNR"
    elif "mpt" in lower:
        party = "MPT"
    elif "ptp" in lower:
        party = "PTP"
    elif "nós" in lower or "nos cidadãos" in lower or "nós, cidadãos" in lower:
        party = "NÓS CIDADÃOS"
    elif "ppm" in lower:
        party = "PPM"
    elif "mas" in lower:
        party = "MAS"
    elif "purp" in lower:
        party = "PURP"
    elif "mep" in lower:
        party = "MEP"
    elif "pnd" in lower:
        party = "PND"
    elif "ppv" in lower:
        party = "PPV"
    elif "pous" in lower:
        party = "POUS"
    elif "sda" in lower or "pda" in lower:
        party = "PDA"
    elif "humanista" in lower or "ph" in lower:
        party = "PH"
    elif "mms" in lower:
        party = "MMS"
    elif "psn" in lower:
        party = "PSN"
    elif "udp" in lower:
        party = "UDP"
    elif "md" in lower:
        party = "MD"
    elif "liberal social" in lower or "pls" in lower:
        party = "PLS"
    elif "libertário" in lower or "pl" in lower:
        party = "Partido Libertário"
    elif "constituição" in lower:
        party = "Constituição"
        category = "Constituição"
        year = 1976 # base year
        
    return {
        "filename": filename,
        "rel_path": rel_path,
        "category": category,
        "year": year,
        "party": party
    }

# Chunking helper
def chunk_text(text, chunk_size=1000, overlap=200):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

# Main processing loop
chunks_db = []
scanned_files = []
error_files = []
processed_count = 0

print("Scanning directory and extracting text...")

for root, dirs, files in os.walk(data_dir):
    for file in files:
        if not file.endswith(".pdf"):
            continue
            
        filepath = os.path.join(root, file)
        meta = get_file_metadata(filepath, file)
        
        try:
            reader = pypdf.PdfReader(filepath)
            num_pages = len(reader.pages)
            
            total_text = ""
            page_chunks = []
            
            # Extract text page by page
            for page_num in range(num_pages):
                page = reader.pages[page_num]
                text = page.extract_text() or ""
                
                # Clean simple whitespace
                text = re.sub(r'\s+', ' ', text).strip()
                
                if text:
                    # We can store the chunks per page
                    # So we know exactly which page it came from
                    p_chunks = chunk_text(text, chunk_size=1000, overlap=200)
                    for chunk_idx, ch in enumerate(p_chunks):
                        page_chunks.append({
                            "text": ch,
                            "page": page_num + 1,
                            "chunk_index": chunk_idx
                        })
            
            # Check if we got any text
            total_words = sum(len(c["text"].split()) for c in page_chunks)
            if total_words < 20 and num_pages > 0:
                scanned_files.append((meta["rel_path"], num_pages))
                continue
                
            # Add to database
            for pc in page_chunks:
                raw_id = f"{meta['party']}_{meta['year']}_{meta['category']}_{meta['filename']}_p{pc['page']}_c{pc['chunk_index']}"
                chunks_db.append({
                    "id": clean_vector_id(raw_id),
                    "text": pc["text"],
                    "page": pc["page"],
                    "party": meta["party"],
                    "year": meta["year"],
                    "category": meta["category"],
                    "filename": meta["filename"],
                    "rel_path": meta["rel_path"]
                })
                
            processed_count += 1
            print(f"  Processed: {meta['rel_path']} ({num_pages} pages, {len(page_chunks)} chunks)")
            
        except Exception as e:
            error_files.append((meta["rel_path"], str(e)))
            print(f"  Error processing {meta['rel_path']}: {e}")

# Save database
print(f"\nSaving {len(chunks_db)} chunks to {output_chunks_file}...")
with open(output_chunks_file, "w", encoding="utf-8") as f:
    json.dump(chunks_db, f, ensure_ascii=False)
    f.flush()
    os.fsync(f.fileno())

# Verify the file was written correctly
verify_size = os.path.getsize(output_chunks_file)
print(f"File written: {verify_size:,} bytes")
if verify_size < 100:
    print("ERROR: File appears to be empty or corrupt!")
    sys.exit(1)

print("\n--- Extraction Summary ---")
print(f"Successfully processed: {processed_count} files")
print(f"Total chunks extracted: {len(chunks_db)}")
print(f"Scanned files skipped (require OCR): {len(scanned_files)}")
for f, p in scanned_files:
    print(f"  - {f} ({p} pages)")
print(f"Files with errors: {len(error_files)}")
for f, err in error_files:
    print(f"  - {f}: {err}")
    
print(f"\nChunks database saved to {output_chunks_file}")
