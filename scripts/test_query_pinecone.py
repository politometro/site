import os
from pinecone import Pinecone

# Load keys from website/.env.local manually
env_path = os.path.join("website", ".env.local")
api_key = None
index_name = "politometro"

if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("PINECONE_API_KEY="):
                api_key = line.split("=", 1)[1].strip()
            if line.startswith("PINECONE_INDEX_NAME="):
                index_name = line.split("=", 1)[1].strip()

print(f"Index Name: {index_name}")
if not api_key:
    print("Error: Pinecone API Key not found in website/.env.local!")
    exit(1)

try:
    print("Connecting to Pinecone...")
    pc = Pinecone(api_key=api_key)
    
    # List indexes
    indexes = pc.list_indexes()
    print("Available indexes:")
    for idx in indexes:
        print(f" - Name: {idx.name}, Host: {idx.host}, Dimension: {idx.dimension}, Metric: {idx.metric}")
        
    if index_name not in [idx.name for idx in indexes]:
        print(f"Error: Index '{index_name}' does not exist in Pinecone!")
        exit(1)
        
    index = pc.Index(index_name)
    stats = index.describe_index_stats()
    print(f"Successfully connected to index!")
    print(f"Index stats: {stats}")
    
except Exception as e:
    print(f"Error connecting to Pinecone: {e}")
