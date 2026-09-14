"""Re-empacota os shards públicos do quadro político do site.

Os shards originais são escritos com um limite de 45 MiB para respeitar o
limite de blobs do GitHub. Para o browser isso significa shards de ~47 MB —
 demasiado para carregar no cliente (o painel carregava ~660 MB só para
mostrar 25 notícias). Este script reescreve `website/public/
political-intelligence-shards/` com shards pequenos (6 MiB), deduplica
promessas repetidas, cria um ficheiro compacto com as promessas que têm
correspondências (Europa/Orçamentos) e um índice de programas eleitorais
para a aba de comparação. O manifesto `political-intelligence.json` é
reescrito em conformidade.

Idempotente: pode ser corrido de novo a partir de qualquer estado.
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
PUBLIC_DIR = os.path.join(ROOT_DIR, "website", "public")
SHARDS_DIR = os.path.join(PUBLIC_DIR, "political-intelligence-shards")
MANIFEST_PATH = os.path.join(PUBLIC_DIR, "political-intelligence.json")
PROGRAMAS_INDEX_PATH = os.path.join(PUBLIC_DIR, "programas-index.json")

# 6 MiB: compacto o suficiente para o browser carregar 1-2 shards por
# página, sem ficar abaixo da compressão eficiente nem criar centenas de
# ficheiros.
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
            parts = []
            for rel in paths:
                parts.extend(_read_json(os.path.join(PUBLIC_DIR, rel)))
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
        key=lambda row: (row["party"], row["year"], row["relPath"]),
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
    inline, arrays = _load_manifest_and_arrays()

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
    arrays["promises"] = deduped_promises

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

        # Remove ficheiros antigos de shards antes de publicar os novos.
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

    print(f"[OK] Promessas deduplicadas: {removed} removidas (restam {len(deduped_promises)})")
    print(f"[OK] promessas com correspondências: {len(matched)}")
    print(f"[OK] programas no índice: {len(programas['programmes'])}")
    for key in (*PUBLIC_ARRAY_KEYS, "promises-matched"):
        print(f"     {key}: {len(shards[key])} shard(s)")
    print(f"[OK] manifest {manifest_size} bytes; programas-index {programas_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
