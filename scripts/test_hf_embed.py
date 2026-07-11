import requests
import json

# Test Hugging Face inference API for multilingual-e5-large
text = "Qual é a posição do PSD sobre a educação?"
hf_url = "https://api-inference.huggingface.co/models/intfloat/multilingual-e5-large"

# We prefix with "query: " because e5 requires it for queries
payload = {
    "inputs": [f"query: {text}"]
}

headers = {}
# If they have a token, we can use it, but let's try anonymous first
res = requests.post(hf_url, json=payload, headers=headers)
print(f"HF Status: {res.status_code}")
if res.status_code == 200:
    data = res.json()
    # It might return a list of embeddings
    print("Type of data:", type(data))
    if isinstance(data, list) and len(data) > 0:
        embedding = data[0]
        print(f"Embedding length: {len(embedding)}")
        print(f"Sample values: {embedding[:5]}")
        
        # Now let's query Pinecone index using this vector to verify it matches!
        # Step 1: Get host
        api_key = "pcsk_4AqUSn_UgboMWWKC7DUa9wD3SfXEQrNMdCEvcYbQNCjMJUc3399JXfgJD8zgU8pBhwk8Zk"
        index_name = "politometro"
        
        desc_url = f"https://api.pinecone.io/indexes/{index_name}"
        desc_res = requests.get(desc_url, headers={"Api-Key": api_key})
        host = desc_res.json().get("host")
        print(f"Host: {host}")
        
        query_url = f"https://{host}/query"
        query_payload = {
            "vector": embedding,
            "topK": 5,
            "includeMetadata": True
        }
        query_res = requests.post(query_url, json=query_payload, headers={"Api-Key": api_key, "Content-Type": "application/json"})
        print(f"Pinecone Query Status: {query_res.status_code}")
        if query_res.status_code == 200:
            matches = query_res.json().get("matches", [])
            print(f"Found {len(matches)} matches:")
            for m in matches[:3]:
                print(f" - Score: {m.get('score'):.4f}, Metadata: {m.get('metadata', {}).get('party')} {m.get('metadata', {}).get('year')} (P.{m.get('metadata', {}).get('page')})")
else:
    print(f"Error from HF: {res.text}")
