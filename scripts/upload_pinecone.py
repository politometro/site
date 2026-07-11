import os
import json
import sys
import time

# We check if pinecone client is installed, and if not, we guide the user to install it
try:
    from pinecone import Pinecone
except ImportError:
    print("Error: The 'pinecone' library is not installed.")
    print("Please install it using: pip install pinecone")
    sys.exit(1)

script_dir = os.path.dirname(os.path.abspath(__file__))
workspace = os.path.abspath(os.path.join(script_dir, os.pardir))
chunks_file = os.path.join(script_dir, "extracted_chunks.json")
ocr_chunks_file = os.path.join(script_dir, "extracted_chunks_ocr.json")

if not os.path.exists(chunks_file):
    print(f"Error: Main chunks file not found at {chunks_file}. Please run scripts/extract_text.py first.")
    sys.exit(1)

# Get API credentials
# We look for environment variables or ask the user
api_key = os.environ.get("PINECONE_API_KEY")
index_name = os.environ.get("PINECONE_INDEX_NAME")

if not api_key:
    print("Error: PINECONE_API_KEY environment variable is not set. Please configure it in your environment.")
    sys.exit(1)
if not index_name:
    index_name = "politometro"


def load_chunks(path, label):
    if not os.path.exists(path):
        print(f"Warning: {label} file not found at {path}.")
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print(f"Warning: {label} file at {path} does not contain a JSON list.")
        return []

    print(f"Loaded {len(data)} {label} chunks.")
    return data


def is_quota_error(error):
    message = str(error).lower()
    return (
        "resource_exhausted" in message
        or "embedding token limit" in message
        or "quota" in message
        or "limit" in message and "embedding" in message
    )


def load_local_embedder():
    project_venv_site_packages = os.path.join(workspace, ".venv", "Lib", "site-packages")
    if os.path.isdir(project_venv_site_packages) and project_venv_site_packages not in sys.path:
        sys.path.insert(0, project_venv_site_packages)

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("Error: Pinecone embedding quota was exhausted and local embeddings are not available.")
        print("Install them with: pip install sentence-transformers")
        sys.exit(1)

    model_name = os.environ.get("LOCAL_EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
    print(f"Using local embedding model: {model_name}")
    return SentenceTransformer(model_name)


local_embedder = None
use_local_embeddings = True  # Set to True by default since the Pinecone embedding quota is exhausted

# Silence Hugging Face Hub warnings about unauthenticated requests
import logging
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)


def generate_embeddings(texts):
    global local_embedder, use_local_embeddings

    if not use_local_embeddings:
        try:
            return pc.inference.embed(
                model="multilingual-e5-large",
                inputs=texts,
                parameters={"input_type": "passage", "truncate": "END"}
            )
        except Exception as e:
            if not is_quota_error(e):
                raise
            print("\nWarning: Pinecone embedding quota reached. Switching permanently to local embeddings for the remaining batches.")
            use_local_embeddings = True

    if local_embedder is None:
        local_embedder = load_local_embedder()

    return local_embedder.encode(
        [f"passage: {text}" for text in texts],
        batch_size=min(32, len(texts)),
        normalize_embeddings=True,
        show_progress_bar=False,
    )

print("\nConnecting to Pinecone...")
try:
    pc = Pinecone(api_key=api_key)
except Exception as e:
    print(f"Error connecting to Pinecone: {e}")
    sys.exit(1)

# Check if index exists, and if not, show instructions
try:
    indexes = pc.list_indexes()
    index_names = [idx.name for idx in indexes]
    if index_name not in index_names:
        print(f"\nIndex '{index_name}' does not exist in your Pinecone account.")
        print("Please create a serverless index in your Pinecone console with:")
        print("  - Dimension: 1024 (matching the 'multilingual-e5-large' model)")
        print("  - Metric: cosine")
        print("  - Cloud: AWS or GCP")
        print("  - Region: us-east-1 (or other serverless regions)")
        sys.exit(1)
except Exception as e:
    print(f"Error verifying index: {e}")
    sys.exit(1)

main_chunks = load_chunks(chunks_file, "main")
ocr_chunks = load_chunks(ocr_chunks_file, "OCR")

if not main_chunks and ocr_chunks:
    print("Warning: main chunks file is empty, so only OCR chunks will be uploaded.")

# Load already uploaded files tracking to support incremental uploads
uploaded_files_track = os.path.join(script_dir, "uploaded_files.json")
uploaded_set = set()
if os.path.exists(uploaded_files_track) and "--force" not in sys.argv:
    try:
        with open(uploaded_files_track, "r", encoding="utf-8") as f:
            uploaded_set = set(json.load(f))
        print(f"Loaded {len(uploaded_set)} already uploaded files from tracking. These will be skipped.")
    except Exception as e:
        print(f"Warning loading upload tracking: {e}")
elif "--force" in sys.argv:
    print("Force mode enabled (--force). Re-uploading all files.")

chunks = []
seen_ids = set()
files_in_run = set()

for chunk in main_chunks + ocr_chunks:
    chunk_id = chunk.get("id")
    if not chunk_id or chunk_id in seen_ids:
        continue
    
    rel_path = chunk.get("rel_path")
    if rel_path in uploaded_set:
        continue
        
    seen_ids.add(chunk_id)
    chunks.append(chunk)
    if rel_path:
        files_in_run.add(rel_path)

if not chunks:
    print("\nNo new or changed files detected. Pinecone index is already up to date!")
    sys.exit(0)

print(f"Total unique new chunks to upload: {len(chunks)} (associated with {len(files_in_run)} new files).")
print("Uploading chunks in batches using Pinecone Inference API (multilingual-e5-large)...")

index = pc.Index(index_name)

# We will upload in batches of 50
batch_size = 50
total_uploaded = 0

for i in range(0, len(chunks), batch_size):
    batch = chunks[i:i+batch_size]
    texts = [item["text"] for item in batch]
    
    max_retries = 6
    retry_delay = 1.0
    success = False
    
    for attempt in range(max_retries):
        try:
            # Generate embeddings using Pinecone inference, with local fallback if the monthly quota is exhausted.
            res = generate_embeddings(texts)
            
            vectors = []
            for idx, item in enumerate(batch):
                vector_id = item["id"]
                raw_embedding = res[idx].values if hasattr(res[idx], "values") else res[idx]
                # Convert numpy float32 to native Python float for JSON serialization
                embedding = raw_embedding.tolist() if hasattr(raw_embedding, "tolist") else list(raw_embedding)
                
                metadata = {
                    "text": item["text"],
                    "page": item["page"],
                    "party": item["party"],
                    "year": item["year"] if item["year"] else 0,
                    "category": item["category"],
                    "filename": item["filename"]
                }
                
                vectors.append({
                    "id": vector_id,
                    "values": embedding,
                    "metadata": metadata
                })
                
            # Upsert vectors to Pinecone
            index.upsert(vectors=vectors)
            total_uploaded += len(batch)
            success = True
            break
            
        except Exception as e:
            print(f"\n  [Warning] Attempt {attempt+1}/{max_retries} failed for batch {i//batch_size + 1}: {e}")
            if attempt < max_retries - 1:
                sleep_time = retry_delay * (2 ** attempt)
                print(f"  Retrying in {sleep_time:.1f} seconds...")
                time.sleep(sleep_time)
            else:
                print(f"  [Error] Batch {i//batch_size + 1} failed permanently. Exiting to prevent silent failure.")
                sys.exit(1)
                
    if success:
        print(f"  Uploaded chunks {i+1} to {min(i+batch_size, len(chunks))} / {len(chunks)}...")
        time.sleep(0.3)
        
print(f"\nUpload complete! Successfully uploaded {total_uploaded} chunks to Pinecone index '{index_name}'.")

# Update uploaded tracking list
if total_uploaded > 0:
    new_uploaded_set = uploaded_set.union(files_in_run)
    try:
        with open(uploaded_files_track, "w", encoding="utf-8") as f:
            json.dump(list(new_uploaded_set), f, indent=2, ensure_ascii=False)
        print(f"Updated upload tracking file: {len(new_uploaded_set)} total files tracked.")
    except Exception as e:
        print(f"Warning saving upload tracking: {e}")
