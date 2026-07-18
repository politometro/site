"""
Politometro - Instagram Post Generator (Production Version)
Generates the Instagram post image and caption using the template and auto-fetched cover art.
Supports completely dynamic recommendation selection, ensuring NO repeating types among
the 3 general slots, and no duplication with the weekly highlight.
Features:
- Cover dimensions are tied to the item TYPE, not the quadrant:
  * Podcasts are always rendered at 192x192 (square)
  * Books, Movies, and Highlights are always rendered at 160x220 (vertical)
- Top row covers align perfectly by the bottom (using dynamic heights based on item types).
- Spacing checks for 2-line title descenders to prevent overlaps.
- Descriptions vertically centered next to the covers.
- Elegant rounded corners on all covers (radius 18px)
"""
import os
import sys
import json
import datetime
import re
import copy
import hashlib
import unicodedata
from PIL import Image, ImageDraw, ImageFont, ImageOps
import requests

# Import the single, source-grounded resolver.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cover_fetcher import load_cover_for_item
from recommendation_resolver import (
    ResolutionError,
    probe_verified_source,
    resolve_recommendation,
)

# --- PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "post_template.jpg")
REC_FILE = os.path.join(ROOT_DIR, "website", "public", "recommendations.json")
OUTPUT_PATH = os.path.join(ROOT_DIR, "website", "public", "current_post.jpg")
OUTPUT_CAPTION_PATH = os.path.join(ROOT_DIR, "website", "public", "current_caption.txt")
PUBLICATION_RECEIPT_PATH = os.path.join(
    SCRIPT_DIR, "instagram_publication.json"
)
MAX_DRAFT_AGE_HOURS = 72
MIN_REVIEW_VALIDITY_HOURS = 24
MIN_PUBLICATION_VALIDITY_HOURS = 6

FONT_DIR = os.path.join(SCRIPT_DIR, "fonts")
FONT_BOLD = os.path.join(FONT_DIR, "Oswald-Bold.ttf")
FONT_REG = os.path.join(FONT_DIR, "Oswald-Regular.ttf")
FONT_DESC_BOLD = os.path.join(FONT_DIR, "Montserrat-SemiBold.ttf")

TEXT_COLOR = (10, 49, 74)

# --- FONT DOWNLOAD ---
FONT_URLS = {
    FONT_BOLD: "https://github.com/bradfrost/atomic-design/raw/main/fonts/Oswald-Bold.ttf",
    FONT_REG: "https://github.com/bradfrost/atomic-design/raw/main/fonts/Oswald-Regular.ttf",
    FONT_DESC_BOLD: "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-SemiBold.ttf",
}

def ensure_fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for path, url in FONT_URLS.items():
        if not os.path.exists(path):
            print(f"Downloading font: {os.path.basename(path)}...")
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)

# --- DYNAMIC QUADRANT BASE X AND DESC CONFIGURATION ---
QUADRANTS_CONFIG = {
    "q1": {
        "label_pos": (50, 150),
        "title_pos": (50, 172),
        "cover_x": 50,
    },
    "q2": {
        "label_pos": (435, 150),
        "title_pos": (435, 172),
        "cover_x": 435,
    },
    "q3": {
        "label_pos": (50, 525),
        "title_pos": (50, 547),
        "cover_x": 50,
    },
    "q4": {
        "label_pos": (435, 525),
        "title_pos": (435, 547),
        "cover_x": 435,
    }
}

# --- ROUNDED CORNERS HELPER ---
def apply_rounded_corners(img, radius):
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, img.width, img.height], radius=radius, fill=255)
    output = img.copy()
    output.putalpha(mask)
    return output

# --- LEGACY SELECTION (kept temporarily for backwards-readable history) ---
def _legacy_get_recommendations_with_valid_covers(queue):
    now = datetime.datetime.now(datetime.timezone.utc)
    
    def score(item):
        s = item.get("priority", 3)
        expiry = item.get("expiryDate")
        if expiry:
            try:
                exp = datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                delta = (exp - now).days
                if delta < 0:
                    return -1
                elif delta < 14:
                    s += 10
            except Exception:
                pass
        return s
    
    active_items = [i for i in queue if i.get("status") not in ["published", "skip", "pending_approval"] and score(i) >= 0]
    active_items.sort(key=lambda x: score(x), reverse=True)
    
    selected = {}
    covers = {}
    
    # 1. First, select the "highlight" (Recomendacao da semana)
    highlight_candidates = [i for i in active_items if i["type"] == "highlight"]
    selected_highlight = None
    if highlight_candidates:
        selected_highlight = highlight_candidates[0]
        print(f"  -> Selected highlight '{selected_highlight['title']}'")
    
    selected["q4"] = selected_highlight

    # 2. Select the other 3 positions dynamically from other types (no two books, no two podcasts, etc.)
    other_candidates = [i for i in active_items if i["type"] != "highlight"]
    selected_others = []
    seen_types = set()
    
    for item in other_candidates:
        if item["type"] in seen_types:
            continue
        selected_others.append(item)
        seen_types.add(item["type"])
        print(f"  -> Selected '{item['title']}' (type: {item['type']})")
        if len(selected_others) == 3:
            break
            
    # Fallback if less than 3 distinct types found
    if len(selected_others) < 3:
        all_types = list(set(i["type"] for i in other_candidates))
        for t in all_types:
            if t not in seen_types and len(selected_others) < 3:
                type_items = [i for i in other_candidates if i["type"] == t]
                if type_items:
                    selected_others.append(type_items[0])
                    seen_types.add(t)
                    print(f"  -> Fallback Selected '{type_items[0]['title']}' (type: {t})")

    # Assign to positions q1, q2, q3
    for idx, item in enumerate(selected_others):
        selected[f"q{idx+1}"] = item

    # Initialize covers dictionary (using cache or generating placeholders initially)
    for qkey in ["q1", "q2", "q3", "q4"]:
        item = selected.get(qkey)
        if item:
            covers[qkey] = fetch_cover_for_item(item, allow_placeholder=True)

    # Auto-resolve correct links and covers using Playwright browser automation
    try:
        from browser_resolver import resolve_all as browser_resolve_all
        browser_results = browser_resolve_all(selected)
        
        for qkey in ["q1", "q2", "q3", "q4"]:
            bres = browser_results.get(qkey)
            if not bres:
                continue
            item = selected.get(qkey)
            if not item:
                continue
            
            # Update link if browser found a real one
            if bres.get("link"):
                old_link = item.get("link", "")
                item["link"] = bres["link"]
                if old_link != bres["link"]:
                    print(f"  [{qkey.upper()}] Link updated: {old_link} -> {bres['link']}")
            
            # Re-fetch cover from cache (which now has the resolved cover image)
            new_cover = fetch_cover_for_item(item, allow_placeholder=False)
            if new_cover:
                covers[qkey] = new_cover
                print(f"  [{qkey.upper()}] Cover updated from browser resolver cache")
    except ImportError:
        print("[Link Resolver] browser_resolver not available, falling back to DuckDuckGo...")
        # Fallback to simple DuckDuckGo search
        for qkey in ["q1", "q2", "q3", "q4"]:
            item = selected.get(qkey)
            if not item:
                continue
            title = item["title"]
            itype = item["type"]
            author = item.get("authorOrMeta", "")
            clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
            
            resolved = False
            if itype == "podcast":
                query = f"episodio {title} {clean_author}"
                try:
                    itunes_url = f"https://itunes.apple.com/search?term={urllib.parse.quote(query)}&media=podcast&entity=podcastEpisode&limit=1"
                    r = requests.get(itunes_url, timeout=5)
                    if r.ok:
                        results = r.json().get("results", [])
                        if results:
                            ep_link = results[0].get("trackViewUrl")
                            if ep_link:
                                item["link"] = ep_link
                                resolved = True
                except Exception:
                    pass
            else:
                if itype == "book":
                    query = f"site:wook.pt livro {title} {clean_author}"
                elif itype in ["movie", "documentary", "series"]:
                    query = f"site:imdb.com {title}"
                else:
                    query = f"{title} {clean_author}"
            
            if not resolved:
                try:
                    found_url = search_duckduckgo_link(query)
                    if found_url:
                        item["link"] = found_url
                except Exception:
                    pass
    except Exception as e:
        print(f"[Link Resolver] Browser resolution failed: {e}. Continuing with original links.")
        
    return selected, covers


# --- SOURCE-GROUNDED SELECTION & QUALITY GATE ---
REQUIRED_TYPES = {
    "q1": "book",
    "q2": "podcast",
    "q3": "movie",
    "q4": "highlight",
}

TYPE_EMOJIS = {
    "book": "📖",
    "podcast": "🎙️",
    "movie": "🎞️",
    "documentary": "🎥",
    "series": "📺",
    "highlight": "📰",
}

TOPIC_HASHTAGS = (
    (("portugal", "portugues", "portuguesa"), "Portugal"),
    (("25 de abril", "revolucao dos cravos"), "25deAbril"),
    (("democracia", "eleicoes", "eleicao", "voto"), "Democracia"),
    (("economia", "economica", "financas", "inflacao"), "Economia"),
    (("historia", "historico", "historica"), "Historia"),
    (("justica", "tribunal", "constitucional"), "Justica"),
    (("investigacao", "jornalismo de investigacao"), "Jornalismo"),
    (("europa", "europeia", "uniao europeia"), "Europa"),
    (("ambiente", "clima", "sustentabilidade"), "Ambiente"),
    (("educacao", "escola", "universidade"), "Educacao"),
    (("saude", "sns", "hospital"), "Saude"),
)

HASHTAG_STOPWORDS = {
    "a", "ao", "aos", "as", "com", "da", "das", "de", "do", "dos", "e",
    "em", "na", "nas", "no", "nos", "o", "os", "para", "por", "sobre",
    "um", "uma",
}


def _plain_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    ).lower()


def _recommendation_text(item):
    return _plain_text(
        " ".join(
            str(item.get(field) or "")
            for field in ("title", "authorOrMeta", "description", "category")
        )
    )


def _recommendation_emoji(item):
    return TYPE_EMOJIS.get(item.get("type"), "🔎")


def _title_hashtag(title):
    words = re.findall(r"[a-zA-Z0-9]+", _plain_text(title))
    meaningful = list(words)
    while meaningful and meaningful[0] in HASHTAG_STOPWORDS:
        meaningful.pop(0)
    if not meaningful:
        return ""
    full_hashtag = "".join(word[:1].upper() + word[1:] for word in meaningful)
    if len(full_hashtag) <= 28:
        return full_hashtag
    compact_words = [
        word for word in meaningful if word not in HASHTAG_STOPWORDS
    ]
    compact_hashtag = "".join(
        word[:1].upper() + word[1:] for word in compact_words
    )
    if len(compact_hashtag) <= 28:
        return compact_hashtag
    hashtag = ""
    for word in compact_words:
        candidate = hashtag + word[:1].upper() + word[1:]
        if len(candidate) > 28:
            break
        hashtag = candidate
    return hashtag


def _caption_hashtags(selected):
    hashtags = ["Politometro"]
    all_text = " ".join(_recommendation_text(item) for item in selected.values())
    for qkey in REQUIRED_TYPES:
        item = selected[qkey]
        title_tag = _title_hashtag(item.get("title"))
        if title_tag:
            hashtags.append(title_tag)
    for keywords, hashtag in TOPIC_HASHTAGS:
        if any(keyword in all_text for keyword in keywords):
            hashtags.append(hashtag)

    unique = []
    seen = set()
    for hashtag in hashtags:
        key = hashtag.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(f"#{hashtag}")
    return " ".join(unique[:10])


def build_caption(selected):
    sections = []
    for qkey in REQUIRED_TYPES:
        item = selected[qkey]
        emoji = _recommendation_emoji(item)
        title = _ellipsize(item["title"], 110)
        author = _ellipsize(item.get("authorOrMeta", ""), 80)
        description = _ellipsize(item["description"], 220)
        author_suffix = f" ({author})" if author else ""
        sections.append(
            f"{emoji} {item['category'].upper()}: {title}{author_suffix}\n"
            f"{description}"
        )

    return (
        "📣 RECOMENDAÇÕES DA SEMANA • POLITÓMETRO\n\n"
        "Trazemos-te a nossa seleção semanal de conteúdos essenciais para "
        "compreenderes a política, a história e a economia de Portugal e do mundo.\n\n"
        "Desenvolvido por @_.davstrango._ e @luisflmaximo no âmbito do projeto "
        "@politiza.te.\n\n"
        + "\n\n".join(sections)
        + "\n\nQual destes vais espreitar primeiro? Diz-nos nos comentários e "
        "aproveita para deixar as tuas próprias sugestões para a próxima semana! 👇\n\n"
        "—\n"
        + _caption_hashtags(selected)
        + "\n"
    )


def _item_score(item, now):
    """Return the editorial priority score, or -1 for an expired item."""
    score = item.get("priority", 3)
    time_sensitive = item.get("type") in {"podcast", "highlight"}
    if time_sensitive and item.get("sourcePublishedAt"):
        try:
            published = datetime.datetime.fromisoformat(
                item["sourcePublishedAt"].replace("Z", "+00:00")
            )
            score += published.timestamp() / 86400
        except (AttributeError, TypeError, ValueError):
            pass
    expiry = item.get("expiryDate")
    if expiry:
        try:
            exp = datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            remaining = exp - now
            if remaining <= datetime.timedelta(0):
                return -1
            if (
                time_sensitive
                and remaining
                < datetime.timedelta(hours=MIN_REVIEW_VALIDITY_HOURS)
            ):
                return -1
            delta = remaining.days
            if delta < 14 and not time_sensitive:
                score += 10
        except (TypeError, ValueError):
            pass
    return score


def _cover_hash(item, cover):
    verification = item.get("verification") or {}
    cached_hash = verification.get("coverHash")
    if cached_hash:
        return cached_hash
    return hashlib.sha256(cover.convert("RGB").tobytes()).hexdigest()


def get_recommendations_with_valid_covers(queue):
    """
    Resolve identity, canonical link and cover as one atomic unit.

    Only explicitly approved `queue` entries can be selected. Ambiguous
    entities and invalid/generic images are skipped in favour of the next
    candidate. There is deliberately no production placeholder.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    active_items = [
        item
        for item in queue
        if item.get("status") == "queue" and _item_score(item, now) >= 0
    ]
    active_items.sort(key=lambda item: _item_score(item, now), reverse=True)

    selected = {}
    covers = {}
    seen_cover_hashes = set()

    for qkey, required_type in REQUIRED_TYPES.items():
        candidates = [
            item for item in active_items if item.get("type") == required_type
        ]
        failures = []

        for queue_item in candidates:
            try:
                # Prefer the recent identity-bound proof already produced by
                # population. The resolver refreshes it automatically when its
                # TTL, link proof or local cover manifest is no longer valid.
                resolved = resolve_recommendation(
                    copy.deepcopy(queue_item), force=False
                )
                if resolved.get("resolutionStatus") != "verified":
                    raise ValueError("o resolvedor não confirmou a entidade")
                if not resolved.get("link"):
                    raise ValueError("link canónico em falta")
                _revalidate_reviewed_source(qkey, resolved)

                cover = load_cover_for_item(resolved)
                if cover is None:
                    raise ValueError("capa raster verificada em falta")

                image_hash = _cover_hash(resolved, cover)
                if image_hash in seen_cover_hashes:
                    raise ValueError("imagem duplicada de outra recomendação")

                # Persist the exact canonical object that is reviewed.
                queue_item.clear()
                queue_item.update(resolved)
                selected[qkey] = queue_item
                covers[qkey] = cover
                seen_cover_hashes.add(image_hash)
                print(
                    f"  -> {qkey.upper()} verified: '{resolved['title']}' "
                    f"({resolved.get('verification', {}).get('source', 'source')})"
                )
                break
            except (ResolutionError, OSError, ValueError) as exc:
                failure = f"{queue_item.get('title', '<sem título>')}: {exc}"
                failures.append(failure)
                print(f"  [REJECTED] {failure}")

        if qkey not in selected:
            details = "; ".join(failures) if failures else "nenhum candidato aprovado na fila"
            raise RuntimeError(
                f"Não existe uma recomendação {required_type!r} totalmente "
                f"verificada para {qkey}. {details}"
            )

    return selected, covers

# --- TEXT WRAPPING ---
def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = []
    for word in words:
        current.append(word)
        line = " ".join(current)
        bbox = font.getbbox(line)
        w = bbox[2] - bbox[0]
        if w > max_width:
            current.pop()
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _ellipsize(value, max_chars):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return shortened + "…"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _draft_content_hash(
    quadrants, post_sha256, caption_sha256, is_test=False
):
    payload = {
        "quadrants": {key: quadrants[key] for key in sorted(REQUIRED_TYPES)},
        "post_sha256": post_sha256,
        "caption_sha256": caption_sha256,
        "is_test": bool(is_test),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _validate_publish_item(qkey, item, now=None):
    expected_type = REQUIRED_TYPES[qkey]
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(item, dict) or item.get("type") != expected_type:
        raise RuntimeError(f"{qkey} não contém um item do tipo {expected_type}")
    if item.get("status") != "queue":
        raise RuntimeError(f"{qkey} já não está aprovado na fila")
    if item.get("resolutionStatus") != "verified":
        raise RuntimeError(f"{qkey} não tem resolução verificada")
    verification = item.get("verification") or {}
    if (
        verification.get("status") != "verified"
        or not verification.get("entityId")
        or not verification.get("coverHash")
    ):
        raise RuntimeError(f"{qkey} tem evidência de verificação inválida")
    if not item.get("link"):
        raise RuntimeError(f"{qkey} não tem link canónico")
    if not str(item.get("imageUrl") or "").startswith("/covers/"):
        raise RuntimeError(f"{qkey} não tem uma capa local verificada")
    if not str(item.get("description") or "").strip():
        raise RuntimeError(f"{qkey} não tem uma descrição fundamentada")
    source_published = _parse_utc_datetime(item.get("sourcePublishedAt"))
    expiry = _parse_utc_datetime(item.get("expiryDate"))
    if item.get("type") in {"podcast", "highlight"} and (
        not source_published
        or not expiry
        or expiry <= source_published
    ):
        raise RuntimeError(
            f"{qkey} não tem um prazo temporal verificável"
        )
    if expiry and expiry <= now:
        raise RuntimeError(
            f"{qkey} expirou em {expiry.isoformat()}; gera uma proposta atualizada"
        )
    if (
        item.get("type") in {"podcast", "highlight"}
        and expiry
        and expiry
        < now + datetime.timedelta(hours=MIN_PUBLICATION_VALIDITY_HOURS)
    ):
        raise RuntimeError(
            f"{qkey} tem menos de {MIN_PUBLICATION_VALIDITY_HOURS} horas "
            "de validade; gera uma proposta atualizada"
        )
    if load_cover_for_item(item) is None:
        raise RuntimeError(f"{qkey} não tem uma imagem raster válida")


def _revalidate_reviewed_source(qkey, item):
    """Check live link availability without mutating the approved cover cache."""
    if not probe_verified_source(item):
        raise RuntimeError(
            f"{qkey} falhou a revalidação segura da fonte; "
            "o URL não devolveu uma página pública HTTP 200/206"
        )


def commit_approved_draft(
    draft_file,
    receipt_file=PUBLICATION_RECEIPT_PATH,
    require_publication_receipt=True,
    dry_run=False,
):
    """Validate/finalize exactly the reviewed canonical objects."""
    if not os.path.exists(draft_file):
        raise RuntimeError("Não existe um rascunho para publicar.")

    with open(draft_file, "r", encoding="utf-8") as handle:
        draft = json.load(handle)

    if draft.get("is_test"):
        raise RuntimeError("Um rascunho de teste nunca pode ser publicado.")

    draft_id = draft.get("draft_id")
    content_hash = draft.get("content_hash")
    approval = draft.get("approval") or {}
    if not draft_id or not content_hash:
        raise RuntimeError("O rascunho não possui identidade/hash de conteúdo.")
    if not approval.get("approved"):
        raise RuntimeError("O rascunho ainda não foi aprovado no Discord.")
    if approval.get("draft_id") != draft_id or approval.get("content_hash") != content_hash:
        raise RuntimeError("A aprovação não corresponde a este rascunho.")

    now = datetime.datetime.now(datetime.timezone.utc)
    created_at = _parse_utc_datetime(draft.get("created_at"))
    if not created_at:
        raise RuntimeError("O rascunho não tem uma data de criação válida.")
    max_age_hours = int(
        os.environ.get("MAX_DRAFT_AGE_HOURS", MAX_DRAFT_AGE_HOURS)
    )
    if now - created_at > datetime.timedelta(hours=max_age_hours):
        raise RuntimeError(
            "O rascunho aprovado está demasiado antigo; gera uma nova proposta."
        )

    if not os.path.exists(OUTPUT_PATH) or not os.path.exists(OUTPUT_CAPTION_PATH):
        raise RuntimeError("Os artefactos revistos do post estão em falta.")

    post_sha = _sha256_file(OUTPUT_PATH)
    caption_sha = _sha256_file(OUTPUT_CAPTION_PATH)
    if post_sha != draft.get("post_sha256") or caption_sha != draft.get("caption_sha256"):
        raise RuntimeError("A imagem ou legenda mudou depois da aprovação.")

    quadrants = {key: draft.get(key) for key in REQUIRED_TYPES}
    for qkey, item in quadrants.items():
        _validate_publish_item(qkey, item, now=now)
    expected_hash = _draft_content_hash(
        quadrants,
        post_sha,
        caption_sha,
        is_test=draft.get("is_test", False),
    )
    if expected_hash != content_hash:
        raise RuntimeError("O conteúdo do rascunho não corresponde ao hash aprovado.")

    with open(REC_FILE, "r", encoding="utf-8") as handle:
        current_data = json.load(handle)
    current_queue = current_data.get("queue", [])
    current_by_id = {item.get("id"): item for item in current_queue}
    selected_ids = {item["id"] for item in quadrants.values()}
    missing_ids = selected_ids.difference(current_by_id)
    if missing_ids:
        raise RuntimeError(
            "A fila mudou depois da revisão; itens em falta: "
            + ", ".join(sorted(missing_ids))
        )
    for qkey, reviewed in quadrants.items():
        current = current_by_id[reviewed["id"]]
        for field in (
            "type",
            "status",
            "resolutionStatus",
            "externalId",
            "link",
            "imageUrl",
            "sourcePublishedAt",
            "expiryDate",
        ):
            if current.get(field) != reviewed.get(field):
                raise RuntimeError(
                    f"{qkey} foi alterado ou substituído depois da revisão"
                )
        current_verification = current.get("verification") or {}
        reviewed_verification = reviewed.get("verification") or {}
        for field in ("entityId", "coverHash"):
            if current_verification.get(field) != reviewed_verification.get(field):
                raise RuntimeError(
                    f"{qkey} já não corresponde à entidade/imagem revista"
                )

    if dry_run:
        for qkey, item in quadrants.items():
            _revalidate_reviewed_source(qkey, item)
        return draft, quadrants

    receipt = {}
    if require_publication_receipt:
        if not os.path.exists(receipt_file):
            raise RuntimeError("O Instagram ainda não confirmou esta publicação.")
        with open(receipt_file, "r", encoding="utf-8") as handle:
            receipt = json.load(handle)
        if (
            receipt.get("draft_id") != draft_id
            or receipt.get("content_hash") != content_hash
            or not receipt.get("post_id")
        ):
            raise RuntimeError(
                "O recibo do Instagram não corresponde ao rascunho aprovado."
            )

    with open(REC_FILE, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    queue = data.get("queue", [])
    history = data.get("history", [])

    queued_by_id = {item.get("id"): item for item in queue}
    selected_ids = {item["id"] for item in quadrants.values()}
    missing_ids = selected_ids.difference(queued_by_id)
    if missing_ids:
        raise RuntimeError(
            "A fila mudou depois da revisão; itens em falta: "
            + ", ".join(sorted(missing_ids))
        )

    published_at = now.isoformat()
    history_ids = {item.get("id") for item in history}
    for qkey in REQUIRED_TYPES:
        reviewed_item = copy.deepcopy(quadrants[qkey])
        reviewed_item["status"] = "published"
        reviewed_item["publishedAt"] = published_at
        reviewed_item["publishedDraftId"] = draft_id
        if receipt.get("post_id"):
            reviewed_item["instagramPostId"] = receipt["post_id"]
        if reviewed_item["id"] not in history_ids:
            history.append(reviewed_item)
            history_ids.add(reviewed_item["id"])

    data["queue"] = [item for item in queue if item.get("id") not in selected_ids]
    data["history"] = history
    with open(REC_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)

    os.remove(draft_file)
    print(
        f"[OK] Draft {draft_id} aprovado e gravado integralmente no histórico."
    )


# --- MAIN ---
def generate_production_post():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", action="store_true", help="Generate draft post for review without modifying database")
    parser.add_argument("--commit", action="store_true", help="Commit the currently approved review draft to database")
    parser.add_argument("--verify-approved", action="store_true", help="Validate an approved draft without changing state")
    parser.add_argument("--test", action="store_true", help="Mark the generated draft as a test run")
    args = parser.parse_args()

    DRAFT_FILE = os.path.join(SCRIPT_DIR, "review_draft.json")

    if args.commit:
        try:
            commit_approved_draft(DRAFT_FILE)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)
        return

    if args.verify_approved:
        try:
            draft, _ = commit_approved_draft(
                DRAFT_FILE,
                require_publication_receipt=False,
                dry_run=True,
            )
            print(f"[OK] Draft {draft['draft_id']} aprovado e ainda válido.")
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)
        return

    if not args.review:
        print(
            "ERROR: A geração autónoma exige --review; a publicação só ocorre "
            "depois da aprovação no Discord."
        )
        sys.exit(1)

    ensure_fonts()
    
    with open(REC_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    queue = data.get("queue", [])
    history = data.get("history", [])
    
    selected, covers = get_recommendations_with_valid_covers(queue)
    
    missing = [q for q in ["q1", "q2", "q3", "q4"] if q not in selected]
    if missing:
        print(f"ERROR: Missing quadrants: {missing}")
        sys.exit(1)
    
    # Load clean template
    template = Image.open(TEMPLATE_PATH).convert("RGBA")
    draw = ImageDraw.Draw(template)
    
    # Load fonts
    try:
        title_font = ImageFont.truetype(FONT_BOLD, 32)
        label_font = ImageFont.truetype(FONT_REG, 18)
        desc_font = ImageFont.truetype(FONT_DESC_BOLD, 15)
    except Exception as e:
        print(f"Font error: {e}")
        title_font = label_font = desc_font = ImageFont.load_default()
    
    print("\nCompositing post...")
    
    # Pre-render text lines and compute title heights
    title_lines_map = {}
    title_bottoms = {}
    
    for qkey in ["q1", "q2", "q3", "q4"]:
        item = selected[qkey]
        config = QUADRANTS_CONFIG[qkey]
        
        # Draw category label (older site submissions did not include it).
        category = item.get("category") or {
            "book": "Livro",
            "podcast": "Podcast",
            "movie": "Filme",
            "highlight": "Destaque",
        }.get(item.get("type"), "Recomendação")
        item["category"] = category
        draw.text(config["label_pos"], category, fill=TEXT_COLOR, font=label_font)
        
        # Wrap title
        tx, ty = config["title_pos"]
        lines = wrap_text(draw, item["title"], title_font, 350)
        if len(lines) > 2:
            lines = lines[:2]
            # Truncate the second line to add '...'
            words = lines[1].split()
            if len(words) > 1:
                lines[1] = " ".join(words[:-1]) + "..."
            else:
                lines[1] += "..."
        title_lines_map[qkey] = lines
        
        # Draw title
        curr_y = ty
        for line in lines:
            draw.text((tx, curr_y), line, fill=TEXT_COLOR, font=title_font)
            curr_y += 34
        
        title_bottoms[qkey] = curr_y

    # Determine Cover Dimensions based on item TYPE dynamically
    cover_dims = {}
    for qkey in ["q1", "q2", "q3", "q4"]:
        item = selected[qkey]
        if item["type"] == "podcast":
            cover_dims[qkey] = (192, 192)
        elif item["type"] == "highlight":
            # Editorial/YouTube thumbnails are normally landscape.
            cover_dims[qkey] = (160, 90)
        else:
            cover_dims[qkey] = (160, 220)

    # --- ROW 1 (TOP) DYNAMIC ALIGNMENT ---
    gap_q1 = 18 if len(title_lines_map["q1"]) >= 2 else 12
    gap_q2 = 18 if len(title_lines_map["q2"]) >= 2 else 12
    
    h_q1 = cover_dims["q1"][1]
    h_q2 = cover_dims["q2"][1]
    
    q1_min_bottom = title_bottoms["q1"] + gap_q1 + h_q1
    q2_min_bottom = title_bottoms["q2"] + gap_q2 + h_q2
    
    common_bottom_y = max(q1_min_bottom, q2_min_bottom)
    
    cover_y_map = {
        "q1": common_bottom_y - h_q1,
        "q2": common_bottom_y - h_q2
    }
    
    # --- ROW 2 (BOTTOM) DYNAMIC ALIGNMENT ---
    gap_q3 = 18 if len(title_lines_map["q3"]) >= 2 else 12
    gap_q4 = 18 if len(title_lines_map["q4"]) >= 2 else 12
    
    q3_top_y = title_bottoms["q3"] + gap_q3
    q4_top_y = title_bottoms["q4"] + gap_q4
    
    common_top_y = max(q3_top_y, q4_top_y)
    
    cover_y_map["q3"] = common_top_y
    cover_y_map["q4"] = common_top_y

    # --- PASTE COVERS AND WRITE DESCRIPTIONS ---
    for qkey in ["q1", "q2", "q3", "q4"]:
        config = QUADRANTS_CONFIG[qkey]
        item = selected[qkey]
        cover = covers[qkey]
        
        cover_w, cover_h = cover_dims[qkey]
        cover_y = cover_y_map[qkey]
        cx = config["cover_x"]
        
        # Crop to the target aspect ratio without stretching the source image.
        cover_resized = ImageOps.fit(
            cover.convert("RGB"),
            (cover_w, cover_h),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        cover_rounded = apply_rounded_corners(cover_resized, radius=18)
        
        template.alpha_composite(cover_rounded, (cx, cover_y))
        
        # Wrap description
        dx = cx + cover_w + 15
        if qkey in ["q1", "q3"]:
            desc_w = 400 - dx
        else:
            desc_w = 780 - dx
            
        desc_lines = wrap_text(draw, item["description"], desc_font, desc_w)
        
        spacing = 18
        max_lines = 11
        text_block_h = len(desc_lines[:max_lines]) * spacing
        dy = cover_y + (cover_h - text_block_h) // 2
        
        for line in desc_lines[:max_lines]:
            draw.text((dx, dy), line, fill=TEXT_COLOR, font=desc_font)
            dy += spacing
            
    # Save image
    output = ImageOps.fit(
        template.convert("RGB"),
        (1080, 1350),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    output.save(
        OUTPUT_PATH,
        "JPEG",
        quality=95,
        optimize=True,
        progressive=True,
    )
    print(f"\n[OK] Production post image saved to: {OUTPUT_PATH}")
    
    # 5. Generate the caption from the exact recommendations in this draft.
    caption = build_caption(selected)
    if len(caption) > 1800:
        raise RuntimeError(
            f"A legenda excede o limite editorial seguro ({len(caption)} caracteres)."
        )
    
    with open(OUTPUT_CAPTION_PATH, "w", encoding="utf-8") as f:
        f.write(caption)
    print(f"[OK] Production Instagram caption saved to: {OUTPUT_CAPTION_PATH}")
    
    if args.review:
        # Persist canonical links/metadata so the queue shown on the website is
        # identical to the draft that reaches Discord.
        data["queue"] = queue
        data["history"] = history
        with open(REC_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        quadrants = {qkey: selected[qkey] for qkey in REQUIRED_TYPES}
        post_sha = _sha256_file(OUTPUT_PATH)
        caption_sha = _sha256_file(OUTPUT_CAPTION_PATH)
        content_hash = _draft_content_hash(
            quadrants,
            post_sha,
            caption_sha,
            is_test=args.test,
        )
        draft_data = {
            "schema_version": 2,
            "draft_id": f"draft_{content_hash[:20]}",
            "content_hash": content_hash,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "is_test": args.test,
            "post_sha256": post_sha,
            "caption_sha256": caption_sha,
            "approval": {"approved": False},
            **quadrants,
        }
        with open(DRAFT_FILE, "w", encoding="utf-8") as f:
            json.dump(draft_data, f, indent=2, ensure_ascii=False)
        print(
            f"[OK] Review draft {draft_data['draft_id']} saved with immutable hashes."
        )
        return
    
    # 6. Update database recommendations.json (Production actions)
    selected_ids = [item["id"] for item in selected.values()]
    updated_queue = []
    
    for item in queue:
        if item["id"] in selected_ids:
            item["status"] = "published"
            history.append(item)
        else:
            updated_queue.append(item)
            
    data["queue"] = updated_queue
    data["history"] = history
    
    with open(REC_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("[OK] Updated recommendations.json database successfully.")
    
    # Pool size warning
    remaining_types = {}
    for item in updated_queue:
        itype = item["type"]
        remaining_types[itype] = remaining_types.get(itype, 0) + 1
        
    for t in ["book", "podcast", "movie", "highlight"]:
        count = remaining_types.get(t, 0)
        if count < 3:
            print(f"[WARNING] Pool depletion warning: Only {count} items of type '{t}' left in the queue!")

if __name__ == "__main__":
    generate_production_post()
