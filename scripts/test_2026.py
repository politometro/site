import os
import pypdf

filepath = os.path.abspath("data/Orçamentos de Estado/Orçamento do Estado 2026.pdf")
if not os.path.exists(filepath):
    # Try with different encodings/names just in case
    print("Does not exist at standard path. Scanning Orçamentos de Estado directory:")
    dir_path = os.path.abspath("data/Orçamentos de Estado")
    if os.path.exists(dir_path):
        for f in os.listdir(dir_path):
            print(f" - {f} (bytes: {f.encode('utf-8')})")
    exit(1)

try:
    reader = pypdf.PdfReader(filepath)
    print(f"Success! Pages: {len(reader.pages)}")
    text = reader.pages[0].extract_text() or ""
    print(f"First page text sample: {text[:200]}")
except Exception as e:
    print(f"Error reading file: {e}")
