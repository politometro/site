"""
Politometro - Automatic Cover Image Fetcher v4
Fetches cover images automatically based on title, type, and author.

Strategies per type:
- book: site:wook.pt search on DuckDuckGo HTML -> scrape wook.pt detail page with browser headers -> cache
- podcast: iTunes Search API (combined short "title + author", fallback "title" alone)
- movie: OMDB API (free key "trilogy") -> cache
- highlight: OMDB API (documentaries) -> cache

All results are cached locally to avoid repeated downloads.
"""
import os
import re
import json
import hashlib
import requests
from urllib.parse import unquote
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cover_cache")

# Common headers
HEADERS_API = {
    "User-Agent": "Politometro/1.0 (https://politometro.pt; contact@politometro.pt)",
    "Accept": "application/json"
}
HEADERS_IMG = {
    "User-Agent": "Politometro/1.0 (https://politometro.pt; contact@politometro.pt)",
    "Accept": "image/*,*/*;q=0.8"
}
# Browser emulation headers to bypass bot protection on Wook.pt
HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

# ===================== CACHE =====================
def _cache_key(title, media_type):
    raw = f"{media_type}_{title}".lower()
    safe = re.sub(r'[^a-z0-9]', '_', raw)[:60]
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"{safe}_{h}"

def _get_cached(title, media_type):
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(title, media_type)
    for ext in [".jpg", ".png"]:
        path = os.path.join(CACHE_DIR, key + ext)
        if os.path.exists(path) and os.path.getsize(path) > 500:
            return path
    return None

def _save_to_cache(img_bytes, title, media_type, ext=".jpg"):
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(title, media_type)
    path = os.path.join(CACHE_DIR, key + ext)
    with open(path, "wb") as f:
        f.write(img_bytes)
    return path

# ===================== IMAGE DOWNLOAD + VALIDATION =====================
def _download_image(url, min_bytes=500, min_px=50):
    """Download and validate an image. Returns (PIL.Image, bytes) or (None, None)."""
    headers_list = [HEADERS_BROWSER, HEADERS_IMG]
    for headers in headers_list:
        try:
            r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                continue
            if len(r.content) < min_bytes:
                continue
            ct = r.headers.get("Content-Type", "")
            if "html" in ct or "text" in ct:
                continue
            img = Image.open(BytesIO(r.content))
            if img.width < min_px or img.height < min_px:
                continue
            return img, r.content
        except Exception:
            continue
    return None, None

# ===================== DUCKDUCKGO HTML SEARCH FOR WOOK =====================
def _find_wook_detail_url(title, author=None):
    """Search DuckDuckGo HTML for the book's wook.pt detail page."""
    q = f"site:wook.pt {title}"
    if author:
        q += f" {author}"
        
    url = "https://html.duckduckgo.com/html/"
    try:
        r = requests.post(url, data={"q": q}, headers=HEADERS_BROWSER, timeout=10)
        # Search for uddg= redirects containing wook.pt/livro/
        redirects = re.findall(r'href="([^"]+uddg=[^"]+)"', r.text)
        for rl in redirects:
            match = re.search(r'uddg=([^&"]+)', rl)
            if match:
                decoded = unquote(match.group(1))
                if "wook.pt/livro/" in decoded:
                    return decoded
        
        # Fallback: regex direct links
        direct = re.findall(r'href="(https://www\.wook\.pt/livro/[^"]+)"', r.text)
        if direct:
            return direct[0]
    except Exception as e:
        print(f"      [DDG Error] {e}")
    return None

def _scrape_wook_og_image(detail_url):
    """Request the Wook book detail page and extract og:image."""
    try:
        r = requests.get(detail_url, headers=HEADERS_BROWSER, timeout=10)
        if r.status_code == 200:
            og = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', r.text)
            if og:
                img_url = og.group(1)
                # Filter generic/empty covers
                if "no-image" not in img_url and "dummy" not in img_url:
                    return img_url
    except Exception as e:
        print(f"      [Wook Scrape Error] {e}")
    return None

# ===================== ITUNES SEARCH API =====================
def _search_itunes_podcast(title, author=None):
    """Search iTunes for podcast cover art. Returns URL or None."""
    queries = []
    # Clean title to get name before ":" (e.g. "Linhas Vermelhas: O Futuro da Esquerda" -> "Linhas Vermelhas")
    short_title = title.split(":")[0].strip()
    
    if author:
        clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
        # Clean specific TV channels (e.g. "SIC Notícias" -> "SIC")
        clean_author_short = clean_author.replace(" Notícias", "").replace(" Portugal", "")
        queries.append(f"{short_title} {clean_author}")
        queries.append(f"{short_title} {clean_author_short}")
    
    queries.append(short_title)
    queries.append(title)
        
    for q in queries:
        url = f"https://itunes.apple.com/search?term={requests.utils.quote(q)}&entity=podcast&limit=5&country=PT"
        try:
            r = requests.get(url, headers=HEADERS_API, timeout=10)
            results = r.json().get("results", [])
            for res in results:
                # Extra validation: if author is specified, make sure it matches artistName in iTunes
                artist = (res.get("artistName") or "").lower()
                col_name = (res.get("collectionName") or "").lower()
                
                # Check if this result matches the search term and channel/author
                if author:
                    clean_auth_lower = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip().lower()
                    # e.g. "sic" in "sic notícias" or "daniel oliveira" in artist
                    if clean_auth_lower in artist or artist in clean_auth_lower or "sic" in artist:
                        art = res.get("artworkUrl600") or res.get("artworkUrl100", "")
                        if art:
                            return art.replace("100x100bb", "600x600bb")
                else:
                    # No author match required
                    art = res.get("artworkUrl600") or res.get("artworkUrl100", "")
                    if art:
                        return art.replace("100x100bb", "600x600bb")
        except Exception:
            pass
            
    # Fallback to the first item from any of the searches if strict match failed
    for q in queries:
        url = f"https://itunes.apple.com/search?term={requests.utils.quote(q)}&entity=podcast&limit=3&country=PT"
        try:
            r = requests.get(url, headers=HEADERS_API, timeout=10)
            results = r.json().get("results", [])
            if results:
                art = results[0].get("artworkUrl600") or results[0].get("artworkUrl100", "")
                if art:
                    return art.replace("100x100bb", "600x600bb")
        except Exception:
            pass
            
    return None

# ===================== OMDB API =====================
def _search_omdb(title, year=None):
    """Search OMDB for movie/series poster. Returns URL or None."""
    clean = title.strip()
    params = f"t={requests.utils.quote(clean)}&apikey=trilogy"
    if year:
        params += f"&y={year}"
    
    url = f"http://www.omdbapi.com/?{params}"
    try:
        r = requests.get(url, headers=HEADERS_API, timeout=10)
        data = r.json()
        if data.get("Response") == "True":
            poster = data.get("Poster")
            if poster and poster != "N/A":
                return poster
    except Exception:
        pass
    return None

# ===================== MAIN FETCH LOGIC =====================
def fetch_cover(title, media_type, author_or_meta=None, image_url_hint=None, category=None, allow_placeholder=False):
    """
    Fetch the best cover image for a recommendation.
    
    Returns: PIL.Image object or None (if allow_placeholder is False and no cover found)
    """
    # 1. Check cache first
    cached = _get_cached(title, media_type)
    if cached:
        print(f"    [CACHE] {title}")
        return Image.open(cached).convert("RGBA")
    
    # 1.5. If image_url_hint is provided, try resolving it first to ensure correct cover
    if image_url_hint:
        print(f"    [Hint Search] Trying hint URL for '{title}': {image_url_hint}")
        img, raw = _download_image(image_url_hint)
        if img:
            _save_to_cache(raw, title, media_type)
            print(f"    [OK - Hint URL] {title}")
            return img.convert("RGBA")
        else:
            print(f"      [Hint URL Failed] Falling back to search strategies...")
            
    cover_url = None
    source = ""
    
    # 2. Strategy for Books (New Scraping System via Wook/DDG)
    if media_type == "book":
        print(f"    [Scrape Search] Searching DDG for Wook link of '{title}'...")
        detail_url = _find_wook_detail_url(title, author_or_meta)
        if detail_url:
            print(f"      [Found Wook URL] {detail_url}")
            cover_url = _scrape_wook_og_image(detail_url)
            source = "Wook Scraper"
        
        # Fallback to hint url if scrape search failed
        if not cover_url and image_url_hint:
            cover_url = image_url_hint
            source = "Image Hint"
            
    # 3. Strategy for Podcasts
    elif media_type == "podcast":
        cover_url = _search_itunes_podcast(title, author_or_meta)
        source = "iTunes"
        
    # 4. Strategy for Movies / Series
    elif media_type == "movie":
        cover_url = _search_omdb(title)
        source = "OMDB"
        if not cover_url:
            alt_title = re.sub(r'^O\s+', 'The ', title)
            if alt_title != title:
                cover_url = _search_omdb(alt_title)
                source = "OMDB (en)"
        if not cover_url and image_url_hint:
            cover_url = image_url_hint
            source = "Image Hint"
            
    # 5. Strategy for Highlights / Docs
    elif media_type == "highlight":
        cat_lower = (category or author_or_meta or "").lower()
        if "document" in cat_lower:
            cover_url = _search_omdb(title)
            source = "OMDB (doc)"
        if not cover_url and image_url_hint:
            cover_url = image_url_hint
            source = "Image Hint"
            
    # 6. Try resolving the cover URL
    if cover_url:
        img, raw = _download_image(cover_url)
        if img:
            _save_to_cache(raw, title, media_type)
            print(f"    [OK - {source}] {title}")
            return img.convert("RGBA")
            
    # 7. Fallback to placeholder if explicitly allowed
    if allow_placeholder:
        print(f"    [PLACEHOLDER] {title}: no cover found")
        return generate_placeholder(title)
        
    print(f"    [FAIL] {title}: no cover found")
    return None


def generate_placeholder(title, width=180, height=240, bg_color=(220, 215, 205), text_color=(10, 49, 74)):
    """Generate a simple placeholder image with the title."""
    img = Image.new("RGBA", (width, height), bg_color + (255,))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width-1, height-1], outline=text_color, width=2)
    
    try:
        font_path = os.path.join(SCRIPT_DIR, "fonts", "Oswald-Regular.ttf")
        font = ImageFont.truetype(font_path, 16)
    except Exception:
        font = ImageFont.load_default()
    
    # Wrap title
    words = title.split()
    lines = []
    current = []
    for word in words:
        current.append(word)
        line = " ".join(current)
        bbox = font.getbbox(line)
        if (bbox[2] - bbox[0]) > width - 20:
            current.pop()
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    
    y_start = height // 2 - (len(lines) * 20) // 2
    for i, line in enumerate(lines[:5]):
        bbox = font.getbbox(line)
        lw = bbox[2] - bbox[0]
        x = (width - lw) // 2
        draw.text((x, y_start + i * 20), line, fill=text_color, font=font)
    
    return img



def fetch_cover_for_item(item, allow_placeholder=False):
    """Convenience wrapper: fetch cover for a recommendation dict."""
    return fetch_cover(
        title=item["title"],
        media_type=item["type"],
        author_or_meta=item.get("authorOrMeta"),
        image_url_hint=item.get("imageUrl"),
        category=item.get("category"),
        allow_placeholder=allow_placeholder
    )
