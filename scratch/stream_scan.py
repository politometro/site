"""Scan em stream do estado (JSON compacto numa linha) sem carregar tudo em RAM."""

from __future__ import annotations

import collections
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "political_intelligence_state.json")

CHECKED = re.compile(rb'"checkedAt":"(\d{4}-\d{2}-\d{2})')
VERSION = re.compile(rb'"filterVersion":"([^"]*)"')
DECISION = re.compile(rb'"decision":"([a-z_]+)"')
LASTRUN = re.compile(rb'"lastRun":\{(.*?)\}')
PUBYEAR = re.compile(rb'"publishedAt":"(\d{4})')
TSF_LEAF = re.compile(
    rb'(https://www\.tsf\.pt/feed/[^"\\\s]+)":\{"checkedAt":"([^"]+)"'
    rb',"children":\[([^\]]*)\],"entryCount":(-?\d+)\}'
)
ART_HEAD = re.compile(rb'"articles":\{')

def run_stream(patterns, path, stop_after_art=None):
    overlap = 65536
    chunk_size = 48 * 1024 * 1024
    tail = b""
    art_head = None
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            window = tail + chunk
            base = len(tail)
            for pat in patterns:
                for match in pat.finditer(window):
                    yield pat, match, base
            if art_head is None:
                m = ART_HEAD.search(window)
                if m:
                    art_head = window[m.start() : m.start() + 1400]
            tail = window[-overlap:]
    yield None, art_head, 0

checked_days = collections.Counter()
versions = collections.Counter()
decisions = collections.Counter()
pubyears = collections.Counter()
tsf_leaves = []
lastruns = []
for pat, match, _base in run_stream(
    [CHECKED, VERSION, DECISION, LASTRUN, PUBYEAR, TSF_LEAF], STATE
):
    if pat is CHECKED:
        checked_days[match.group(1).decode()] += 1
    elif pat is VERSION:
        versions[match.group(1).decode("latin1")] += 1
    elif pat is DECISION:
        decisions[match.group(1).decode()] += 1
    elif pat is PUBYEAR:
        pubyears[match.group(1).decode()] += 1
    elif pat is LASTRUN:
        if len(lastruns) < 60:
            lastruns.append(match.group(1).decode("latin1", "replace"))
    elif pat is TSF_LEAF:
        if len(tsf_leaves) < 500000:
            kids = match.group(3)
            tsf_leaves.append((match.group(1).decode(), match.group(2).decode(),
                               kids.count(b"https://"), match.group(4).decode()))

print("== checkedAt por dia ==")
for day, n in sorted(checked_days.items())[-10:]:
    print(f"  {day}: {n}")
print("\n== filterVersion ==")
print("  " + ", ".join(f"{k or 'null'}={v}" for k, v in versions.most_common()))
print("\n== decisões ==")
print("  " + ", ".join(f"{k or '?'}={v}" for k, v in decisions.most_common()))
print("\n== anos publicados ==")
total_recent = sum(n for y, n in pubyears.items() if y >= "2025")
print("  últimos anos:", {y: pubyears[y] for y in sorted(pubyears)[-14:]})
print(f"  >=2025: {total_recent} | total publicado datado: {sum(pubyears.values())}")

print(f"\n== folhas sitemap tsf ({len(tsf_leaves)} registos capturados) ==")
byday = collections.Counter(day[:10] for _u, day, _k, _e in tsf_leaves)
print("  verificadas por dia:", dict(sorted(byday.items())))
with_children = [(u, d, k, e) for u, d, k, e in tsf_leaves if k]
for url, day, kids, entries in sorted(with_children, key=lambda x: x[1])[:8]:
    print(f"  {url}\n      dia={day} filhos={kids} entradas={entries}")

print("\n== lastRun por fonte ==")
for item in lastruns:
    print("  {" + item[:340] + "}")
