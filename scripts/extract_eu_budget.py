"""Extrai texto dos PDF de orçamentos europeus já em disco.

Gera ``scripts/extracted_chunks_eu_budget.json`` com o mesmo schema do
``extract_text.py`` (id, text, page, party, year, category, filename,
rel_path) para que o pipeline ``political_intelligence.py`` os consuma como
evidência da categoria "Orçamento UE (BCE)" / "Quadro Financeiro Plurianual
da UE" / "Regulamento Financeiro da UE" / "Recursos Próprios da UE".

Uso:  python scripts/extract_eu_budget.py [--force]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import warnings
from pathlib import Path

import pypdf

# Suprimir avisos "wrong pointing object" do pypdf
warnings.filterwarnings("ignore", message=".*wrong pointing object.*")
warnings.filterwarnings("ignore", message=".*Ignoring wrong pointing object.*")
import logging
logging.getLogger("pypdf").setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "Orçamentos de Estado Europeus"
OUTPUT = ROOT / "scripts" / "extracted_chunks_eu_budget.json"


def clean_vector_id(raw_id: str) -> str:
    normalized = unicodedata.normalize("NFKD", raw_id).encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", normalized)


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start += max(1, chunk_size - overlap)
    return [chunk for chunk in chunks if chunk]


def category_for(filename: str) -> str:
    lowered = filename.lower()
    if "bce" in lowered or "banco central" in lowered:
        return "Orçamento UE (BCE)"
    if "quadro financeiro plurianual" in lowered or "plurianual" in lowered:
        return "Quadro Financeiro Plurianual da UE"
    if "recursos próprios" in lowered or "recursos proprios" in lowered:
        return "Recursos Próprios da UE"
    if "nextgeneration" in lowered or "mecanismo de recuperação" in lowered:
        return "Quadro Financeiro Plurianual da UE"
    if "regulamento financeiro" in lowered:
        return "Regulamento Financeiro da UE"
    return "Orçamento UE (Metadados)"


def year_for(filepath: Path, filename: str) -> int | None:
    match = re.search(r"\b(19\d{2}|20\d{2})\b", filename)
    if not match:
        match = re.search(r"\b(19\d{2}|20\d{2})\b", str(filepath))
    return int(match.group(1)) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Reextrai mesmo com saída recente.")
    args = parser.parse_args()

    if not DATA_DIR.exists():
        print(f"Pasta ausente: {DATA_DIR}")
        return 1

    if OUTPUT.exists() and not args.force:
        existing_size = OUTPUT.stat().st_size
        newer = OUTPUT.stat().st_mtime > max(
            (pdf.stat().st_mtime for pdf in DATA_DIR.glob("*.pdf")), default=0.0
        )
        if existing_size > 1000 and newer:
            print(f"Saída em dia ({existing_size:,} bytes); use --force para reextrair.")
            return 0

    chunks_db: list[dict] = []
    processed: list[tuple[str, int, int]] = []
    errors: list[tuple[str, str]] = []

    pdf_files = sorted(DATA_DIR.glob("*.pdf"))
    print(f"A extrair {len(pdf_files)} PDF de {DATA_DIR} ...")
    for pdf_path in pdf_files:
        rel_path = f"Orçamentos de Estado Europeus/{pdf_path.name}"
        category = category_for(pdf_path.name)
        year_value = year_for(pdf_path, pdf_path.name)
        try:
            reader = pypdf.PdfReader(str(pdf_path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    pass
            page_chunks: list[dict] = []
            for page_num, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                text = re.sub(r"\s+", " ", text).strip()
                if not text:
                    continue
                for idx, piece in enumerate(chunk_text(text)):
                    page_chunks.append({"text": piece, "page": page_num, "chunk_index": idx})
            total_words = sum(len(piece["text"].split()) for piece in page_chunks)
            if total_words < 20 and len(reader.pages):
                errors.append((rel_path, f"PDF sem texto utilizável ({len(reader.pages)} páginas; requer OCR)"))
                continue
            for piece in page_chunks:
                raw_id = f"{category}_{year_value}_{pdf_path.stem}_p{piece['page']}_c{piece['chunk_index']}"
                chunks_db.append({
                    "id": clean_vector_id(raw_id),
                    "text": piece["text"],
                    "page": piece["page"],
                    "party": "UE",
                    "year": year_value,
                    "category": category,
                    "filename": pdf_path.name,
                    "rel_path": rel_path,
                })
            processed.append((rel_path, len(reader.pages), len(page_chunks)))
            print(f"  Processado: {rel_path} ({len(reader.pages)} páginas, {len(page_chunks)} excertos)")
        except Exception as exc:  # noqa: BLE001 — falhas individuais não param a extração
            errors.append((rel_path, str(exc)))
            print(f"  Erro {rel_path}: {exc}")

    OUTPUT.write_text(json.dumps(chunks_db, ensure_ascii=False), encoding="utf-8")
    from collections import Counter

    categories = Counter(item["category"] for item in chunks_db)
    print(f"\nGuardados {len(chunks_db):,} excertos em {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")
    for name, count in categories.most_common():
        print(f"  {count:>6}  {name}")
    if errors:
        print(f"Ficheiros com problema ({len(errors)}):")
        for name, error in errors:
            print(f"  - {name}: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
