#!/usr/bin/env python3
"""Upload the political-intelligence retrieval corpus to its own Pinecone namespace.

The ordinary electoral-programme index remains untouched.  Keeping fresh news
and parliamentary facts in ``political-intelligence`` lets the chat decide when
current evidence is relevant instead of mixing it into every programme answer.

Examples
--------
    python scripts/political_intelligence.py all --since-days 4
    python scripts/upload_political_intelligence.py
    python scripts/upload_political_intelligence.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHUNKS = ROOT / "scripts" / "extracted_chunks_political_intelligence.json"
DEFAULT_TRACKING = ROOT / "scripts" / "uploaded_political_intelligence.json"
DEFAULT_NAMESPACE = "political-intelligence"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Envia factos políticos atualizados para a memória de pesquisa do bot.")
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--tracking", type=Path, default=DEFAULT_TRACKING)
    parser.add_argument("--namespace", default=os.environ.get("PINECONE_POLITICAL_NAMESPACE", DEFAULT_NAMESPACE))
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def json_load(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def json_save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def fingerprint(item: Mapping[str, Any]) -> str:
    payload = {
        key: item.get(key)
        for key in ("text", "party", "year", "category", "filename", "source_type", "source_url")
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_embedder() -> Any:
    venv_site_packages = ROOT / ".venv" / "Lib" / "site-packages"
    if venv_site_packages.is_dir() and str(venv_site_packages) not in sys.path:
        sys.path.insert(0, str(venv_site_packages))
    try:
        from sentence_transformers import SentenceTransformer  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise RuntimeError(
            "Não foi possível gerar embeddings localmente. Instale sentence-transformers ou use créditos de inferência Pinecone."
        ) from exc
    model = os.environ.get("LOCAL_EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
    print(f"A usar embeddings locais: {model}")
    return SentenceTransformer(model)


def quota_error(error: Exception) -> bool:
    message = str(error).casefold()
    return "resource_exhausted" in message or "quota" in message or "embedding token limit" in message


def run(args: argparse.Namespace) -> dict[str, int | str]:
    raw_chunks = json_load(args.chunks, [])
    if not isinstance(raw_chunks, list):
        raise RuntimeError(f"{args.chunks} não contém uma lista JSON.")
    valid_chunks = [
        item for item in raw_chunks
        if isinstance(item, Mapping) and item.get("id") and item.get("text")
    ]
    if args.limit is not None:
        valid_chunks = valid_chunks[: max(0, args.limit)]

    tracking_payload = json_load(args.tracking, {"schemaVersion": 1, "chunks": {}})
    tracked = tracking_payload.get("chunks", {}) if isinstance(tracking_payload, Mapping) else {}
    tracked = tracked if isinstance(tracked, Mapping) else {}
    pending = [
        item for item in valid_chunks
        if args.force or tracked.get(str(item["id"])) != fingerprint(item)
    ]
    print(f"Corpus: {len(valid_chunks)} factos; a enviar: {len(pending)}; namespace: {args.namespace}.")
    if args.dry_run or not pending:
        return {"total": len(valid_chunks), "pending": len(pending), "uploaded": 0, "namespace": args.namespace}

    api_key = os.environ.get("PINECONE_API_KEY")
    index_name = os.environ.get("PINECONE_INDEX_NAME") or "politometro"
    if not api_key:
        raise RuntimeError("Defina PINECONE_API_KEY antes de enviar a memória do bot.")
    try:
        from pinecone import Pinecone
    except ImportError as exc:
        raise RuntimeError("Instale o pacote pinecone para enviar a memória do bot.") from exc

    client = Pinecone(api_key=api_key)
    index = client.Index(index_name)
    local_embedder: Any | None = None
    use_local = False

    def embeddings(texts: list[str]) -> Any:
        nonlocal local_embedder, use_local
        if not use_local:
            try:
                return client.inference.embed(
                    model="multilingual-e5-large",
                    inputs=texts,
                    parameters={"input_type": "passage", "truncate": "END"},
                )
            except Exception as exc:
                if not quota_error(exc):
                    raise
                print("A quota de embeddings Pinecone foi atingida; a mudar para modelo local.")
                use_local = True
        if local_embedder is None:
            local_embedder = load_embedder()
        assert local_embedder is not None
        return local_embedder.encode(
            [f"passage: {text}" for text in texts],
            batch_size=min(32, len(texts)),
            normalize_embeddings=True,
            show_progress_bar=False,
        )

    batch_size = max(1, min(100, args.batch_size))
    uploaded = 0
    completed = dict(tracked)
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset: offset + batch_size]
        encoded = embeddings([str(item["text"]) for item in batch])
        vectors = []
        for index_offset, item in enumerate(batch):
            raw_vector = encoded[index_offset].values if hasattr(encoded[index_offset], "values") else encoded[index_offset]
            values = raw_vector.tolist() if hasattr(raw_vector, "tolist") else list(raw_vector)
            metadata = {
                "text": str(item["text"]),
                "page": int(item.get("page") or 0),
                "party": str(item.get("party") or "ASSEMBLEIA"),
                "year": str(item.get("year") or ""),
                "category": str(item.get("category") or "Atualidade política"),
                "filename": str(item.get("filename") or ""),
                "source_type": str(item.get("source_type") or ""),
                "source_url": str(item.get("source_url") or ""),
            }
            vectors.append({"id": str(item["id"]), "values": values, "metadata": metadata})
        index.upsert(vectors=vectors, namespace=args.namespace)
        for item in batch:
            completed[str(item["id"])] = fingerprint(item)
        uploaded += len(batch)
        print(f"Enviados {uploaded}/{len(pending)} factos.")
        time.sleep(0.25)

    json_save(args.tracking, {"schemaVersion": 1, "namespace": args.namespace, "chunks": completed})
    return {"total": len(valid_chunks), "pending": len(pending), "uploaded": uploaded, "namespace": args.namespace}


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run(parse_args(argv))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
