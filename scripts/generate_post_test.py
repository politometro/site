"""
Politometro - Test Post Generator v8
Generates a test Instagram post image using the clean template.
Features:
- Aligns covers by the bottom of the image for the top row (podcast and book)
- Prevents cover overlap with title descenders (adds space if title has 2 lines)
- Perfect cover aspect ratios:
  * Podcast: 192x192 (square)
  * Book / Movie / Highlight: 160x220 (perfect vertical ratio, no stretching)
- Descriptions vertically centered next to the covers (Montserrat-SemiBold, size 15)
- Temporarily skips "Porque Sou Liberal" and "Linhas Vermelhas" for this test run.
"""
import os
import sys
import json
import datetime
from PIL import Image, ImageDraw, ImageFont
import requests

# Import cover fetcher
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cover_fetcher import fetch_cover_for_item, generate_placeholder

# --- PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "post_template.jpg")
REC_FILE = os.path.join(ROOT_DIR, "website", "public", "recommendations.json")
OUTPUT_PATH = os.path.join(ROOT_DIR, "website", "public", "current_post.png")

FONT_DIR = os.path.join(SCRIPT_DIR, "fonts")
FONT_BOLD = os.path.join(FONT_DIR, "Oswald-Bold.ttf")
FONT_REG = os.path.join(FONT_DIR, "Oswald-Regular.ttf")
FONT_DESC_BOLD = os.path.join(FONT_DIR, "Montserrat-SemiBold.ttf")

TEXT_COLOR = (10, 49, 74)

# --- FONT DOWNLOAD URLS ---
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

# --- QUADRANT CONFIGURATION ---
# Note: x coordinates and widths are adjusted to accommodate the new 160px wide book/movie covers.
QUADRANTS_CONFIG = {
    "podcast": {
        "label_pos": (50, 150),
        "title_pos": (50, 172),
        "cover_x": 50,
        "cover_w": 192,
        "cover_h": 192,
        "desc_x": 257,                 # 50 + 192 + 15px gap
        "desc_width": 143,             # 400 - 257
        "desc_max_lines": 11
    },
    "book": {
        "label_pos": (435, 150),
        "title_pos": (435, 172),
        "cover_x": 435,
        "cover_w": 160,                # 160px width
        "cover_h": 220,                # 220px height
        "desc_x": 610,                 # 435 + 160 + 15px gap
        "desc_width": 170,             # 780 - 610
        "desc_max_lines": 11
    },
    "movie": {
        "label_pos": (50, 525),
        "title_pos": (50, 547),
        "cover_x": 50,
        "cover_w": 160,
        "cover_h": 220,
        "desc_x": 225,                 # 50 + 160 + 15px gap
        "desc_width": 175,             # 400 - 225
        "desc_max_lines": 11
    },
    "highlight": {
        "label_pos": (435, 525),
        "title_pos": (435, 547),
        "cover_x": 435,
        "cover_w": 160,
        "cover_h": 220,
        "desc_x": 610,
        "desc_width": 170,
        "desc_max_lines": 11
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

# --- SELECTION & FALLBACK LOGIC ---
def get_recommendations_with_valid_covers(queue):
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
    
    active_items = []
    for i in queue:
        # Dynamically skip "Porque Sou Liberal" and "Linhas Vermelhas" for this test run
        if i["title"] in ["Porque Sou Liberal", "Linhas Vermelhas: O Futuro da Esquerda"]:
            continue
        if i.get("status") != "published" and score(i) >= 0:
            active_items.append(i)
            
    active_items.sort(key=lambda x: score(x), reverse=True)
    
    selected = {}
    covers = {}
    types_needed = ["book", "podcast", "movie", "highlight"]
    
    for t in types_needed:
        type_items = [i for i in active_items if i["type"] == t]
        found = False
        for item in type_items:
            print(f"Checking cover for [{t.upper()}] '{item['title']}'...")
            cover_img = fetch_cover_for_item(item, allow_placeholder=False)
            if cover_img:
                selected[t] = item
                covers[t] = cover_img
                print(f"  -> SUCCESS! Selected '{item['title']}'")
                found = True
                break
            else:
                print(f"  -> FAILED to find cover for '{item['title']}', skipping...")
        
        if not found and type_items:
            fallback_item = type_items[0]
            print(f"  -> WARNING: No real cover found for any '{t}' recommendations. Using placeholder.")
            selected[t] = fallback_item
            covers[t] = generate_placeholder(fallback_item['title'])
            
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

# --- MAIN ---
def generate_test_post():
    ensure_fonts()
    
    with open(REC_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    selected, covers = get_recommendations_with_valid_covers(data["queue"])
    
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
    
    # Pre-render text lines and compute title heights to do alignment calculations
    title_lines_map = {}
    title_bottoms = {}
    
    for qtype in ["podcast", "book", "movie", "highlight"]:
        item = selected[qtype]
        config = QUADRANTS_CONFIG[qtype]
        
        # Draw category label
        draw.text(config["label_pos"], item["category"], fill=TEXT_COLOR, font=label_font)
        
        # Wrap title
        tx, ty = config["title_pos"]
        lines = wrap_text(draw, item["title"], title_font, 350)
        title_lines_map[qtype] = lines
        
        # Draw title
        curr_y = ty
        for line in lines[:2]:
            draw.text((tx, curr_y), line, fill=TEXT_COLOR, font=title_font)
            curr_y += 34
        
        # Calculate Y value where title ends
        title_bottoms[qtype] = curr_y

    # --- ROW 1 (TOP) DYNAMIC ALIGNMENT ---
    # Top Row: Align by the BOTTOM of the cover images.
    # Podcast height is 192, Book height is 220.
    
    # Calculate bottom Y limit based on title heights + required space
    # (If a title has 2 lines, we add more spacing (+18 instead of +12) for the 'ç' descenders)
    gap_pod = 18 if len(title_lines_map["podcast"]) >= 2 else 12
    gap_book = 18 if len(title_lines_map["book"]) >= 2 else 12
    
    podcast_min_bottom = title_bottoms["podcast"] + gap_pod + QUADRANTS_CONFIG["podcast"]["cover_h"]
    book_min_bottom = title_bottoms["book"] + gap_book + QUADRANTS_CONFIG["book"]["cover_h"]
    
    # Common bottom alignment Y
    common_bottom_y = max(podcast_min_bottom, book_min_bottom)
    
    # Calculate starting Y coordinates for top row covers
    cover_y_map = {
        "podcast": common_bottom_y - QUADRANTS_CONFIG["podcast"]["cover_h"],
        "book": common_bottom_y - QUADRANTS_CONFIG["book"]["cover_h"]
    }
    
    # --- ROW 2 (BOTTOM) DYNAMIC ALIGNMENT ---
    # Bottom Row: Since movie and highlight both have 220px height, they naturally align by both top and bottom.
    gap_movie = 18 if len(title_lines_map["movie"]) >= 2 else 12
    gap_highlight = 18 if len(title_lines_map["highlight"]) >= 2 else 12
    
    movie_top_y = title_bottoms["movie"] + gap_movie
    highlight_top_y = title_bottoms["highlight"] + gap_highlight
    
    common_top_y = max(movie_top_y, highlight_top_y)
    
    cover_y_map["movie"] = common_top_y
    cover_y_map["highlight"] = common_top_y

    # --- PASTE COVERS AND WRITE DESCRIPTIONS ---
    for qtype in ["podcast", "book", "movie", "highlight"]:
        config = QUADRANTS_CONFIG[qtype]
        item = selected[qtype]
        cover = covers[qtype]
        
        cover_w = config["cover_w"]
        cover_h = config["cover_h"]
        cover_y = cover_y_map[qtype]
        cx = config["cover_x"]
        
        # Resize and apply rounded corners (radius 18px like sample)
        cover_resized = cover.resize((cover_w, cover_h), Image.Resampling.LANCZOS)
        cover_rounded = apply_rounded_corners(cover_resized, radius=18)
        
        cx = config["cover_x"]
        template.alpha_composite(cover_rounded, (cx, cover_y))
        
        # Wrap description
        dx = config["desc_x"]
        desc_lines = wrap_text(draw, item["description"], desc_font, config["desc_width"])
        
        spacing = 18
        text_block_h = len(desc_lines[:config["desc_max_lines"]]) * spacing
        
        # Vertically center the description relative to this cover's Y position and height
        dy = cover_y + (cover_h - text_block_h) // 2
        
        for line in desc_lines[:config["desc_max_lines"]]:
            draw.text((dx, dy), line, fill=TEXT_COLOR, font=desc_font)
            dy += spacing
            
    # Save
    output = template.convert("RGB")
    output.save(OUTPUT_PATH, "PNG", quality=95)
    print(f"\n[OK] Test post saved to: {OUTPUT_PATH}")

if __name__ == "__main__":
    generate_test_post()
