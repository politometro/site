import json
import os

import requests


WEBSITE_URL = os.environ.get("WEBSITE_URL", "http://localhost:3000")


def query_politometro_chat(query, source="bot", user_id="unknown", timeout=35):
    """Query the website chat API and return the streamed assistant answer."""
    base_url = WEBSITE_URL.rstrip("/")
    url = f"{base_url}/api/chat"
    payload = {"messages": [{"role": "user", "content": str(query or "").strip()}]}
    headers = {
        "Content-Type": "application/json",
        "x-client-id": f"{source}:{user_id}",
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=timeout,
        )

        if response.status_code != 200:
            try:
                err_json = response.json()
                detail = err_json.get("error", "Nao foi possivel obter resposta.")
            except (ValueError, TypeError):
                detail = "Nao foi possivel obter resposta."
            return f"O Politometro nao conseguiu responder agora. {detail}"

        full_text = ""
        for line in response.iter_lines():
            if not line:
                continue
            decoded_line = line.decode("utf-8", errors="replace")
            if not decoded_line.startswith("data: "):
                continue
            data_str = decoded_line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                data_json = json.loads(data_str)
                full_text += data_json["choices"][0]["delta"].get("content", "")
            except (KeyError, TypeError, ValueError):
                continue

        return full_text.strip() or "Nao foi possivel obter uma resposta para a tua pergunta."
    except requests.RequestException:
        return "O Politometro nao conseguiu ligar-se ao chat neste momento. Tenta novamente daqui a pouco."


def split_text_chunks(value, limit):
    """Split text by paragraphs while respecting a platform message limit."""
    text = str(value or "").strip()
    if not text:
        return ["Nao foi possivel obter uma resposta."]
    chunks = []
    current = ""
    for paragraph in text.splitlines() or [text]:
        candidate = f"{current}\n{paragraph}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks
