"""Leitura apontada do estado do pipeline para auditoria pós-run."""

from __future__ import annotations

import collections
import json
import mmap
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import orjson

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "political_intelligence_state.json")

with open(STATE, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
    state = orjson.loads(memoryview(mapped))

print("== topo ==")
for key, value in state.items():
    size = len(value) if isinstance(value, (dict, list, str)) else "-"
    print(f"  {key}: {type(value).__name__} ({size})")
print("  updatedAt:", state.get("updatedAt"))

articles = state.get("articles", {})
promises = state.get("promises", {})
print(f"\n== totais ==\n  artigos guardados: {len(articles)}\n  promessas: {len(promises)}")

fields = collections.Counter()
sample = None
for record in articles.values():
    if sample is None:
        sample = {
            k: (str(v)[:90] if isinstance(v, str) else v) for k, v in list(record.items())[:18]
        }
    fields.update(record.keys())
    break
print("\n  campos do artigo exemplo:", sorted(fields))
print("  artigo exemplo:", json.dumps(sample, ensure_ascii=False, default=str))

source_ids = []
decision_by_source = {}
version_by_source = {}
progress_counts = {}
checked_spans = {}

for sid, data in state.get("sources", {}).items():
    source_ids.append(sid)
    seen = data.get("seen", {}) if isinstance(data, dict) else {}
    decisions = collections.Counter(str(entry.get("decision")) for entry in seen.values() if isinstance(entry, dict))
    versions = collections.Counter(str(entry.get("filterVersion")) for entry in seen.values() if isinstance(entry, dict))
    decision_by_source[sid] = decisions
    version_by_source[sid] = versions
    progress = data.get("sitemapProgress", {}).get("completed", {}) if isinstance(data, dict) else {}
    progress_counts[sid] = len(progress)
    stamps = [str(v.get("checkedAt")) for v in progress.values() if isinstance(v, dict)]
    checked_spans[sid] = (min(stamps) if stamps else "", max(stamps) if stamps else "")

today_prefix = max((state.get("updatedAt") or "T")[:10], "0000")
print(f"\ndia de referência (updatedAt): {today_prefix}")

def fmt_counter(counter: collections.Counter) -> str:
    return ", ".join(f"{k or '?'}={v}" for k, v in counter.most_common())

print("\n== por fonte ==")
for sid in source_ids:
    data = state["sources"][sid]
    seen_n = len(data.get("seen", {})) if isinstance(data, dict) else 0
    lo, hi = checked_spans[sid]
    print(
        f"  {sid}: seen={seen_n}, folhas_sitemap={progress_counts[sid]} "
        f"[{lo or '-'} … {hi or '-'}]"
    )
    print(f"      decisões: {fmt_counter(decision_by_source[sid])}")
    print(f"      versões:   {fmt_counter(version_by_source[sid])}")
    last_run = data.get("lastRun", {}) if isinstance(data, dict) else {}
    if last_run:
        interesting = {
            k: last_run[k]
            for k in ("collected", "candidates", "rejectedMetadata", "rejectedArticle", "indisponiveis", "status", "note", "updatedAt")
            if k in last_run
        }
        print(f"      última run: {interesting}")

tsf = state.get("sources", {}).get("tsf", {})
if tsf:
    print("\n== tsf: sitemapProgress ==")
    completed = tsf.get("sitemapProgress", {}).get("completed", {})
    items = sorted(completed.items(), key=lambda kv: str(kv[1].get("checkedAt")))
    for url, meta in items[-6:]:
        kids = meta.get("children") or []
        print(f"  {url} -> entradas={meta.get('entryCount')} folhas={len(kids)} checked={meta.get('checkedAt')}")
        for child in kids[:3]:
            print(f"      · {child}")
    refreshed_today = sum(1 for _, m in items if str(m.get("checkedAt", "")) >= today_prefix)

# artigos 2016 da TSF: quando foram tocados pela última vez?
year_counter = collections.Counter()
touched_recently = collections.Counter()
tsf_like_articles = 0
recent_window = ("20260825", "20260826")
recent_stamps = collections.Counter()
stamp_key_used = None
for aid, article in articles.items():
    published = str(article.get("publishedAt") or "")
    source_field = str(article.get("sourceId") or article.get("source") or "")
    year = published[:4]
    if "tsf" in (str(article.get("sourceName") or "") + source_field + aid).lower():
        tsf_like_articles += 1
        year_counter[year] += 1
        for stamp_key in ("collectedAt", "checkedAt", "updatedAt", "firstSeenAt", "lastCheckedAt"):
            stamp = str(article.get(stamp_key) or "")
            if stamp >= "2026-08":
                stamp_key_used = stamp_key or stamp_key_used
                recent_stamps[stamp[:10]] += 1
                break
        if year == "2016":
            touched_recently[any(str(article.get(k) or "") >= "2026-08-24" for k in ("collectedAt", "checkedAt", "updatedAt", "reviewedAt"))] += 1

print("\n== tsf nos artigos ==")
print("  artigos com marcação tsf:", tsf_like_articles)
print("  anos mais recentes (publicação):", dict(sorted(year_counter.items())[-12:]))
print("  2016 tocados após 2026-08-24:", dict(touched_recently), "| chave usada:", stamp_key_used)
print("  carimbos 2026-08 vistos:", dict(sorted(recent_stamps.items())[-6:]))

print("\n== tópicos dos artigos guardados ==")
topics = collections.Counter()
no_topics = 0
for article in articles.values():
    value = article.get("topics")
    if not value:
        no_topics += 1
        continue
    topics[tuple(sorted(value))] += 1
print("  sem tópicos:", no_topics)
for combo, n in topics.most_common(8):
    print(f"  {combo}: {n}")

print("\nfim.")
