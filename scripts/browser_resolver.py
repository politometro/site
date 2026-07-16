"""
Politometro - Browser-based Link & Cover Resolver using Playwright
Navigates real websites (Wook.pt, FNAC, IMDb, etc.) with a headless browser
to resolve correct links and extract cover images for recommendations.

Falls back gracefully if Playwright is not installed.
"""
import os
import re
import json
import hashlib
import urllib.parse
import requests
from io import BytesIO

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(ROOT_DIR, "website", "public", "covers")

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[browser_resolver] Playwright not installed. Browser resolution disabled.")

# ===================== CACHE HELPERS =====================
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

def _save_image_to_cache(img_bytes, title, media_type, ext=".jpg"):
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(title, media_type)
    path = os.path.join(CACHE_DIR, key + ext)
    with open(path, "wb") as f:
        f.write(img_bytes)
    return path

def _download_image_bytes(url, min_bytes=500):
    """Download image bytes from a URL (supports base64 URLs as well). Returns bytes or None."""
    if url.startswith("data:image"):
        try:
            import base64
            if "," in url:
                header, encoded = url.split(",", 1)
                return base64.b64decode(encoded)
        except Exception as e:
            print(f"      [Base64 Decode Error] {e}")
            return None
            
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "image/*,*/*;q=0.8"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > min_bytes:
            ct = r.headers.get("Content-Type", "")
            if "html" not in ct and "text" not in ct:
                return r.content
    except Exception:
        pass
    return None


# ===================== PLAYWRIGHT BROWSER CONTEXT =====================
def _create_browser_context(playwright):
    """Create a stealth-ish browser context."""
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900},
        locale="pt-PT",
        timezone_id="Europe/Lisbon",
    )
    # Mask webdriver property
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
    """)
    return browser, context


# ===================== BOOK RESOLVER (Wook.pt / FNAC via Google) =====================
def resolve_book(page, title, author=None):
    """
    Search Google for the Wook.pt or FNAC detail page, navigate to it, and extract cover.
    Returns dict: {"link": str, "cover_url": str} or None values.
    """
    result = {"link": None, "cover_url": None}
    query = title
    if author:
        clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
        query = f"{title} {clean_author}"
    
    # 1. Search Google for the Wook.pt detail page
    print(f"    [Playwright/Wook] Searching Google for book link...")
    link = google_search(page, f"site:wook.pt livro {query}")
    if not link:
        link = google_search(page, f"wook.pt {query}")
        
    if link:
        result["link"] = link
        try:
            page.goto(link, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            cover = _extract_og_image(page)
            if cover:
                result["cover_url"] = cover
                print(f"      [Found Wook Cover from detail page] {cover}")
        except Exception as e:
            print(f"      [Wook detail page navigation error] {e}")
            
    # 2. Fallback to FNAC if Wook failed
    if not result["cover_url"]:
        print(f"    [Playwright/Wook] Fallback: Searching Google for FNAC link...")
        link = google_search(page, f"site:fnac.pt {query}")
        if link:
            result["link"] = link
            try:
                page.goto(link, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                cover = _extract_og_image(page)
                if cover:
                    result["cover_url"] = cover
                    print(f"      [Found FNAC Cover from detail page] {cover}")
            except Exception as e:
                print(f"      [FNAC detail page navigation error] {e}")
                
    return result


# ===================== MOVIE RESOLVER (IMDb via Google) =====================
def resolve_movie(page, title, author=None):
    """
    Search Google for the IMDb detail page, navigate to it, and extract poster.
    Returns dict: {"link": str, "cover_url": str} or None values.
    """
    result = {"link": None, "cover_url": None}
    query = title
    if author:
        clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
        query = f"{title} {clean_author}"
        
    # Search Google for the IMDb page
    print(f"    [Playwright/IMDb] Searching Google for movie link...")
    link = google_search(page, f"site:imdb.com {query}")
    if not link:
        link = google_search(page, f"imdb.com {title} movie")
        
    if link:
        result["link"] = link
        try:
            page.goto(link, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            cover = _extract_og_image(page)
            if cover:
                result["cover_url"] = cover
                print(f"      [Found IMDb Poster from detail page] {cover}")
        except Exception as e:
            print(f"      [IMDb detail page navigation error] {e}")
            
    return result

# ===================== GOOGLE SEARCH HELPER =====================
def google_search(page, query):
    """
    Search Google and return the first actual search result link.
    """
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=pt"
    print(f"    [Playwright/Google] Searching: {query}")
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        
        # Click cookie consent if present
        for btn_text in ["Aceitar tudo", "Aceito", "Agree", "Accept all", "Consinto"]:
            try:
                btn = page.query_selector(f'button:has-text("{btn_text}")')
                if btn:
                    btn.click()
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                pass
        
        # Look for result h3s inside anchors
        h3s = page.query_selector_all('a h3')
        for h3 in h3s:
            try:
                parent_a = page.evaluate_handle('(el) => el.closest("a")', h3)
                if parent_a:
                    href = page.evaluate('(el) => el.href', parent_a)
                    if href and href.startswith("http") and "google.com" not in href:
                        # Exclude YouTube search/consent urls if any
                        if "google.com" not in href and "youtube.com/results" not in href:
                            print(f"      [Google Search Match] Found link: {href}")
                            return href
            except Exception:
                continue
    except Exception as e:
        print(f"      [Google Search Page Error] {e}")
    return None


# ===================== PODCAST RESOLVER (iTunes API + Playwright) =====================
def resolve_podcast(page, title, author=None):
    """
    Resolve podcast episode links using Google Search first (for exact episode URLs),
    falling back to iTunes API.
    Returns dict: {"link": str, "cover_url": str} or None values.
    """
    result = {"link": None, "cover_url": None}
    
    clean_author = ""
    if author:
        clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
    
    # 1. Try Google Search to find the specific episode link
    search_query = title
    if clean_author:
        search_query = f'"{clean_author}" "{title}"'
    else:
        search_query = f'"{title}" podcast'
        
    print(f"    [Playwright/Podcast] Searching Google for episode...")
    episode_link = google_search(page, search_query)
    
    # If specific query failed, try a broader one
    if not episode_link and clean_author:
        search_query_broad = f'{clean_author} {title} podcast'
        episode_link = google_search(page, search_query_broad)
        
    if episode_link:
        result["link"] = episode_link
        # Try to navigate and get cover art
        try:
            page.goto(episode_link, wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(1500)
            cover = _extract_og_image(page)
            if cover:
                result["cover_url"] = cover
                print(f"      [Found Podcast Cover from Link] {cover}")
        except Exception as e:
            print(f"      [Podcast page navigation error] {e}")
            
    # 2. Fallback to iTunes API search (especially useful for general show cover art)
    itunes_res = {"link": None, "cover_url": None}
    short_title = title.split(":")[0].strip()
    queries = [
        f"episodio {title} {clean_author}",
        f"{title} {clean_author}",
        f"{short_title} {clean_author}",
    ]
    
    headers = {"User-Agent": "Politometro/1.0", "Accept": "application/json"}
    
    for q in queries:
        try:
            url = f"https://itunes.apple.com/search?term={urllib.parse.quote(q)}&media=podcast&entity=podcastEpisode&limit=3&country=PT"
            r = requests.get(url, headers=headers, timeout=10)
            if r.ok:
                results = r.json().get("results", [])
                if results:
                    itunes_res["link"] = results[0].get("trackViewUrl", results[0].get("collectionViewUrl"))
                    art = results[0].get("artworkUrl600") or results[0].get("artworkUrl100", "")
                    if art:
                        itunes_res["cover_url"] = art.replace("100x100bb", "600x600bb")
                    if itunes_res["link"]:
                        break
        except Exception:
            pass
            
    # If iTunes search failed, try show search
    if not itunes_res["link"] or not itunes_res["cover_url"]:
        for q in [f"{short_title} {clean_author}", short_title, title]:
            try:
                url = f"https://itunes.apple.com/search?term={urllib.parse.quote(q)}&entity=podcast&limit=5&country=PT"
                r = requests.get(url, headers=headers, timeout=10)
                if r.ok:
                    results = r.json().get("results", [])
                    for res in results:
                        art = res.get("artworkUrl600") or res.get("artworkUrl100", "")
                        col_url = res.get("collectionViewUrl")
                        if art:
                            itunes_res["cover_url"] = art.replace("100x100bb", "600x600bb")
                        if col_url:
                            itunes_res["link"] = col_url
                        if itunes_res["cover_url"]:
                            break
            except Exception:
                pass
                
    # Merge results (Google preferred for link, iTunes fallback for cover if needed)
    if not result["link"]:
        result["link"] = itunes_res.get("link")
    if not result["cover_url"]:
        result["cover_url"] = itunes_res.get("cover_url")
        
    return result


# ===================== GOOGLE IMAGES RESOLVER =====================
def resolve_google_images(page, query):
    """
    Search Google Images for the query and return the first image URL (http/https or base64).
    """
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&tbm=isch&hl=pt"
    print(f"    [Playwright/GoogleImages] Searching Images: {query}")
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=12000)
        page.wait_for_timeout(1500)
        
        # Click cookie consent if present
        for btn_text in ["Aceitar tudo", "Aceito", "Agree", "Accept all", "Consinto"]:
            try:
                btn = page.query_selector(f'button:has-text("{btn_text}")')
                if btn:
                    btn.click()
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                pass
                
        imgs = page.query_selector_all('img')
        # Try to find standard http/https URLs first (excluding Google logos/icons)
        for img in imgs:
            try:
                src = img.get_attribute("src")
                if src and src.startswith("http") and "google" not in src and len(src) > 30:
                    print(f"      [Google Images Match] Found image URL: {src[:80]}...")
                    return src
            except Exception:
                continue
        
        # Fallback to base64 data URLs if no http/https link was found
        for img in imgs:
            try:
                src = img.get_attribute("src")
                if src and src.startswith("data:image") and len(src) > 500:
                    print(f"      [Google Images Match] Found base64 thumbnail image")
                    return src
            except Exception:
                continue
    except Exception as e:
        print(f"      [Google Images Error] {e}")
    return None


# ===================== HIGHLIGHT RESOLVER (Google search) =====================
def resolve_highlight(page, title, author=None):
    """
    Search Google for articles/debates and extract og:image.
    Returns dict: {"link": str, "cover_url": str} or None values.
    """
    result = {"link": None, "cover_url": None}
    
    query = title
    if author:
        clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
        query += f" {clean_author}"
        
    # Use google_search helper to get the first actual article result
    link = google_search(page, query)
    if link:
        result["link"] = link
        # Navigate to the page to extract og:image
        try:
            page.goto(link, wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(1500)
            
            cover_url = _extract_og_image(page)
            if cover_url:
                result["cover_url"] = cover_url
                print(f"      [Found Highlight Cover] {cover_url[:80]}...")
            else:
                print(f"      [No og:image on highlight page] Trying Google Images fallback...")
                img_url = resolve_google_images(page, query)
                if img_url:
                    result["cover_url"] = img_url
        except Exception as e:
            print(f"      [Highlight page navigation error] {e}. Trying Google Images fallback...")
            img_url = resolve_google_images(page, query)
            if img_url:
                result["cover_url"] = img_url
    else:
        print(f"      [No Highlight Results] for '{query}' Trying Google Images directly...")
        img_url = resolve_google_images(page, query)
        if img_url:
            result["cover_url"] = img_url
        
    return result


# ===================== HELPERS =====================
def _extract_og_image(page):
    """Extract og:image or fallback to a large article image from a rendered page."""
    try:
        content = page.evaluate("""() => {
            const selectors = [
                'meta[property="og:image"]',
                'meta[name="twitter:image"]',
                'meta[property="og:image:url"]',
                'meta[itemprop="image"]'
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.getAttribute('content')) {
                    const val = el.getAttribute('content').trim();
                    if (val && !val.includes('no-image') && !val.includes('dummy')) {
                        return val;
                    }
                }
            }
            // Fallback: search for prominent article images
            const imgs = Array.from(document.querySelectorAll('article img, .main img, main img, img'));
            for (const img of imgs) {
                const src = img.src;
                if (src && src.startsWith('http') && !src.includes('logo') && !src.includes('avatar') && !src.includes('icon')) {
                    if (img.naturalWidth > 150 || img.width > 150 || !img.complete) {
                        return src;
                    }
                }
            }
            return null;
        }""")
        if content:
            if content.startswith("//"):
                content = "https:" + content
            return content
    except Exception as e:
        print(f"      [Error extracting og:image] {e}")
    return None


# ===================== MAIN: RESOLVE ALL =====================
def resolve_all(selected_items):
    """
    Resolve links and covers for all selected recommendation items.
    
    Args:
        selected_items: dict with keys "q1", "q2", "q3", "q4", values are item dicts
    
    Returns:
        dict mapping qkey -> {"link": str|None, "cover_url": str|None, "cover_bytes": bytes|None}
    """
    if not PLAYWRIGHT_AVAILABLE:
        print("[browser_resolver] Playwright not available. Skipping browser resolution.")
        return {}
    
    results = {}
    
    print("\n=== Browser-Based Link & Cover Resolution ===\n")
    
    with sync_playwright() as p:
        browser, context = _create_browser_context(p)
        page = context.new_page()
        
        try:
            for qkey in ["q1", "q2", "q3", "q4"]:
                item = selected_items.get(qkey)
                if not item:
                    continue
                
                title = item["title"]
                itype = item["type"]
                author = item.get("authorOrMeta", "")
                
                print(f"  [{qkey.upper()}] Resolving '{title}' (type: {itype})...")
                
                # Check if we already have a cached cover
                cached = _get_cached(title, itype)
                if cached:
                    print(f"    [CACHE HIT] Already have cover for '{title}'")
                    results[qkey] = {"link": item.get("link"), "cover_url": None, "cover_bytes": None}
                    continue
                
                # Resolve based on type
                if itype == "book":
                    res = resolve_book(page, title, author)
                elif itype in ["movie", "documentary", "series"]:
                    res = resolve_movie(page, title, author)
                elif itype == "podcast":
                    res = resolve_podcast(page, title, author)
                elif itype == "highlight":
                    res = resolve_highlight(page, title, author)
                else:
                    res = resolve_highlight(page, title, author)
                
                # Download cover image if we got a URL
                cover_bytes = None
                if res.get("cover_url"):
                    cover_bytes = _download_image_bytes(res["cover_url"])
                    if cover_bytes:
                        _save_image_to_cache(cover_bytes, title, itype)
                        print(f"    [COVER SAVED] Cached cover for '{title}'")
                    else:
                        print(f"    [COVER DOWNLOAD FAILED] Could not download: {res['cover_url']}")
                
                results[qkey] = {
                    "link": res.get("link"),
                    "cover_url": res.get("cover_url"),
                    "cover_bytes": cover_bytes,
                }
                
        finally:
            context.close()
            browser.close()
    
    print("\n=== Browser Resolution Complete ===\n")
    return results
