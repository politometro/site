#!/usr/bin/env python3
"""Ativa a Quantização de Vetores Integrada (int8) no índice Pinecone do Politómetro.

A quantização é uma definição AO NÍVEL DO ÍNDICE. Uma vez ativada, o Pinecone
re-quantiza todos os vetores já armazenados (os ~0,5 GB de chunks de PDF
existentes) e guarda quantizado cada upsert futuro. Nada é eliminado e não é
preciso reenviar nada.

Correr uma vez com um PINECONE_API_KEY válido (ambiente ou .env):
    python scripts/ensure_pinecone_quantization.py

Se a chamada à API for rejeitada, ative manualmente na consola do Pinecone:
índice "politometro" -> Settings -> Integrated Vector Quantization -> int8.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = "https://api.pinecone.io"


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def _request(method: str, path: str, api_key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "api-key": api_key,
        "X-Pinecone-Api-Version": "2025-10",
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{HOST}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else {}


def main() -> int:
    load_env_file()
    api_key = os.environ.get("PINECONE_API_KEY")
    index_name = os.environ.get("PINECONE_INDEX_NAME", "politometro")
    if not api_key:
        print("Defina PINECONE_API_KEY (env ou .env).", file=sys.stderr)
        return 2

    try:
        info = _request("GET", f"/indexes/{index_name}", api_key)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        print(f"Nao foi possivel descrever o indice ({exc.code}): {body}", file=sys.stderr)
        return 1

    integrated = info.get("integrated") or {}
    current = integrated.get("quantization")
    print(f"Indice '{index_name}': quantization atual = {current!r}")
    if current == "int8":
        print("Ja esta ativo (int8). Nada a fazer.")
        return 0

    print("A ativar quantization int8 (re-quantiza os vetores existentes in-place)...")
    try:
        _request("PATCH", f"/indexes/{index_name}", api_key, {"integrated": {"quantization": "int8"}})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        print(f"Falhou ao ativar via API ({exc.code}): {body}", file=sys.stderr)
        print(
            "Alternativa: na consola do Pinecone, abra o indice "
            f"'{index_name}' > Settings e ative 'Integrated Vector Quantization (int8)'.",
            file=sys.stderr,
        )
        return 1

    print("Quantization int8 ativado. Todos os vetores (existentes + futuros) ficam quantizados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
