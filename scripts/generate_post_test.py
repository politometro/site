"""
Politometro - Test Post Generator v9
Generates a test Instagram post image using the clean template.
Features:
- Completely dynamic selection: The 3 general quadrants are chosen from the active queue
  by highest priority, ensuring NO repeating types (no two books, no two podcasts, etc.)
  and no overlap with the weekly highlight.
- Cover dimensions are tied to the item TYPE, not the quadrant:
  * Podcasts are always rendered at 192x192 (square)
  * Books, Movies, and Highlights are always rendered at 160x220 (vertical)
- Top row covers align perfectly by the bottom (using dynamic heights based on item types).
- Spacing checks for 2-line title descenders to prevent overlaps.
- Descriptions vertically centered next to the covers.
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

# --- DYNAMIC QUADRANT BASE X AND DESC CONFIGURATION ---
QUADRANTS_CONFIG = {
    "q1": { # Top Left
        "label_pos": (50, 150),
        "title_pos": (50, 172),
        "cover_x": 50,
        "desc_x_default": 257, # x position of desc when cover width is 192
        "desc_width_default": 143,
    },
    "q2": { # Top Right
        "label_pos": (435, 150),
        "title_pos": (435, 172),
        "cover_x": 435,
        "desc_x_default": 610, # x position of desc when cover width is 160
        "desc_width_default": 170,
    },
    "q3": { # Bottom Left
        "label_pos": (50, 525),
        "title_pos": (50, 547),
        "cover_x": 50,
        "desc_x_default": 225, # x position of desc when cover width is 160
        "desc_width_default": 175,
    },
    "q4": { # Bottom Right (Highlight / Recomendacao da semana)
        "label_pos": (435, 525),
        "title_pos": (435, 547),
        "cover_x": 435,
        "desc_x_default": 610,
        "desc_width_default": 170,
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
    
    # Filter active queue items
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
    
    # 1. First, select the "highlight" (Recomendacao da semana)
    highlight_candidates = [i for i in active_items if i["type"] == "highlight"]
    selected_highlight = None
    
    for item in highlight_candidates:
        print(f"Checking cover for [HIGHLIGHT] '{item['title']}'...")
        cover_img = fetch_cover_for_item(item, allow_placeholder=False)
        if cover_img:
            selected_highlight = item
            covers["q4"] = cover_img
            print(f"  -> SUCCESS! Selected highlight '{item['title']}'")
            break
        else:
            print(f"  -> FAILED to find cover for '{item['title']}', skipping...")
            
    if not selected_highlight and highlight_candidates:
        fallback_item = highlight_candidates[0]
        print(f"  -> WARNING: No real cover found for highlight candidates. Using placeholder.")
        selected_highlight = fallback_item
        covers["q4"] = generate_placeholder(fallback_item['title'])
        
    selected["q4"] = selected_highlight

    # 2. Select the other 3 positions dynamically from other types (no two books, no two podcasts, etc.)
    # We want 3 items with distinct types that are NOT "highlight".
    other_candidates = [i for i in active_items if i["type"] != "highlight"]
    
    selected_others = []
    seen_types = set()
    
    for item in other_candidates:
        # We need a cover first
        print(f"Checking cover for [{item['type'].upper()}] '{item['title']}'...")
        if item["type"] in seen_types:
            print(f"  -> Skipping '{item['title']}' because we already selected type '{item['type']}'")
            continue
            
        cover_img = fetch_cover_for_item(item, allow_placeholder=False)
        if cover_img:
            selected_others.append(item)
            covers[f"q{len(selected_others)}"] = cover_img
            seen_types.add(item["type"])
            print(f"  -> SUCCESS! Selected '{item['title']}' for position q{len(selected_others)}")
            if len(selected_others) == 3:
                break
        else:
            print(f"  -> FAILED to find cover for '{item['title']}', skipping...")
            
    # Fallback to placeholders if we couldn't find 3 items of distinct types with valid covers
    if len(selected_others) < 3:
        # Get all unique types available in candidates
        all_types = list(set(i["type"] for i in other_candidates))
        for t in all_types:
            if t not in seen_types and len(selected_others) < 3:
                type_items = [i for i in other_candidates if i["type"] == t]
                if type_items:
                    fallback_item = type_items[0]
                    print(f"  -> WARNING: Using placeholder for '{fallback_item['title']}' (type: {t})")
                    selected_others.append(fallback_item)
                    covers[f"q{len(selected_others)}"] = generate_placeholder(fallback_item['title'])
                    seen_types.add(t)

    # Assign to positions q1, q2, q3
    for idx, item in enumerate(selected_others):
        selected[f"q{idx+1}"] = item
        
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
    
    for qkey in ["q1", "q2", "q3", "q4"]:
        item = selected[qkey]
        config = QUADRANTS_CONFIG[qkey]
        
        # Draw category label
        draw.text(config["label_pos"], item["category"], fill=TEXT_COLOR, font=label_font)
        
        # Wrap title
        tx, ty = config["title_pos"]
        lines = wrap_text(draw, item["title"], title_font, 350)
        title_lines_map[qkey] = lines
        
        # Draw title
        curr_y = ty
        for line in lines[:2]:
            draw.text((tx, curr_y), line, fill=TEXT_COLOR, font=title_font)
            curr_y += 34
        
        # Calculate Y value where title ends
        title_bottoms[qkey] = curr_y

    # Determine Cover Dimensions based on item TYPE dynamically
    cover_dims = {}
    for qkey in ["q1", "q2", "q3", "q4"]:
        item = selected[qkey]
        if item["type"] == "podcast":
            cover_dims[qkey] = (192, 192) # Podcasts are always square
        else:
            cover_dims[qkey] = (160, 220) # Books, movies, highlights are vertical

    # --- ROW 1 (TOP) DYNAMIC ALIGNMENT ---
    # Top Row: Align by the BOTTOM of the cover images dynamically.
    gap_q1 = 18 if len(title_lines_map["q1"]) >= 2 else 12
    gap_q2 = 18 if len(title_lines_map["q2"]) >= 2 else 12
    
    h_q1 = cover_dims["q1"][1]
    h_q2 = cover_dims["q2"][1]
    
    q1_min_bottom = title_bottoms["q1"] + gap_q1 + h_q1
    q2_min_bottom = title_bottoms["q2"] + gap_q2 + h_q2
    
    # Common bottom alignment Y
    common_bottom_y = max(q1_min_bottom, q2_min_bottom)
    
    # Calculate starting Y coordinates for top row covers
    cover_y_map = {
        "q1": common_bottom_y - h_q1,
        "q2": common_bottom_y - h_q2
    }
    
    # --- ROW 2 (BOTTOM) DYNAMIC ALIGNMENT ---
    # Bottom Row: Align by the TOP of the cover images dynamically.
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
        
        # Resize and apply rounded corners
        cover_resized = cover.resize((cover_w, cover_h), Image.Resampling.LANCZOS)
        cover_rounded = apply_rounded_corners(cover_resized, radius=18)
        
        # Draw soft cover (rounded) border by compositing to template
        template.alpha_composite(cover_rounded, (cx, cover_y))
        
        # Calculate description positioning dynamically based on the actual cover width
        # gap = 15px
        dx = cx + cover_w + 15
        
        # Determine maximum description width based on quadrant layout limits
        # q1/q3 end at x=400. q2/q4 end at x=780.
        if qkey in ["q1", "q3"]:
            desc_w = 400 - dx
        else:
            desc_w = 780 - dx
            
        desc_lines = wrap_text(draw, item["description"], desc_font, desc_w)
        
        spacing = 18
        max_lines = 11
        text_block_h = len(desc_lines[:max_lines]) * spacing
        
        # Vertically center the description relative to this cover's Y position and height
        dy = cover_y + (cover_h - text_block_h) // 2
        
        for line in desc_lines[:max_lines]:
            draw.text((dx, dy), line, fill=TEXT_COLOR, font=desc_font)
            dy += spacing
            
    # Save
    output = template.convert("RGB")
    output.save(OUTPUT_PATH, "PNG", quality=95)
    print(f"\n[OK] Test post saved to: {OUTPUT_PATH}")

if __name__ == "__main__":
    generate_test_post()
