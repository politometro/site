import requests

url = "https://router.huggingface.co/v1/embeddings"
payload = {
    "model": "intfloat/multilingual-e5-large",
    "input": "query: Qual é a posição do PSD sobre a educação?"
}

# Try without a token first, then we can see if it requires one
res = requests.post(url, json=payload)
print(f"Status: {res.status_code}")
try:
    print(res.json())
except:
    print(res.text)
