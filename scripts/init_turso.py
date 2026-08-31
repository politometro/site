#!/usr/bin/env python3
"""Cria a tabela `chunks` do Turso usada para guardar o texto dos chunks.

Requer TURSO_URL e TURSO_TOKEN (ambiente ou .env do projeto). Correr uma vez
após criar uma base de dados Turso vazia:

    python scripts/init_turso.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "scripts"


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


def main() -> int:
    load_env_file()
    url = os.environ.get("TURSO_URL")
    token = os.environ.get("TURSO_TOKEN")
    if not url or not token:
        print("Defina TURSO_URL e TURSO_TOKEN no .env antes de correr este script.", file=sys.stderr)
        return 2
    schema = (SCRIPT_DIR / "turso_schema.sql").read_text(encoding="utf-8")
    statements = [
        s.strip()
        for s in schema.split(";")
        if s.strip() and not s.strip().startswith("--")
    ]
    req_url = url.rstrip("/") + "/v1/sql"
    for stmt in statements:
        body = json.dumps({"statements": [{"q": stmt, "args": []}]}).encode("utf-8")
        req = urllib.request.Request(
            req_url,
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            resp.read()
        print(f"{'OK' if status == 200 else 'WARN ' + str(status)}: {stmt[:55]}")
    print("Tabela 'chunks' pronta no Turso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
