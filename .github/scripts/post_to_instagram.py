import os
import sys
import json
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

# Load environment variables
INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_ACCOUNT_ID = os.environ.get("INSTAGRAM_ACCOUNT_ID")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

queue_file = "website/public/recommendations.json"

if not os.path.exists(queue_file):
    print(f"Error: Queue file not found at {queue_file}")
    sys.exit(0)

# Check Lisbon Timezone for DST
lisbon_time = datetime.now(ZoneInfo("Europe/Lisbon"))
offset = lisbon_time.utcoffset().total_seconds()

print(f"Current Lisbon Time: {lisbon_time}")
print(f"Timezone offset from UTC: {offset} seconds")

# If offset is 0, it means it's Winter Time (WET, UTC+0)
# The cron runs at 09:00 UTC, which is 09:00 Lisbon time in winter.
# We need to wait 1 hour (3600s) to post at 10:00 Lisbon time!
if offset == 0:
    print("Winter Time detected (UTC+0). Sleeping for 3600 seconds to post at 10:00 Lisbon time...")
    time.sleep(3600)
    print("Waking up to publish post.")
else:
    print("Summer Time detected (UTC+1). Posting immediately.")

# Read queue and history
with open(queue_file, "r", encoding="utf-8") as f:
    data = json.load(f)

queue = data.get("queue", [])
history = data.get("history", [])

if not queue:
    print("Queue is empty, nothing to post.")
    sys.exit(0)

# Get next item
item = queue[0]
print(f"Processing post: {item['highlight']['title']}")

# 1. Generate image using Pillow
img_size = (1080, 1080)
img = Image.new("RGB", img_size, color="#070913")
draw = ImageDraw.Draw(img)

# Draw decorative grid
grid_color = "#131835"
for x in range(0, 1080, 135):
    draw.line([(x, 0), (x, 1080)], fill=grid_color, width=1)
for y in range(0, 1080, 135):
    draw.line([(0, y), (1080, y)], fill=grid_color, width=1)

# Draw division lines
div_color = "rgba(255, 255, 255, 30)" # grid lines
draw.line([(540, 50), (540, 950)], fill="#232b5c", width=3)
draw.line([(50, 500), (1030, 500)], fill="#232b5c", width=3)

# Load fonts (using system fonts in GitHub runner, e.g. Arial)
try:
    font_title = ImageFont.truetype("arial.ttf", 22)
    font_header = ImageFont.truetype("arial.ttf", 16)
    font_bold = ImageFont.truetype("arialbd.ttf", 24)
except IOError:
    font_title = ImageFont.load_default()
    font_header = ImageFont.load_default()
    font_bold = ImageFont.load_default()

def draw_card(x, y, w, h, category):
    # Rounded card outline
    draw.rectangle([x, y, x+w, y+h], fill="#0e1329", outline="#1c254d", width=1)
    # Header category text
    draw.text((x + 20, y + 20), category.upper(), fill="#06b6d4", font=font_header)

draw_card(70, 70, 430, 390, "LIVRO RECOMENDADO")
draw_card(580, 70, 430, 390, "PODCAST RECOMENDADO")
draw_card(70, 540, 430, 390, "FILME/SÉRIE RECOMENDADO")
draw_card(580, 540, 430, 390, "RECOMENDAÇÃO DA SEMANA")

# Draw footer
draw.rectangle([0, 990, 1080, 1080], fill="#04060f")
draw.line([(0, 990), (1080, 990)], fill="#1c254d", width=2)
draw.text((540, 1010), "🗳️ POLITÓMETRO", fill="#ffffff", font=font_bold, anchor="ms")
draw.text((540, 1045), "Programas Eleitorais | @politometro_pt", fill="#64748b", font=font_header, anchor="ms")

# Helper to download and draw image
def draw_cover_image(url, x, y, w, h):
    if url and url.startswith("http"):
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                with open("temp_cover.png", "wb") as f:
                    f.write(res.content)
                cover = Image.open("temp_cover.png")
                cover = cover.resize((w, h))
                img.paste(cover, (x, y))
                os.remove("temp_cover.png")
                return
        except Exception as e:
            print(f"Error downloading image {url}: {e}")
    
    # Fallback placeholder box
    draw.rectangle([x, y, x+w, y+h], fill="#1c254d")

# Draw the covers
draw_cover_image(item["book"]["imageUrl"], 215, 120, 120, 150)
draw_cover_image(item["podcast"]["imageUrl"], 725, 120, 130, 130)
draw_cover_image(item["movie"]["imageUrl"], 215, 590, 120, 150)
draw_cover_image(item["highlight"]["imageUrl"], 725, 590, 130, 130)

# Draw texts
# Book details
draw.text((90, 310), item["book"]["title"][:40], fill="#ffffff", font=font_title)
draw.text((90, 360), f"por {item['book']['author']}", fill="#94a3b8", font=font_header)

# Podcast details
draw.text((600, 310), item["podcast"]["episode"][:40], fill="#ffffff", font=font_title)
draw.text((600, 360), item["podcast"]["name"], fill="#94a3b8", font=font_header)

# Movie details
draw.text((90, 780), item["movie"]["title"][:40], fill="#ffffff", font=font_title)
draw.text((90, 830), item["movie"]["type"], fill="#94a3b8", font=font_header)

# Highlight details
draw.text((600, 750), item["highlight"]["title"][:40], fill="#ffffff", font=font_title)
# Simple word wrap for description
desc_words = item["highlight"]["description"].split(" ")
desc_lines = []
current_line = ""
for word in desc_words:
    if len(current_line + " " + word) < 38:
        current_line += " " + word
    else:
        desc_lines.append(current_line.strip())
        current_line = word
desc_lines.append(current_line.strip())

desc_y = 790
for line in desc_lines[:3]:
    draw.text((600, desc_y), line, fill="#64748b", font=font_header)
    desc_y += 22

# Save generated post image
post_image_path = "instagram_post_temp.png"
img.save(post_image_path)
print("Instagram template post image generated.")

# 2. Upload image to a public host (Freeimage.host) to get a public URL for Instagram API
public_image_url = None
try:
    print("Uploading post image to Freeimage.host...")
    with open(post_image_path, "rb") as f:
        # We use a demo API key or standard upload
        res = requests.post(
            "https://freeimage.host/api/1/upload",
            data={
                "key": "6d207e02198a847a40c485a794719577", # Public API Key
                "action": "upload"
            },
            files={"source": f},
            timeout=30
        )
    
    if res.status_code == 200:
        data = res.json()
        public_image_url = data["image"]["url"]
        print(f"Public image URL: {public_image_url}")
    else:
        print(f"Failed to upload image. Status: {res.status_code}, Response: {res.text}")
except Exception as e:
    print(f"Error uploading image to public host: {e}")

if not public_image_url:
    print("Could not get a public image URL. Aborting post.")
    os.remove(post_image_path)
    sys.exit(1)

# Caption text focused on politics
caption = f"""🗳️ RECOMENDAÇÃO SEMANAL POLITÓMETRO

Aqui ficam as nossas sugestões literárias, mediáticas e cinematográficas para este fim de semana, pensadas especialmente para quem se interessa pela política, história e sociedade portuguesa:

📚 Livro: "{item['book']['title']}" de {item['book']['author']}
🎙️ Podcast: {item['podcast']['name']} - "{item['podcast']['episode']}"
🎬 {item['movie']['type']}: "{item['movie']['title']}"

🌟 Recomendação da Semana: {item['highlight']['title']}
{item['highlight']['description']}

---
Acompanha mais análises e propostas dos partidos no nosso site Politómetro. Link na bio! 🗳️🇵🇹

#politica #portugal #politómetro #livros #podcasts #cinema #series #cultura"""

# 3. Publish to Instagram
if INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID:
    try:
        print("Publishing to Instagram...")
        # Step 1: Create media container
        container_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_ACCOUNT_ID}/media"
        container_res = requests.post(container_url, data={
            "image_url": public_image_url,
            "caption": caption,
            "access_token": INSTAGRAM_ACCESS_TOKEN
        })
        
        if container_res.status_code == 200:
            creation_id = container_res.json()["id"]
            print(f"Media container created with ID: {creation_id}")
            
            # Wait a few seconds for Instagram to process the image
            time.sleep(10)
            
            # Step 2: Publish media container
            publish_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_ACCOUNT_ID}/media_publish"
            publish_res = requests.post(publish_url, data={
                "creation_id": creation_id,
                "access_token": INSTAGRAM_ACCESS_TOKEN
            })
            
            if publish_res.status_code == 200:
                post_id = publish_res.json()["id"]
                print(f"Successfully published on Instagram! Post ID: {post_id}")
            else:
                print(f"Failed to publish media container: {publish_res.text}")
                sys.exit(1)
        else:
            print(f"Failed to create media container: {container_res.text}")
            sys.exit(1)
            
    except Exception as e:
        print(f"Error publishing to Instagram Graph API: {e}")
        sys.exit(1)
else:
    print("Instagram credentials missing. Skipping post upload (dry-run).")

# Cleanup temp image
os.remove(post_image_path)

# 4. Move posted item from queue to history and save
item_posted = queue.pop(0)
# Add posting date for the history record
item_posted["publishedAt"] = datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%Y-%m-%d")
history.insert(0, item_posted)

data["queue"] = queue
data["history"] = history

with open(queue_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Queue updated successfully. Posted item moved to history.")
