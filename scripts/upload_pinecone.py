#!/usr/bin/env python3
"""Envio incremental de todos os corpora do Politómetro para o Pinecone.

O modo de embeddings predefinido é local, pelo que as execuções semanais não
consomem o quota de embeddings alojado do Pinecone. Guarda-se uma impressão
digital por namespace/id; os chunks inalterados são ignorados e os alterados
são reenviados com o mesmo ID de vetor.
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
import urllib.error
import urllib.request
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
DEFAULT_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_POLITICAL_NAMESPACE = "political-intelligence"
MAX_PINECONE_EMBEDDING_TOKENS = 4_500_000
LOGGER = logging.getLogger("upload_pinecone")


def load_env_file() -> None:
    """Lê as chaves do ficheiro .env do projeto (apenas para variáveis não definidas).

    Isto permite que execuções locais via CMD obtenham o PINECONE_API_KEY sem o
    operador definir uma variável de ambiente global. O GitHub Actions continua
    a sobrepor através do seu próprio ambiente, pelo que os valores de CI têm
    sempre precedência.
    """
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
    """Carrega uma lista normal ou o manifesto de shards produzido pelo pipeline."""
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


# --------------------------------------------------------------------------- #
# Arquivo de texto no Turso (libSQL)
#
# O escalão free do Pinecone limita o armazenamento a 2 GB; o texto integral dos
# chunks (~418 MB) ultrapassaria esse limite. Mantemos no Pinecone apenas os
# vetores + uma pequena flag `source_type` e guardamos o texto integral e os
# metadados estruturados no Turso (free: 5 GB, 500M leituras, 10M escritas/mês).
# A aplicação de chat junta os dados pelo `id` do chunk.
# --------------------------------------------------------------------------- #
def turso_enabled() -> bool:
    return bool(os.environ.get("TURSO_URL") and os.environ.get("TURSO_TOKEN"))


def _turso_request(statements: list[dict[str, Any]]) -> dict[str, Any]:
    url = os.environ["TURSO_URL"].rstrip("/") + "/v1/sql"
    token = os.environ["TURSO_TOKEN"]
    body = json.dumps({"statements": statements}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _turso_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _turso_upsert_chunks(rows: list[dict[str, Any]]) -> None:
    """Escreve as linhas de texto dos chunks de forma idempotente. Seguro repetir (ON CONFLICT)."""
    statements: list[dict[str, Any]] = []
    for r in rows:
        statements.append({
            "q": (
                "INSERT INTO chunks "
                "(id, namespace, text, page, party, year, category, filename, source_url, source_type, embedding_model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "namespace=excluded.namespace, text=excluded.text, page=excluded.page, "
                "party=excluded.party, year=excluded.year, category=excluded.category, "
                "filename=excluded.filename, source_url=excluded.source_url, "
                "source_type=excluded.source_type, embedding_model=excluded.embedding_model"
            ),
            "args": [
                {"type": "text", "value": str(r.get("id") or "")},
                {"type": "text", "value": str(r.get("namespace") or "")},
                {"type": "text", "value": str(r.get("text") or "")},
                {"type": "integer", "value": _turso_int(r.get("page"))},
                {"type": "text", "value": str(r.get("party") or "")},
                {"type": "text", "value": str(r.get("year") or "")},
                {"type": "text", "value": str(r.get("category") or "")},
                {"type": "text", "value": str(r.get("filename") or "")},
                {"type": "text", "value": str(r.get("source_url") or "")},
                {"type": "text", "value": str(r.get("source_type") or "")},
                {"type": "text", "value": str(r.get("embedding_model") or DEFAULT_MODEL)},
            ],
        })
    for i in range(0, len(statements), 200):
        _turso_request(statements[i:i + 200])


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.batch_size < 1:
        raise RuntimeError("--batch-size tem de ser positivo.")
    load_env_file()
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
    if not turso_enabled():
        raise RuntimeError(
            "Defina TURSO_URL e TURSO_TOKEN no .env (texto dos chunks vai para o Turso, "
            "para caber no limite de 2 GB do Pinecone free). Veja scripts/turso_schema.sql e scripts/init_turso.py."
        )
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
            turso_rows = []
            for offset, (item, item_namespace, _digest) in enumerate(batch):
                # Pinecone fica apenas com o vetor + flag mínima (source_type) para
                # caber no limite de 2 GB do plano free. O texto integral vai para o Turso.
                metadata = (
                    {"source_type": item["source_type"]}
                    if item.get("source_type") not in (None, "")
                    else {}
                )
                vectors.append({"id": str(item["id"]), "values": as_values(encoded[offset]), "metadata": metadata})
                turso_rows.append({
                    "id": item["id"],
                    "namespace": item_namespace,
                    "text": item.get("text", ""),
                    "page": item.get("page"),
                    "party": item.get("party"),
                    "year": item.get("year"),
                    "category": item.get("category"),
                    "filename": item.get("filename"),
                    "source_url": item.get("source_url"),
                    "source_type": item.get("source_type"),
                    "embedding_model": DEFAULT_MODEL,
                })
            # 1) Persiste o texto no Turso primeiro (chat resolve por id).
            _turso_upsert_chunks(turso_rows)
            # 2) Upsert dos vetores no Pinecone.
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
    load_env_file()
    try:
        result = run(parse_args(argv))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
