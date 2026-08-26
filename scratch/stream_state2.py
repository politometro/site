"""Scan final dirigido ao estado: contagens de artigos, promessas e resultado da limpeza."""

from __future__ import annotations

import collections
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "political_intelligence_state.json")

PATTERNS = {
    "articles_open": re.compile(rb'"articles":\{'),
    "promises_open": re.compile(rb'"promises":\{'),
    "pruned": re.compile(rb'"articlesRemovedAsIrrelevant":(-?\d+)'),
    "excerpt": re.compile(rb'"excerpt":"'),
    "summary": re.compile(rb'"summary":"'),
    "viaFeed": re.compile(rb'"viaFeed"'),
    "review_v9": re.compile(rb'"reviewVersion":"rv-9"'),
    "review_old": re.compile(rb'"reviewVersion":"rv-(?:[0-8])"'),
    "promise_pv9": re.compile(rb'"promiseReviewVersion":"pv-9"'),
    "noticia_origin": re.compile(rb'"origin":"noticia"'),
    "collected_today": re.compile(rb'"decision":"collected","checkedAt":"[^"]*"'),
}
decisions_sampled = collections.Counter()

totals = collections.Counter()
first_article_snip = None
article_zone_started = False
seen_in_articles = collections.Counter()

with open(STATE, "rb") as handle:
    overlap_size = 262144
    tail = b""
    chunk_size = 64 * 1024 * 1024
    while True:
        chunk = handle.read(chunk_size)
        if not chunk:
            break
        window = tail + chunk
        base = len(tail)
        idx_articles = PATTERNS["articles_open"].search(window)
        if idx_articles and first_article_snip is None:
            start = idx_articles.end()
            first_article_snip = window[start : start + 1200]
            article_zone_started = True
        for name, pat in PATTERNS.items():
            n = len(pat.findall(window))
            totals[name] += n
        if first_article_snip is not None:
            # amostra de algumas chaves logo a seguir ao início do dicionário
            zone = first_article_snip.decode("latin1")
            seen_in_articles.update(re.findall(r'"([A-Za-z]+)":', zone))
        tail = window[-overlap_size:]

print("== contagens brutas ==")
for key, value in sorted(totals.items()):
    print(f"  {key}: {value}")
print("\n== artigo exemplo (primeiros 1200 bytes após '\"articles\":{') ==")
print((first_article_snip or b"(nao encontrado)").decode("latin1", "replace"))
