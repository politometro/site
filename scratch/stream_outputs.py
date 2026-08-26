"""Auditoria em stream dos outputs finais (estado-tail, público, chunks)."""

from __future__ import annotations

import collections
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_head(path: str, n: int) -> str:
    with open(path, "rb") as handle:
        return handle.read(n).decode("utf-8", "replace")


def read_tail(path: str, n: int) -> str:
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        handle.seek(max(0, size - n))
        return handle.read(n).decode("utf-8", "replace")


def stream_count(path: str, pattern: re.Pattern[str], limit_hits: int = 60):
    hits = []
    overlap = len(pattern.pattern) * 8 + 4096
    chunk_size = 64 * 1024 * 1024
    tail = ""
    total = 0
    with open(path, "rb") as handle:
        while True:
            chunk_b = handle.read(chunk_size)
            if not chunk_b:
                break
            window = tail + chunk_b.decode("latin1")
            for match in pattern.finditer(window):
                total += 1
                if len(hits) < limit_hits:
                    hits.append(match.groups() if match.groups() else match.group(0))
            tail = window[-overlap:]
    return total, hits


STATE = os.path.join(ROOT, "data", "political_intelligence_state.json")
PUBLIC = os.path.join(ROOT, "website", "public", "political-intelligence.json")
CHUNKS = os.path.join(ROOT, "scripts", "extracted_chunks_political_intelligence.json")

print("== estado: cauda (últimos 6000 caracteres) ==")
print(read_tail(STATE, 6000))

print("\n== estado: resultados das últimas runs ==")
tail_text = read_tail(STATE, 400_000)
for key in ("articlesRemovedAsIrrelevant", "fromNews", "fromProgrammes",
            "matchesSuggested", "publicArticlesExported"):
    values = re.findall(rf'"{key}": *([-\w\.]+)', tail_text)
    if values:
        print(f"  {key}: {values[-12:]}")

print("\n== público: cabeçalho ==")
print(read_head(PUBLIC, 2400)[:2400])

pub_patterns = {
    "urls": re.compile(r'"url":"'),
    "topicos_suja": re.compile(r'"topics":\[("[a-z]+"(?:,"[a-z]+")*)\]'),
    "titulos_desporto": re.compile(r'"title":"([^"]{0,150}(?:futebol|Futebol| Sporting| Benfica| FC Porto| golo| Liga dos| transfer[êe]ncia| ciclismo|MotoGP|Formula 1|F[óo]rmula 1)[^"]{0,80})"'),
    "titulos_famosos": re.compile(r'"title":"([^"]{0,150}(?:Cristiano Ronaldo|novela|Festival da Can[çc]ao|Big Brother|[Cc]elebridade)[^"]{0,80})"'),
    "titulos_crime": re.compile(r'"title":"([^"]{0,150}(?:homic[íi]dio|tr[áa]fico de droga|assaltante|estupro|estupro de vulner)[^"]{0,80})"'),
}
for name, pat in pub_patterns.items():
    total, hits = stream_count(PUBLIC, pat)
    print(f"\n-- {name}: {total}")
    for hit in hits[:18]:
        text = hit[0] if isinstance(hit, tuple) else hit
        print(f"   · {text[:170]}")

print("\n== chunks: cabeçalho ==")
print(read_head(CHUNKS, 1800))
count_pat = re.compile(r'"(?:chunkId|id)":"')
total_ids, _ = stream_count(CHUNKS, count_pat)
print(f"\nchunks: entradas ~{total_ids}")
