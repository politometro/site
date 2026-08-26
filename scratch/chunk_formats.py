import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
seen = {}
for rel in ("scripts/extracted_chunks.json", "scripts/extracted_chunks_ocr.json"):
    for chunk in json.load(open(rel, encoding="utf-8")):
        rel_path = str(chunk.get("rel_path") or chunk.get("relPath") or "")
        filename = str(chunk.get("filename") or "")
        category = str(chunk.get("category") or "")
        parts = rel_path.replace(chr(92), "/").split("/")
        key = parts[0] if len(parts) > 1 else "(raiz)"
        if key not in seen:
            seen[key] = (category, rel_path, filename, chunk.get("party"), chunk.get("year"))
for key, value in sorted(seen.items()):
    cat, rp, fn, party, year = value
    print(f"{key!r}: cat={cat!r} rel_path={rp!r} file={fn!r} party={party!r} year={year!r}")
