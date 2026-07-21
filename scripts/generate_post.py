"""
Politometro - Instagram Post Generator (Production Version)
Generates the Instagram post image and caption using the template and auto-fetched cover art.
Supports completely dynamic recommendation selection, ensuring NO repeating types among
the 3 general slots, and no duplication with the weekly highlight.
Features:
- Cover dimensions are tied to the item TYPE, not the quadrant:
  * Podcasts are always rendered at 192x192 (square)
  * Books and Movies are rendered at 160x220 (vertical)
  * Highlights and Podcasts are rendered at 192x192 (square)
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
from PIL import Image, ImageDraw, ImageFont, ImageOps
import requests

# Import the single, source-grounded resolver.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cover_fetcher import load_cover_for_item
from recommendation_resolver import (
    ResolutionError,
    is_eligible_highlight,
    is_same_series,
    is_series_recency_restricted,
    probe_verified_source,
    resolve_recommendation,
)

# --- PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "post_template.jpg")
TEMPLATE_CANVAS_SIZE = (819, 1024)
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
    },
    "w1": {
        "label_pos": (50, 150),
        "title_pos": (50, 175),
        "cover_x": 50,
    },
    "w2": {
        "label_pos": (50, 525),
        "title_pos": (50, 550),
        "cover_x": 50,
    },
}

DESCRIPTION_CHAR_LIMITS = {
    "q1": 220,
    "q2": 138,
    "q3": 220,
    "q4": 145,
    "w1": 260,
    "w2": 260,
}
DESCRIPTION_LINE_LIMITS = {
    "q1": 11,
    "q2": 8,
    "q3": 11,
    "q4": 8,
    "w1": 9,
    "w2": 9,
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


# --- SOURCE-GROUNDED SELECTION & QUALITY GATE ---# --- SOURCE-GROUNDED SELECTION & QUALITY GATE ---
SUNDAY_Q3_TYPES = ("investigation", "movie")
WEDNESDAY_Q3_TYPES = ("nostalgia",)
ROTATING_Q3_TYPES = ("nostalgia", "investigation", "movie")

REQUIRED_SLOTS_FOR_POST_TYPE = {
    "sunday_standard": {
        "q1": "book",
        "q2": "podcast",
        "q3": "movie",
        "q4": "highlight",
    },
    "wednesday_nostalgia": {
        "w1": "nostalgia",
    },
}

REQUIRED_TYPES = REQUIRED_SLOTS_FOR_POST_TYPE["sunday_standard"]


def _slot_types(qkey, post_type="sunday_standard"):
    if post_type == "wednesday_nostalgia":
        if qkey == "w1":
            return ("nostalgia",)
    if qkey == "q1":
        return ("book",)
    elif qkey == "q2":
        return ("podcast",)
    elif qkey == "q3":
        week = datetime.datetime.now(datetime.timezone.utc).isocalendar().week
        preferred = SUNDAY_Q3_TYPES[week % len(SUNDAY_Q3_TYPES)]
        return (preferred,) + tuple(
            media_type
            for media_type in SUNDAY_Q3_TYPES
            if media_type != preferred
        )
    elif qkey == "q4":
        return ("highlight", "investigation", "movie", "podcast")
    return (REQUIRED_TYPES.get(qkey, "highlight"),)

TYPE_EMOJIS = {
    "book": "📖",
    "podcast": "🎙️",
    "movie": "🎞️",
    "documentary": "🎥",
    "series": "📺",
    "nostalgia": "📼",
    "investigation": "🔎",
    "highlight": "📰",
}

CAPTION_HASHTAGS = (
    "#Portugal",
    "#PolitizaTe",
    "#Recomendacoes",
    "#Sugestoes",
    "#Politometro",
    "#Politica",
)

TYPE_HASHTAGS = {
    "book": "#Livro",
    "podcast": "#Podcast",
    "movie": "#Filme",
    "documentary": "#Documentario",
    "series": "#Serie",
    "nostalgia": "#Nostalgia",
    "investigation": "#Investigacao",
}

CATEGORY_HASHTAGS = {
    "livro": "#Livro",
    "podcast": "#Podcast",
    "filme": "#Filme",
    "série": "#Serie",
    "serie": "#Serie",
    "documentário": "#Documentario",
    "documentario": "#Documentario",
    "investigação": "#Investigacao",
    "investigacao": "#Investigacao",
    "nostalgia": "#Nostalgia",
    "artigo": "#Artigo",
    "artigo de opinião": "#ArtigoDeOpiniao",
    "artigo de opiniao": "#ArtigoDeOpiniao",
}


def _recommendation_emoji(item):
    return TYPE_EMOJIS.get(item.get("type"), "🔎")


def _caption_hashtags(selected, post_type="sunday_standard"):
    hashtags = list(CAPTION_HASHTAGS)
    if post_type == "wednesday_nostalgia":
        if "#Classicos" not in hashtags:
            hashtags.append("#Classicos")
    for qkey in selected.keys():
        item = selected.get(qkey)
        if not item:
            continue
        category = str(item.get("category") or "").strip().casefold()
        hashtag = CATEGORY_HASHTAGS.get(category)
        if not hashtag:
            hashtag = TYPE_HASHTAGS.get(item.get("type"))
        if hashtag and hashtag not in hashtags:
            hashtags.append(hashtag)
    forbidden = {"#humorpolitico", "#quartanostalgia", "#satirapolitica"}
    final_hashtags = [h for h in hashtags if h.casefold() not in forbidden]
    return " ".join(final_hashtags)


def build_caption(selected, post_type="sunday_standard"):
    sections = []
    for qkey in selected.keys():
        item = selected[qkey]
        emoji = _recommendation_emoji(item)
        title = _ellipsize(item["title"], 110)
        author = _ellipsize(item.get("authorOrMeta", ""), 80)
        clean_desc = _sanitize_description(item.get("description", ""), item.get("title", ""))
        description = _compact_text(clean_desc, 220)
        author_suffix = f" ({author})" if author else ""
        sections.append(
            f"{emoji} {item['category'].upper()}: {title}{author_suffix}\n"
            f"{description}"
        )

    if post_type == "wednesday_nostalgia":
        title_line = "📣 RECOMENDAÇÕES DO POLITÓMETRO\n\n"
        intro_line = (
            "Trazemos-te a nossa seleção de meio da semana com um grande clássico "
            "do nosso arquivo e uma sugestão imperdível!\n\n"
        )
    else:
        title_line = "📣 RECOMENDAÇÕES DO POLITÓMETRO\n\n"
        intro_line = (
            "Trazemos-te a nossa seleção semanal de conteúdos essenciais para "
            "compreenderes a política, a história e a economia de Portugal e do mundo.\n\n"
        )

    return (
        title_line
        + intro_line
        + "Desenvolvido por @_.davstrango._ e @luisflmaximo no âmbito do projeto "
        "@politiza.te.\n\n"
        + "\n\n".join(sections)
        + "\n\nQual destes vais espreitar primeiro? Diz-nos nos comentários e "
        "aproveita para deixar as tuas próprias sugestões para a próxima semana! 👇\n\n"
        + "—\n"
        + _caption_hashtags(selected, post_type=post_type)
        + "\n"
    )


def _item_score(item, now):
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


def get_recommendations_with_valid_covers(queue, history=None, post_type="sunday_standard"):
    """
    Resolve identity, canonical link and cover as one atomic unit.

    Only explicitly approved `queue` entries can be selected. Ambiguous
    entities and invalid/generic images are skipped in favour of the next
    candidate. There is deliberately no production placeholder.
    """
    if history is None:
        try:
            with open(REC_FILE, "r", encoding="utf-8") as f:
                rec_data = json.load(f)
            history = rec_data.get("history", [])
        except Exception:
            history = []

    now = datetime.datetime.now(datetime.timezone.utc)
    active_items = [
        item
        for item in queue
        if item.get("status") == "queue"
        and _item_score(item, now) >= 0
        and (
            item.get("type") != "highlight"
            or is_eligible_highlight(
                title=item.get("title"),
                description=item.get("description"),
                link=item.get("link"),
                categories=(
                    (item.get("_discovery") or {}).get("categories", [])
                    if isinstance(item.get("_discovery"), dict)
                    else []
                ),
            )
        )
    ]
    active_items.sort(key=lambda item: _item_score(item, now), reverse=True)

    selected = {}
    covers = {}
    seen_cover_hashes = set()

    target_slots = REQUIRED_SLOTS_FOR_POST_TYPE.get(post_type, REQUIRED_SLOTS_FOR_POST_TYPE["sunday_standard"])
    for qkey, required_type in target_slots.items():
        allowed_types = _slot_types(qkey, post_type=post_type)
        candidates = [
            item
            for media_type in allowed_types
            for item in active_items
            if item.get("type") == media_type
        ]
        failures = []

        for queue_item in candidates:
            try:
                if is_series_recency_restricted(queue_item, history, now):
                    raise ValueError(
                        "série/podcast recomendado nas últimas 4 semanas (restrição de recência)"
                    )
                if any(
                    is_same_series(queue_item, sel_item)
                    for sel_item in selected.values()
                ):
                    raise ValueError("mesma série/podcast já selecionada no post atual")

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


def _sanitize_description(description, title=""):
    import html
    text = html.unescape(str(description or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"www\.\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if title:
        norm_title = re.sub(r"\s+", " ", str(title)).strip()
        if norm_title and len(norm_title) >= 5:
            if text.startswith(norm_title):
                text = text[len(norm_title):].strip()
            elif text.lower().startswith(norm_title.lower()):
                text = text[len(norm_title):].strip()
            else:
                parts = [p.strip() for p in re.split(r"[:—|-]", norm_title) if len(p.strip()) >= 8]
                for p in parts:
                    if text.startswith(p):
                        text = text[len(p):].strip()
                        break

    text = re.sub(r"^[\s:—\-.,;]+", "", text).strip()
    text = re.sub(r"(?:\.{2,}|…)\s*$", "", text).strip()
    return text or re.sub(r"(?:\.{2,}|…)\s*$", "", str(description or "").strip()).strip()


def _ellipsize(value, max_chars):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return shortened + "…"

def _compact_text(value, max_chars):
    """Shorten text at natural boundaries without adding ellipses."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?:\.{2,}|…)\s*$", "", text).strip()
    if len(text) <= max_chars:
        return text

    sentence_matches = list(re.finditer(r"[.!?](?:\s|$)", text))
    best_complete = ""
    for match in sentence_matches:
        candidate = text[: match.end()].strip()
        if len(candidate) <= max_chars:
            best_complete = candidate
        else:
            break
    if best_complete:
        return re.sub(r"(?:\.{2,}|…)\s*$", "", best_complete)

    shortened = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:.!?…")
    return re.sub(r"(?:\.{2,}|…)\s*$", "", shortened) or re.sub(r"(?:\.{2,}|…)\s*$", "", text[:max_chars].rstrip(" ,;:.!?…"))

def remove_black_bars(image, threshold=28):
    """Detect and crop black/dark letterbox or pillarbox borders from an image."""
    try:
        img_rgb = image.convert("RGB") if image.mode not in ("RGB", "RGBA") else image
        gray = img_rgb.convert("L")
        w, h = gray.size
        if w < 50 or h < 50:
            return image

        bw = gray.point(lambda p: 255 if p > threshold else 0)
        bbox = bw.getbbox()
        if not bbox:
            return image

        left, top, right, bottom = bbox
        crop_w = right - left
        crop_h = bottom - top

        if (left > 0 or top > 0 or right < w or bottom < h) and (crop_w >= w * 0.4) and (crop_h >= h * 0.4):
            return image.crop((left, top, right, bottom))
    except Exception:
        pass
    return image


def _fit_text_lines(
    draw,
    text,
    font_path,
    start_size,
    min_size,
    max_width,
    max_lines,
):
    compact = str(text or "").strip()
    for size in range(start_size, min_size - 1, -1):
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception:
            font = ImageFont.load_default()
        lines = wrap_text(draw, compact, font, max_width)
        if len(lines) <= max_lines:
            return font, lines, max(16, size + 3)

    try:
        font = ImageFont.truetype(font_path, min_size)
    except Exception:
        font = ImageFont.load_default()
    lines = wrap_text(draw, compact, font, max_width)
    if len(lines) > max_lines:
        sentence_ends = [
            match.end()
            for match in re.finditer(r"[.!?](?:\s|$)", compact)
            if match.end() < len(compact)
        ]
        for sentence_end in reversed(sentence_ends):
            complete = compact[:sentence_end].strip()
            complete_lines = wrap_text(draw, complete, font, max_width)
            if len(complete_lines) <= max_lines:
                return font, complete_lines, max(15, min_size + 3)

        words = compact.rstrip(" .!?").split()
        while words:
            candidate = " ".join(words).rstrip(" ,;:.!?")
            while words and words[-1].casefold() in {
                "a",
                "as",
                "com",
                "da",
                "das",
                "de",
                "do",
                "dos",
                "e",
                "em",
                "na",
                "nas",
                "no",
                "nos",
                "o",
                "os",
                "ou",
                "para",
                "por",
                "que",
            }:
                words.pop()
                candidate = " ".join(words).rstrip(" ,;:.!?")
            candidate = candidate + "." if candidate else ""
            candidate_lines = wrap_text(draw, candidate, font, max_width)
            if candidate and len(candidate_lines) <= max_lines:
                lines = candidate_lines
                break
            if not words:
                lines = []
                break
            words.pop()
    return font, lines, max(15, min_size + 3)


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
        "quadrants": {key: quadrants[key] for key in sorted(quadrants.keys())},
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
    allowed_types = (
        ROTATING_Q3_TYPES if qkey == "q3" else (expected_type,)
    )
    if not isinstance(item, dict) or item.get("type") not in allowed_types:
        raise RuntimeError(
            f"{qkey} não contém um item de um tipo permitido: "
            + ", ".join(allowed_types)
        )
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
    if item.get("type") == "highlight" and not is_eligible_highlight(
        title=item.get("title"),
        description=item.get("description"),
        link=item.get("link"),
        categories=(
            (item.get("_discovery") or {}).get("categories", [])
            if isinstance(item.get("_discovery"), dict)
            else []
        ),
    ):
        raise RuntimeError(f"{qkey} é uma notícia e não um destaque editorial")
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
    parser.add_argument(
        "--post-type",
        choices=["auto", "sunday_standard", "wednesday_nostalgia"],
        default="auto",
        help="Specify post edition (sunday_standard or wednesday_nostalgia)",
    )
    args = parser.parse_args()

    post_type = args.post_type
    if post_type == "auto":
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        post_type = "wednesday_nostalgia" if now_utc.weekday() == 2 else "sunday_standard"

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
    
    selected, covers = get_recommendations_with_valid_covers(queue, history=history, post_type=post_type)
    
    slot_keys = list(selected.keys())
    missing = [q for q in slot_keys if q not in selected]
    if missing:
        print(f"ERROR: Missing slots: {missing}")
        sys.exit(1)
    
    # Normalise full-resolution template assets to the coordinate canvas used
    # below. This keeps the supplied 4:5 artwork intact and the layout stable.
    with Image.open(TEMPLATE_PATH) as template_source:
        template = ImageOps.fit(
            template_source.convert("RGBA"),
            TEMPLATE_CANVAS_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
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
    
    if len(slot_keys) == 1:
        qkey = slot_keys[0]
        item = selected[qkey]
        
        category = item.get("category") or {
            "nostalgia": "Nostalgia",
            "podcast": "Podcast",
            "book": "Livro",
            "movie": "Filme",
            "investigation": "Investigação",
            "highlight": "Destaque",
        }.get(item.get("type"), "Recomendação")
        item["category"] = category
        
        center_x = 410  # 819 // 2
        
        # 1. Draw Category Label centered at top (24px)
        solo_label_font = ImageFont.truetype(FONT_REG, 24)
        cat_text = category.upper()
        cat_bbox = solo_label_font.getbbox(cat_text)
        cat_w = cat_bbox[2] - cat_bbox[0]
        draw.text((center_x - cat_w // 2, 145), cat_text, fill=TEXT_COLOR, font=solo_label_font)
        
        # 2. Draw Title centered across full width (700px) with larger font (40px -> 26px)
        raw_title = item.get("title", "")
        if len(raw_title) > 65:
            raw_title = _ellipsize(raw_title, 65)
        item["title"] = raw_title

        fitted_title_font, lines, title_spacing = _fit_text_lines(
            draw,
            raw_title,
            FONT_BOLD,
            40,
            26,
            700,
            3,
        )
        curr_y = 180
        for line in lines:
            bbox = fitted_title_font.getbbox(line)
            line_w = bbox[2] - bbox[0]
            draw.text((center_x - line_w // 2, curr_y), line, fill=TEXT_COLOR, font=fitted_title_font)
            curr_y += title_spacing
            
        # 3. Draw Centered Hero Cover Banner (660x360px)
        cover = remove_black_bars(covers[qkey])
        cover_w, cover_h = 660, 360
        cover_x = (819 - cover_w) // 2
        cover_y = curr_y + 15
        
        cover_resized = ImageOps.fit(
            cover.convert("RGB"),
            (cover_w, cover_h),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        cover_rounded = apply_rounded_corners(cover_resized, radius=24)
        template.alpha_composite(cover_rounded, (cover_x, cover_y))
        
        # 4. Draw Centered Description below Hero Cover with larger font (20px -> 15px)
        clean_desc = _sanitize_description(item.get("description", ""), item.get("title", ""))
        item["description"] = clean_desc
        description = _compact_text(clean_desc, 320)
        
        desc_y = cover_y + cover_h + 20
        fitted_desc_font, desc_lines, desc_spacing = _fit_text_lines(
            draw,
            description,
            FONT_DESC_BOLD,
            20,
            15,
            700,
            6,
        )
        for line in desc_lines[:6]:
            bbox = fitted_desc_font.getbbox(line)
            line_w = bbox[2] - bbox[0]
            draw.text((center_x - line_w // 2, desc_y), line, fill=TEXT_COLOR, font=fitted_desc_font)
            desc_y += desc_spacing
    else:
        # Pre-render text lines and compute title heights
        title_lines_map = {}
        title_bottoms = {}
        
        for qkey in slot_keys:
            item = selected[qkey]
            config = QUADRANTS_CONFIG[qkey]
            
            # Draw category label (older site submissions did not include it).
            category = item.get("category") or {
                "book": "Livro",
                "podcast": "Podcast",
                "movie": "Filme",
                "nostalgia": "Nostalgia",
                "investigation": "Investigação",
                "highlight": "Destaque",
            }.get(item.get("type"), "Recomendação")
            item["category"] = category
            draw.text(config["label_pos"], category, fill=TEXT_COLOR, font=label_font)
            
            # Wrap title
            tx, ty = config["title_pos"]
            title_max_lines = 3 if (qkey in ["q2", "q3", "q4", "w1", "w2"] or len(item.get("title", "")) > 55) else 2
            raw_title = item.get("title", "")
            if len(raw_title) > 55 and "empresa de construção" in raw_title.lower():
                raw_title = re.sub(r"burlar dezenas de famílias com empresa de construção", "burla na construção", raw_title, flags=re.I)
            elif len(raw_title) > 65:
                raw_title = _ellipsize(raw_title, 65)
            item["title"] = raw_title

            max_title_width = 700 if qkey in ["w1", "w2"] else 350
            fitted_title_font, lines, title_spacing = _fit_text_lines(
                draw,
                raw_title,
                FONT_BOLD,
                30,
                18,
                max_title_width,
                title_max_lines,
            )
            title_lines_map[qkey] = lines
            
            # Draw title
            curr_y = ty
            for line in lines:
                draw.text((tx, curr_y), line, fill=TEXT_COLOR, font=fitted_title_font)
                curr_y += title_spacing
            
            title_bottoms[qkey] = curr_y

        # Determine Cover Dimensions based on item TYPE dynamically
        cover_dims = {}
        for qkey in slot_keys:
            item = selected[qkey]
            if qkey in ["w1", "w2"]:
                cover_dims[qkey] = (200, 200) if item["type"] in ["podcast", "highlight", "nostalgia"] else (160, 220)
            elif item["type"] in ["podcast", "highlight"]:
                cover_dims[qkey] = (192, 192)
            else:
                cover_dims[qkey] = (160, 220)

        cover_y_map = {}
        if "q1" in slot_keys and "q2" in slot_keys:
            gap_q1 = 18 if len(title_lines_map["q1"]) >= 2 else 12
            gap_q2 = 18 if len(title_lines_map["q2"]) >= 2 else 12
            h_q1 = cover_dims["q1"][1]
            h_q2 = cover_dims["q2"][1]
            q1_min_bottom = title_bottoms["q1"] + gap_q1 + h_q1
            q2_min_bottom = title_bottoms["q2"] + gap_q2 + h_q2
            common_bottom_y = max(q1_min_bottom, q2_min_bottom)
            cover_y_map["q1"] = common_bottom_y - h_q1
            cover_y_map["q2"] = common_bottom_y - h_q2
            
            gap_q3 = 18 if len(title_lines_map["q3"]) >= 2 else 12
            gap_q4 = 18 if len(title_lines_map["q4"]) >= 2 else 12
            q3_top_y = title_bottoms["q3"] + gap_q3
            q4_top_y = title_bottoms["q4"] + gap_q4
            common_top_y = max(q3_top_y, q4_top_y)
            cover_y_map["q3"] = common_top_y
            cover_y_map["q4"] = common_top_y
        else:
            cover_y_map["w1"] = max(title_bottoms.get("w1", 195) + 15, 230)
            cover_y_map["w2"] = max(title_bottoms.get("w2", 570) + 15, 600)

        # --- PASTE COVERS AND WRITE DESCRIPTIONS ---
        for qkey in slot_keys:
            config = QUADRANTS_CONFIG[qkey]
            item = selected[qkey]
            cover = remove_black_bars(covers[qkey])
            
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
            dx = cx + cover_w + 20
            if qkey in ["w1", "w2"]:
                desc_w = 760 - dx
            elif qkey in ["q1", "q3"]:
                desc_w = 400 - dx
            else:
                desc_w = 780 - dx
                
            clean_desc = _sanitize_description(item.get("description", ""), item.get("title", ""))
            item["description"] = clean_desc
            description = _compact_text(
                clean_desc,
                DESCRIPTION_CHAR_LIMITS.get(qkey, 240),
            )
            
            spacing = 18
            max_lines = min(
                DESCRIPTION_LINE_LIMITS.get(qkey, 8),
                max(1, cover_h // spacing),
            )
            fitted_font, desc_lines, spacing = _fit_text_lines(
                draw,
                description,
                FONT_DESC_BOLD,
                15,
                11,
                desc_w,
                max_lines,
            )
            text_block_h = len(desc_lines[:max_lines]) * spacing
            dy = cover_y + max(0, (cover_h - text_block_h) // 2)
            
            for line in desc_lines[:max_lines]:
                draw.text((dx, dy), line, fill=TEXT_COLOR, font=fitted_font)
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
    caption = build_caption(selected, post_type=post_type)
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

        quadrants = {qkey: selected[qkey] for qkey in selected.keys()}
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
            "post_type": post_type,
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
