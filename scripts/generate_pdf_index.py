import os
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "data")
OUTPUT_JSON = os.path.join(ROOT_DIR, "website", "src", "app", "api", "download", "pdf_index.json")

def generate_index():
    pdf_files = []
    if not os.path.exists(DATA_DIR):
        print(f"Data directory not found at {DATA_DIR}")
        return
        
    for root, dirs, files in os.walk(DATA_DIR):
        for file in files:
            if file.lower().endswith(".pdf"):
                full_path = os.path.join(root, file)
                # Get path relative to the repository root
                rel_path = os.path.relpath(full_path, ROOT_DIR)
                # Normalize slashes to forward slashes for URLs
                rel_path = rel_path.replace("\\", "/")
                pdf_files.append(rel_path)
                
    pdf_files.sort()
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(pdf_files, f, indent=2, ensure_ascii=False)
        
    print(f"Successfully generated PDF index with {len(pdf_files)} files at {OUTPUT_JSON}")

if __name__ == "__main__":
    generate_index()
