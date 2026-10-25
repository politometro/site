"""Re-empacota os shards públicos do quadro político do site.

Os shards originais são escritos com um limite de 45 MiB para respeitar o
limite de blobs do GitHub. Para o browser isso significa shards de ~47 MB —
demasiado para carregar no cliente. Este script reescreve
`website/public/political-intelligence-shards/` com shards pequenos (6 MiB),
deduplica promessas repetidas, assegura as ligações enriquecidas (votos,
Europa e Orçamentos com excertos), cataloga os documentos orçamentais e
procedimentos europeus, e gera o manifesto `political-intelligence.json`
e o índice de programas eleitorais `programas-index.json`.

Idempotente: pode ser corrido de novo a partir de qualquer estado.
"""

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
import political_intelligence as pi

PUBLIC_DIR = os.path.join(ROOT_DIR, "website", "public")
SHARDS_DIR = os.path.join(PUBLIC_DIR, "political-intelligence-shards")
MANIFEST_PATH = os.path.join(PUBLIC_DIR, "political-intelligence.json")
PROGRAMAS_INDEX_PATH = os.path.join(PUBLIC_DIR, "programas-index.json")

# 6 MiB: compacto o suficiente para o browser carregar 1-2 shards por
# página, sem ficar abaixo da compressão eficiente nem criar centenas de ficheiros.
SHARD_MAX_BYTES = 6 * 1024 * 1024
PUBLIC_ARRAY_KEYS = ("articles", "promises", "initiatives", "votes")
STAGING_PREFIX = ".staging-reshard-"


def manifest_path():
    return MANIFEST_PATH


def _read_json(path):
    with open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def _write_json_file(path, payload):
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(encoded)
    os.replace(tmp, path)
    return len(encoded)


def _load_manifest_and_arrays():
    manifest = _read_json(MANIFEST_PATH)
    arrays = {}
    if manifest.get("format") == "political-intelligence-shards":
        inline = manifest.get("inline") or {}
        for key, paths in (manifest.get("shards") or {}).items():
            if key not in PUBLIC_ARRAY_KEYS and key != "promises-matched":
                continue
            parts = []
            for rel in paths:
                shard_file = os.path.join(PUBLIC_DIR, rel)
                if os.path.exists(shard_file):
                    parts.extend(_read_json(shard_file))
            arrays[key] = parts
    else:
        inline = manifest
        for key in PUBLIC_ARRAY_KEYS:
            arrays[key] = inline.pop(key, [])
    return inline, arrays


def _normalise_statement_key(statement):
    import unicodedata

    text = unicodedata.normalize("NFKD", str(statement or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def _promise_dedup_key(promise):
    source = promise.get("source") or {}
    return (
        str(promise.get("party") or ""),
        _normalise_statement_key(promise.get("statement")),
        str(source.get("filename") or ""),
        str(source.get("page") or ""),
    )


def _promise_has_matches(promise):
    return bool(
        promise.get("proposalMatches")
        or promise.get("voteOutcomes")
        or promise.get("europeanMatches")
        or promise.get("budgetMatches")
    )


def _programas_index(promises):
    """Índice leve dos programas eleitorais publicados (por partido/ano)."""
    index = {}
    for promise in promises:
        if promise.get("origin") != "programa_eleitoral":
            continue
        source = promise.get("source") or {}
        key = (
            str(promise.get("party") or ""),
            str(source.get("year") or ""),
            str(source.get("relPath") or source.get("filename") or ""),
        )
        entry = index.setdefault(
            key,
            {
                "party": key[0],
                "year": key[1],
                "filename": str(source.get("filename") or ""),
                "relPath": key[2],
                "contest": str(source.get("contest") or ""),
                "promiseCount": 0,
            },
        )
        entry["promiseCount"] += 1
    programmes = sorted(
        index.values(),
        key=lambda row: (row["party"], str(row["year"]), row["relPath"]),
    )
    return {"schemaVersion": 1, "programmes": programmes}


def _write_shards(staging_dir, key, items):
    """Write items as staged shards under the byte cap; return relative paths."""
    paths = []
    batch = []
    batch_bytes = 2
    for item in items:
        encoded = json.dumps(
            item, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if batch and batch_bytes + len(encoded) + 1 > SHARD_MAX_BYTES:
            paths.append(_write_staged_shard(staging_dir, key, len(paths), batch))
            batch = []
            batch_bytes = 2
        batch.append(item)
        batch_bytes += len(encoded) + 1
    if batch or not paths:
        paths.append(_write_staged_shard(staging_dir, key, len(paths), batch))
    return paths


def _write_staged_shard(staging_dir, key, index, batch):
    name = f"{key}-{index:04d}.json"
    path = os.path.join(staging_dir, name)
    _write_json_file(path, batch)
    return f"political-intelligence-shards/{name}"


def main():
    print("[1/6] A carregar manifesto e shards existentes...", flush=True)
    inline, arrays = _load_manifest_and_arrays()
    config = pi.json_load(Path(ROOT_DIR) / "data" / "political_intelligence_config.json", {})

    # Deduplica promessas repetidas (mesmo partido + enunciado + fonte).
    promises = arrays.get("promises", [])
    seen = set()
    deduped_promises = []
    for promise in promises:
        key = _promise_dedup_key(promise)
        if key in seen:
            continue
        seen.add(key)
        deduped_promises.append(promise)
    removed = len(promises) - len(deduped_promises)
    print(f"      Promessas deduplicadas: {removed} duplicadas removidas ({len(deduped_promises)} únicas).", flush=True)

    # [2/6] Associar resultados de votos (voteOutcomes) às promessas
    print("[2/6] A gerar resultados de votações (voteOutcomes)...", flush=True)
    initiatives = arrays.get("initiatives", [])
    votes = arrays.get("votes", [])
    inis_by_id = {str(i.get("id") or ""): i for i in initiatives}
    votes_by_id = {str(v.get("id") or ""): v for v in votes}

    outcomes_count = 0
    for promise in deduped_promises:
        if promise.get("proposalMatches"):
            outcomes = pi.promise_vote_outcomes(promise, inis_by_id, votes_by_id)
            if outcomes:
                promise["voteOutcomes"] = outcomes
                outcomes_count += 1
    print(f"      {outcomes_count} promessas enriquecidas com voteOutcomes oficiais.", flush=True)

    # [3/6] Associar rubricas orçamentais (budgetMatches) e catalogar documentos
    print("[3/6] A processar rubricas orçamentais (OE e UE)...", flush=True)
    matched_candidates = {
        p["id"]: p for p in deduped_promises
        if p.get("proposalMatches") or _promise_has_matches(p)
    }
    state_mock = {
        "promises": matched_candidates,
        "budgetDocuments": {},
    }

    budget_matched = pi.rebuild_budget_matches(state_mock, config)
    print(f"      {budget_matched} ligações orçamentais geradas.", flush=True)

    # Documentos orçamentais catalogados para a interface
    budget_docs_public = sorted(
        (
            {
                "id": str(item.get("id")),
                "category": item.get("category"),
                "year": item.get("year"),
                "filename": item.get("filename"),
                "relPath": item.get("relPath"),
                "chunkCount": item.get("chunkCount"),
                "preview": item.get("preview"),
                "governmentLabel": item.get("governmentLabel"),
                "matchCount": int(item.get("matchCount") or 0),
            }
            for item in state_mock.get("budgetDocuments", {}).values()
            if isinstance(item, dict)
        ),
        key=lambda d: (-(d.get("year") or 0), str(d.get("category") or ""), str(d.get("filename") or "")),
    )
    inline["budgets"] = {
        "documents": budget_docs_public,
        "documentsKnown": len(budget_docs_public),
    }

    # [4/6] Garantir secção European Union no inline
    if "europeanUnion" not in inline or not inline["europeanUnion"]:
        inline["europeanUnion"] = {
            "initiativesMatched": [],
            "proceduresKnown": 0,
            "votesKnown": 0,
        }

    arrays["promises"] = deduped_promises

    # [5/6] Escrever shards particionados
    print("[5/6] A escrever novos shards (limite 6 MiB)...")
    staging_dir = os.path.join(PUBLIC_DIR, f"{STAGING_PREFIX}{os.getpid()}")
    os.makedirs(staging_dir, exist_ok=True)
    try:
        shards = {}
        for key in PUBLIC_ARRAY_KEYS:
            shards[key] = _write_shards(staging_dir, key, arrays.get(key, []))

        matched = [
            promise
            for promise in arrays.get("promises", [])
            if _promise_has_matches(promise)
        ]
        shards["promises-matched"] = _write_shards(
            staging_dir, "promises-matched", matched
        )

        old_files = set()
        for name in os.listdir(SHARDS_DIR):
            old_files.add(name)
        new_names = {os.path.basename(path) for path in sum(shards.values(), [])}

        for name in new_names:
            os.replace(
                os.path.join(staging_dir, name),
                os.path.join(SHARDS_DIR, name),
            )
        for name in sorted(old_files - new_names):
            os.remove(os.path.join(SHARDS_DIR, name))

        manifest = {
            "schemaVersion": 2,
            "format": "political-intelligence-shards",
            "inline": inline,
            "shards": shards,
        }
        manifest_size = _write_json_file(MANIFEST_PATH, manifest)
        programas = _programas_index(arrays.get("promises", []))
        programas_size = _write_json_file(PROGRAMAS_INDEX_PATH, programas)
    finally:
        for name in os.listdir(staging_dir):
            os.remove(os.path.join(staging_dir, name))
        os.rmdir(staging_dir)

    print("[6/6] Concluído com sucesso!")
    print(f"      Promessas com correspondências (promises-matched): {len(matched)}")
    print(f"      Documentos orçamentais catalogados: {len(budget_docs_public)}")
    print(f"      Programas no índice: {len(programas['programmes'])}")
    for key in (*PUBLIC_ARRAY_KEYS, "promises-matched"):
        print(f"      {key}: {len(shards[key])} shard(s)")
    print(f"      Manifest: {manifest_size} bytes; programas-index: {programas_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
