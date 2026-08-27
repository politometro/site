#!/usr/bin/env python3
"""Upload every Politómetro corpus to Pinecone incrementally.

The default embedding mode is local, so weekly runs do not consume Pinecone's
hosted embedding quota. A fingerprint is stored per namespace/id; unchanged
chunks are skipped and changed chunks are upserted with the same vector ID.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "scripts"
DEFAULT_TRACKING = SCRIPT_DIR / "pinecone_upload_state.json"
DEFAULT_POLITICAL_CHUNKS = SCRIPT_DIR / "extracted_chunks_political_intelligence.json"
DEFAULT_BASE_SOURCES = (
    SCRIPT_DIR / "extracted_chunks.json",
    SCRIPT_DIR / "extracted_chunks_ocr.json",
    SCRIPT_DIR / "extracted_chunks_eu_budget.json",
)
DEFAULT_MODEL = "multilingual-e5-large"
DEFAULT_POLITICAL_NAMESPACE = "political-intelligence"
MAX_PINECONE_EMBEDDING_TOKENS = 4_500_000
LOGGER = logging.getLogger("upload_pinecone")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking", type=Path, default=DEFAULT_TRACKING)
    parser.add_argument("--namespace", default=os.environ.get("PINECONE_NAMESPACE", ""))
    parser.add_argument("--political-namespace", default=os.environ.get("PINECONE_POLITICAL_NAMESPACE", DEFAULT_POLITICAL_NAMESPACE))
    parser.add_argument("--embedding-mode", choices=("local", "pinecone"), default=None)
    parser.add_argument("--model", default=os.environ.get("LOCAL_EMBEDDING_MODEL", DEFAULT_MODEL))
    parser.add_argument("--batch-size", type=int, default=48)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-embedding-tokens", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Reenvia todos os chunks, ignorando fingerprints.")
    return parser.parse_args(argv)


def json_load(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def json_save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_list(path: Path) -> list[dict[str, Any]]:
    """Load a normal list or the sharded-list manifest produced by the pipeline."""
    if not path.exists():
        print(f"Aviso: corpus ausente, ignorado: {path}")
        return []
    payload = json_load(path, [])
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping) or payload.get("format") != "json-list-shards":
        raise RuntimeError(f"{path} não contém uma lista JSON nem um manifesto de shards.")
    result: list[dict[str, Any]] = []
    for relative in payload.get("shards", []):
        shard_path = path.parent / str(relative)
        shard = json_load(shard_path, [])
        if not isinstance(shard, list):
            raise RuntimeError(f"Shard inválido: {shard_path}")
        result.extend(item for item in shard if isinstance(item, Mapping))
    return result


def fingerprint(item: Mapping[str, Any]) -> str:
    relevant = {
        key: item.get(key)
        for key in ("id", "text", "page", "party", "year", "category", "filename", "rel_path", "source_type", "source_url")
    }
    raw = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def estimate_tokens(texts: Sequence[str]) -> int:
    # Estimativa conservadora para português; o contador da API pode contar subwords.
    return max(0, int(sum(len(text.split()) for text in texts) * 1.35))


def quota_error(error: Exception) -> bool:
    message = str(error).casefold()
    return any(token in message for token in ("resource_exhausted", "quota", "embedding token limit", "too_many_requests"))


def load_local_embedder(model_name: str) -> Any:
    venv_site_packages = ROOT / ".venv" / "Lib" / "site-packages"
    if venv_site_packages.is_dir() and str(venv_site_packages) not in sys.path:
        sys.path.insert(0, str(venv_site_packages))
    try:
        from sentence_transformers import SentenceTransformer  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise RuntimeError("Embeddings locais requerem sentence-transformers. Instale com pip install sentence-transformers.") from exc
    print(f"A usar embeddings locais: {model_name}")
    return SentenceTransformer(model_name)


def as_values(value: Any) -> list[float]:
    raw = value.values if hasattr(value, "values") else value
    tolist = getattr(raw, "tolist", None)
    values = tolist() if callable(tolist) else list(raw)
    return [float(item) for item in values]


def load_tracking(path: Path) -> dict[str, str]:
    payload = json_load(path, {})
    if not isinstance(payload, Mapping):
        return {}
    vectors = payload.get("vectors")
    if isinstance(vectors, Mapping):
        return {str(key): str(value) for key, value in vectors.items()}
    # O antigo uploaded_files.json só registava ficheiros, não conteúdo; tudo
    # é revalidado uma vez para criar fingerprints corretos.
    return {}


def build_sources(args: argparse.Namespace) -> list[tuple[Path, str]]:
    sources = [(path, args.namespace) for path in DEFAULT_BASE_SOURCES]
    sources.append((DEFAULT_POLITICAL_CHUNKS, args.political_namespace))
    return sources


def pinecone_upsert(index: Any, vectors: list[dict[str, Any]], namespace: str) -> None:
    kwargs: dict[str, Any] = {"vectors": vectors}
    if namespace:
        kwargs["namespace"] = namespace
    index.upsert(**kwargs)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.batch_size < 1:
        raise RuntimeError("--batch-size tem de ser positivo.")
    tracking = load_tracking(args.tracking)
    pending: list[tuple[dict[str, Any], str, str]] = []
    totals: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for path, namespace in build_sources(args):
        chunks = load_list(path)
        totals[str(path.name)] = len(chunks)
        for raw_item in chunks:
            item = dict(raw_item)
            identifier = str(item.get("id") or "").strip()
            text = str(item.get("text") or "").strip()
            if not identifier or not text:
                continue
            key = (namespace, identifier)
            if key in seen:
                continue
            seen.add(key)
            digest = fingerprint(item)
            if args.force or tracking.get(f"{namespace}\x00{identifier}") != digest:
                pending.append((item, namespace, digest))
    if args.limit is not None:
        pending = pending[: max(0, args.limit)]
    mode = args.embedding_mode or os.environ.get("PINECONE_EMBEDDINGS", "local")
    print(f"Corpora: {totals}; chunks pendentes: {len(pending)}; modo de embeddings: {mode}.")
    if args.dry_run or not pending:
        return {"pending": len(pending), "uploaded": 0, "embeddingMode": mode}

    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        raise RuntimeError("Defina PINECONE_API_KEY antes de enviar para Pinecone.")
    try:
        from pinecone import Pinecone
    except ImportError as exc:
        raise RuntimeError("Instale pinecone: pip install pinecone.") from exc

    mode = mode.strip().casefold()
    if mode not in {"local", "pinecone"}:
        raise RuntimeError("PINECONE_EMBEDDINGS deve ser 'local' ou 'pinecone'.")
    client = Pinecone(api_key=api_key)
    index_name = os.environ.get("PINECONE_INDEX_NAME", "politometro")
    index = client.Index(index_name)
    local_embedder: Any | None = None
    embedding_tokens = 0
    token_limit = args.max_embedding_tokens
    if token_limit is None:
        token_limit = int(os.environ.get("PINECONE_EMBEDDING_TOKEN_BUDGET", str(MAX_PINECONE_EMBEDDING_TOKENS)))

    def embed(texts: list[str]) -> Any:
        nonlocal local_embedder, embedding_tokens
        if mode == "local":
            if local_embedder is None:
                local_embedder = load_local_embedder(args.model)
            return local_embedder.encode(
                [f"passage: {text}" for text in texts],
                batch_size=min(32, len(texts)),
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        estimate = estimate_tokens(texts)
        if embedding_tokens + estimate > token_limit:
            raise RuntimeError(
                f"O lote excederia o orçamento configurado de embeddings Pinecone ({token_limit:,} tokens estimados). "
                "Use --embedding-mode local ou continue noutro mês."
            )
        try:
            response = client.inference.embed(
                model=DEFAULT_MODEL,
                inputs=texts,
                parameters={"input_type": "passage", "truncate": "END"},
            )
            embedding_tokens += estimate
            return response
        except Exception as exc:
            if quota_error(exc):
                raise RuntimeError("A quota mensal de embeddings Pinecone foi atingida; use embeddings locais para continuar.") from exc
            raise

    completed = dict(tracking)
    uploaded = 0
    batch_size = min(96, max(1, args.batch_size))
    # Agrupa por namespace para que um upsert nunca misture o namespace padrão
    # com o namespace de atualidade política.
    for namespace in dict.fromkeys(item[1] for item in pending):
        namespace_pending = [item for item in pending if item[1] == namespace]
        for start in range(0, len(namespace_pending), batch_size):
            batch = namespace_pending[start:start + batch_size]
            encoded = embed([str(item[0]["text"]) for item in batch])
            vectors = []
            for offset, (item, _item_namespace, _digest) in enumerate(batch):
                metadata = {
                    key: item.get(key)
                    for key in ("text", "page", "party", "year", "category", "filename", "rel_path", "source_type", "source_url")
                    if item.get(key) not in (None, "")
                }
                vectors.append({"id": str(item["id"]), "values": as_values(encoded[offset]), "metadata": metadata})
            for attempt in range(6):
                try:
                    pinecone_upsert(index, vectors, namespace)
                    break
                except Exception:
                    if attempt == 5:
                        raise
                    time.sleep(2 ** attempt)
            for item, item_namespace, digest in batch:
                completed[f"{item_namespace}\x00{item['id']}"] = digest
            uploaded += len(batch)
            json_save(args.tracking, {"schemaVersion": 2, "index": index_name, "vectors": completed})
            print(f"Enviados {uploaded}/{len(pending)} chunks.")

    return {
        "pending": len(pending),
        "uploaded": uploaded,
        "embeddingMode": mode,
        "estimatedPineconeEmbeddingTokens": embedding_tokens,
        "index": index_name,
    }


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = run(parse_args(argv))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
