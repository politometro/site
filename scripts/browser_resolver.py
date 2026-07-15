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
    """Download image bytes from a URL. Returns bytes or None."""
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


# ===================== BOOK RESOLVER (Wook.pt) =====================
def resolve_book(page, title, author=None):
    """
    Search Wook.pt for a book by title/author.
    Returns dict: {"link": str, "cover_url": str} or None values.
    """
    result = {"link": None, "cover_url": None}
    query = title
    if author:
        clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
        query = f"{title} {clean_author}"
    
    search_url = f"https://www.wook.pt/pesquisa/{urllib.parse.quote(query)}"
    print(f"    [Playwright/Wook] Navigating to: {search_url}")
    
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)  # Wait for JS rendering
        
        # Look for first book result link
        # Wook search results have links to /livro/ pages
        book_links = page.query_selector_all('a[href*="/livro/"]')
        
        if book_links:
            href = book_links[0].get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = "https://www.wook.pt" + href
                result["link"] = href
                print(f"      [Found Wook Link] {href}")
                
                # Navigate to the book detail page to get the cover
                page.goto(href, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1500)
                
                # Try to find the book cover image
                # Wook uses og:image meta tag and also has image elements
                cover_url = _extract_og_image(page)
                
                if not cover_url:
                    # Try CSS selectors for Wook book images
                    img_selectors = [
                        'img.img-responsive[src*="images"]',
                        '.content-product img[src*="wook"]',
                        'img[src*="MX"]',  # Wook image pattern
                        '.product-image img',
                        'img[alt*="' + title.split()[0] + '"]',
                    ]
                    for sel in img_selectors:
                        try:
                            img_el = page.query_selector(sel)
                            if img_el:
                                src = img_el.get_attribute("src") or img_el.get_attribute("data-src")
                                if src and "no-image" not in src.lower():
                                    if src.startswith("//"):
                                        src = "https:" + src
                                    elif src.startswith("/"):
                                        src = "https://www.wook.pt" + src
                                    cover_url = src
                                    break
                        except Exception:
                            continue
                
                if cover_url:
                    result["cover_url"] = cover_url
                    print(f"      [Found Wook Cover] {cover_url}")
                else:
                    print(f"      [No Wook Cover Found]")
        else:
            print(f"      [No Wook Results] for '{query}'")
            
    except Exception as e:
        print(f"      [Playwright/Wook Error] {e}")
    
    return result


# ===================== MOVIE RESOLVER (IMDb via Google) =====================
def resolve_movie(page, title, author=None):
    """
    Search for a movie/documentary on Google targeting IMDb.
    Returns dict: {"link": str, "cover_url": str} or None values.
    """
    result = {"link": None, "cover_url": None}
    
    query = f"site:imdb.com {title}"
    if author:
        clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
        if clean_author.lower() not in ["imdb", "cinema"]:
            query += f" {clean_author}"
    
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=pt"
    print(f"    [Playwright/IMDb] Searching Google: {search_url}")
    
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
        
        # Find IMDb links in Google results
        all_links = page.query_selector_all('a[href*="imdb.com/title/"]')
        
        for link_el in all_links:
            href = link_el.get_attribute("href")
            if href and "imdb.com/title/" in href:
                # Clean Google redirect URL if needed
                if "/url?" in href:
                    match = re.search(r'url=([^&]+)', href)
                    if match:
                        href = urllib.parse.unquote(match.group(1))
                
                # Normalize IMDb URL
                imdb_match = re.search(r'(https?://(?:www\.)?imdb\.com/title/tt\d+)', href)
                if imdb_match:
                    result["link"] = imdb_match.group(1) + "/"
                    print(f"      [Found IMDb Link] {result['link']}")
                    break
        
        # Navigate to IMDb page to get poster
        if result["link"]:
            page.goto(result["link"], wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)
            
            cover_url = _extract_og_image(page)
            if cover_url:
                result["cover_url"] = cover_url
                print(f"      [Found IMDb Poster] {cover_url}")
            else:
                # Try poster-specific selectors
                poster_selectors = [
                    'img.ipc-image[src*="images-amazon"]',
                    '.ipc-poster img',
                    'img[alt*="Poster"]',
                    '.poster img',
                ]
                for sel in poster_selectors:
                    try:
                        img_el = page.query_selector(sel)
                        if img_el:
                            src = img_el.get_attribute("src")
                            if src and len(src) > 20:
                                result["cover_url"] = src
                                print(f"      [Found IMDb Poster via selector] {src}")
                                break
                    except Exception:
                        continue
        else:
            print(f"      [No IMDb Result] for '{title}'")
            
    except Exception as e:
        print(f"      [Playwright/IMDb Error] {e}")
    
    return result


# ===================== PODCAST RESOLVER (iTunes API + Playwright) =====================
def resolve_podcast(title, author=None):
    """
    Resolve podcast episode links using iTunes API.
    No Playwright needed — iTunes API works perfectly.
    Returns dict: {"link": str, "cover_url": str} or None values.
    """
    result = {"link": None, "cover_url": None}
    
    clean_author = ""
    if author:
        clean_author = re.sub(r'^(Filme|S[eé]rie|Document[aá]rio|Podcast)\s*/\s*', '', author).strip()
    
    short_title = title.split(":")[0].strip()
    
    # Try episode search first
    queries = [
        f"episodio {title} {clean_author}",
        f"{title} {clean_author}",
        f"{short_title} {clean_author}",
    ]
    
    headers = {"User-Agent": "Politometro/1.0", "Accept": "application/json"}
    
    # Search for episode
    for q in queries:
        try:
            url = f"https://itunes.apple.com/search?term={urllib.parse.quote(q)}&media=podcast&entity=podcastEpisode&limit=3&country=PT"
            r = requests.get(url, headers=headers, timeout=10)
            if r.ok:
                results = r.json().get("results", [])
                if results:
                    result["link"] = results[0].get("trackViewUrl", results[0].get("collectionViewUrl"))
                    art = results[0].get("artworkUrl600") or results[0].get("artworkUrl100", "")
                    if art:
                        result["cover_url"] = art.replace("100x100bb", "600x600bb")
                    if result["link"]:
                        print(f"    [iTunes Episode] Found: {result['link']}")
                        return result
        except Exception:
            pass
    
    # Fallback: search for the podcast show
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
                        result["cover_url"] = art.replace("100x100bb", "600x600bb")
                    if col_url:
                        result["link"] = col_url
                    if result["cover_url"]:
                        print(f"    [iTunes Show] Found cover: {result['cover_url']}")
                        return result
        except Exception:
            pass
    
    return result


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
    
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=pt"
    print(f"    [Playwright/Highlight] Searching Google: {search_url}")
    
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
        
        # Find the first non-google result link
        all_links = page.query_selector_all('a[href^="http"]')
        for link_el in all_links:
            href = link_el.get_attribute("href")
            if href and "google" not in href and "youtube.com" not in href and "wikipedia" not in href:
                # Clean Google redirect
                if "/url?" in href:
                    match = re.search(r'url=([^&]+)', href)
                    if match:
                        href = urllib.parse.unquote(match.group(1))
                
                if href.startswith("http") and "google" not in href:
                    result["link"] = href
                    print(f"      [Found Highlight Link] {href}")
                    break
        
        # Navigate to the page to extract og:image
        if result["link"]:
            try:
                page.goto(result["link"], wait_until="domcontentloaded", timeout=12000)
                page.wait_for_timeout(1500)
                
                cover_url = _extract_og_image(page)
                if cover_url:
                    result["cover_url"] = cover_url
                    print(f"      [Found Highlight Cover] {cover_url}")
                else:
                    print(f"      [No og:image on highlight page]")
            except Exception as e:
                print(f"      [Highlight page navigation error] {e}")
        else:
            print(f"      [No Highlight Results] for '{query}'")
            
    except Exception as e:
        print(f"      [Playwright/Highlight Error] {e}")
    
    return result


# ===================== HELPERS =====================
def _extract_og_image(page):
    """Extract og:image or twitter:image from a rendered page."""
    selectors = [
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="og:image:url"]',
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                content = el.get_attribute("content")
                if content and "no-image" not in content.lower() and "dummy" not in content.lower() and len(content) > 10:
                    if content.startswith("//"):
                        content = "https:" + content
                    return content
        except Exception:
            continue
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
                    res = resolve_podcast(title, author)
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
