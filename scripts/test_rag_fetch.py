import os
import requests

# Load keys from website/.env.local
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

# Step 1: Describe Index (Get Host)
try:
    print("\n--- Step 1: Getting index host from control plane ---")
    url = f"https://api.pinecone.io/indexes/{index_name}"
    headers = {"Api-Key": api_key}
    
    res = requests.get(url, headers=headers)
    print(f"Response status: {res.status_code}")
    if res.status_code != 200:
        print(f"Error describing index: {res.text}")
        exit(1)
        
    data = res.json()
    host = data.get("host")
    print(f"Successfully retrieved index host: {host}")
    
    # Step 2: Embed Query
    print("\n--- Step 2: Generating embedding for test query ---")
    embed_url = "https://api.pinecone.io/embed"
    embed_payload = {
        "model": "multilingual-e5-large",
        "inputs": [{"text": "Qual é a posição do PSD sobre a educação?"}],
        "parameters": {"input_type": "query"}
    }
    embed_headers = {"Api-Key": api_key, "X-Pinecone-API-Version": "2025-10"}
    embed_res = requests.post(embed_url, json=embed_payload, headers=embed_headers)
    print(f"Embed status: {embed_res.status_code}")
    if embed_res.status_code != 200:
        print(f"Error generating embedding: {embed_res.text}")
        exit(1)
        
    embed_data = embed_res.json()
    # The output structure is usually list of embeddings
    # print(embed_data)
    vector = embed_data.get("data", [{}])[0].get("values")
    if not vector:
        # Check if alternative key structure
        vector = embed_res.json()[0].values
    print(f"Successfully generated embedding vector of length: {len(vector)}")
    
    # Step 3: Query index
    print("\n--- Step 3: Querying the index host ---")
    query_url = f"https://{host}/query"
    query_payload = {
        "vector": vector,
        "topK": 5,
        "includeMetadata": True
    }
    
    query_res = requests.post(query_url, json=query_payload, headers=headers)
    print(f"Query status: {query_res.status_code}")
    if query_res.status_code != 200:
        print(f"Error querying index: {query_res.text}")
        exit(1)
        
    query_data = query_res.json()
    matches = query_data.get("matches", [])
    print(f"Successfully queried! Found {len(matches)} matches.")
    for idx, match in enumerate(matches):
        print(f" [{idx+1}] Score: {match.get('score'):.4f}, Metadata: {match.get('metadata', {}).get('filename')} (P.{match.get('metadata', {}).get('page')})")
        
except Exception as e:
    print(f"Exception raised: {e}")
