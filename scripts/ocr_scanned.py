import os
import re
import json
import pypdf
import asyncio
import sys
import unicodedata
from winrt.windows.storage import StorageFile
from winrt.windows.data.pdf import PdfDocument
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.storage.streams import InMemoryRandomAccessStream
from winrt.windows.globalization import Language

workspace = "."
data_dir = os.path.join(workspace, "data")
main_chunks_file = os.path.join(workspace, "scripts", "extracted_chunks.json")
ocr_chunks_file = os.path.join(workspace, "scripts", "extracted_chunks_ocr.json")

# Helper to normalize and clean IDs to be strictly ASCII
def clean_vector_id(raw_id):
    # Convert accents to their base letters (e.g. Açores -> Acores)
    normalized = unicodedata.normalize('NFKD', raw_id).encode('ASCII', 'ignore').decode('ASCII')
    # Replace any character that is not a letter, digit, underscore, or dash with underscore
    cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '_', normalized)
    return cleaned

# Metadata mapping helper
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
    if "psd" in lower or "ppd" in lower:
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
        year = 1976
        
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

# Run Windows Native OCR on a PDF file
async def ocr_pdf_winrt(filepath, meta):
    abs_path = os.path.abspath(filepath)
    try:
        file = await StorageFile.get_file_from_path_async(abs_path)
        pdf_doc = await PdfDocument.load_from_file_async(file)
        
        # Try Portuguese language, fallback to system languages
        lang = Language("pt-PT")
        engine = OcrEngine.try_create_from_language(lang)
        if not engine:
            print("  [Warning] Portuguese language pack not found. Falling back to system default OCR language.")
            engine = OcrEngine.try_create_from_user_profile_languages()
            
        if not engine:
            print("  [Error] No OCR engine could be initialized on this system.")
            return []

        pages_text = []
        for i in range(pdf_doc.page_count):
            page = pdf_doc.get_page(i)
            stream = InMemoryRandomAccessStream()
            
            # Render page as bitmap image
            await page.render_to_stream_async(stream)
            decoder = await BitmapDecoder.create_async(stream)
            software_bitmap = await decoder.get_software_bitmap_async()
            
            # OCR execution
            result = await engine.recognize_async(software_bitmap)
            pages_text.append(result.text or "")
            
            # Clean up WinRT objects immediately to avoid memory locks
            software_bitmap.close()
            stream.close()
            page.close()
            
        return pages_text
    except Exception as e:
        print(f"  [Error] Failed to OCR {meta['rel_path']}: {e}")
        return []

async def ocr_processing_loop():
    # Load existing main text chunks
    main_files = set()
    if os.path.exists(main_chunks_file):
        with open(main_chunks_file, "r", encoding="utf-8") as f:
            main_chunks = json.load(f)
            main_files = set(c["rel_path"] for c in main_chunks)
        print(f"Loaded {len(main_chunks)} main text chunks from database.")

    # Load existing OCR chunks if any
    ocr_chunks_db = []
    ocr_files = set()
    if os.path.exists(ocr_chunks_file):
        try:
            with open(ocr_chunks_file, "r", encoding="utf-8") as f:
                ocr_chunks_db = json.load(f)
                ocr_files = set(c["rel_path"] for c in ocr_chunks_db)
            print(f"Loaded {len(ocr_chunks_db)} existing OCR chunks.")
        except Exception as e:
            print(f"Warning: Could not read existing OCR file, starting fresh. Error: {e}")
            ocr_chunks_db = []

    # Combined processed files (either text or already OCRed)
    processed_files = main_files.union(ocr_files)
    
    scanned_files_to_ocr = []
    
    print("Scanning directory for scanned PDFs that need OCR...")
    for root, dirs, files in os.walk(data_dir):
        for file in files:
            if not file.endswith(".pdf"):
                continue
                
            filepath = os.path.join(root, file)
            meta = get_file_metadata(filepath, file)
            
            if meta["rel_path"] in processed_files:
                continue # Already processed (either text or OCRed)
                
            # Check if it's scanned (pypdf returns < 20 words across first 5 pages)
            try:
                reader = pypdf.PdfReader(filepath)
                num_pages = len(reader.pages)
                
                total_text = ""
                for page_num in range(min(5, num_pages)):
                    page = reader.pages[page_num]
                    total_text += page.extract_text() or ""
                
                total_words = len(total_text.split())
                if total_words < 20 and num_pages > 0:
                    scanned_files_to_ocr.append((filepath, meta, num_pages))
            except Exception as e:
                print(f"  Error reading {meta['rel_path']}: {e}")

    total_scanned = len(scanned_files_to_ocr)
    print(f"Found {total_scanned} scanned PDFs that need OCR.")
    
    if total_scanned == 0:
        print("No scanned PDFs require OCR. Exiting.")
        return
        
    processed_count = 0
    ocr_chunks_added = 0

    for idx, (filepath, meta, num_pages) in enumerate(scanned_files_to_ocr):
        print(f"[{idx+1}/{total_scanned}] Running native Windows OCR on {meta['rel_path']} ({num_pages} pages)...")
        
        pages_text = await ocr_pdf_winrt(filepath, meta)
        
        if not pages_text:
            print("  Failed to extract any text.")
            continue
            
        print(f"  Extracted {len(pages_text)} pages of text. Chunking...")
        
        file_chunks_added = 0
        for page_num, text in enumerate(pages_text):
            # Clean simple whitespace
            clean_text = re.sub(r'\s+', ' ', text).strip()
            
            if clean_text:
                p_chunks = chunk_text(clean_text, chunk_size=1000, overlap=200)
                for chunk_idx, ch in enumerate(p_chunks):
                    raw_id = f"{meta['party']}_{meta['year']}_{meta['category']}_{meta['filename']}_ocr_p{page_num + 1}_c{chunk_idx}"
                    ocr_chunks_db.append({
                        "id": clean_vector_id(raw_id),
                        "text": ch,
                        "page": page_num + 1,
                        "party": meta["party"],
                        "year": meta["year"],
                        "category": meta["category"],
                        "filename": meta["filename"],
                        "rel_path": meta["rel_path"]
                    })
                    file_chunks_added += 1
                    ocr_chunks_added += 1
                    
        print(f"  Processed successfully! Added {file_chunks_added} chunks.")
        processed_count += 1
        
        # Save OCR file immediately
        with open(ocr_chunks_file, "w", encoding="utf-8") as f:
            json.dump(ocr_chunks_db, f, ensure_ascii=False, indent=2)

    print("\n--- OCR Processing Summary ---")
    print(f"Successfully OCR-ed: {processed_count}/{total_scanned} files")
    print(f"Total new chunks added to database: {ocr_chunks_added}")
    print(f"Total chunks in OCR database: {len(ocr_chunks_db)}")
    print(f"OCR database saved to {ocr_chunks_file}")

if __name__ == "__main__":
    asyncio.run(ocr_processing_loop())
