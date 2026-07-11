from PIL import Image
import os

uploaded_img = r"C:\Users\luisf\AppData\Local\Temp\gemini_antigravity\media__1783708049173.png"
artifacts_dir = r"C:\Users\luisf\.gemini\antigravity\brain\de256a41-03ed-4b38-959e-b204a8032963"
uploaded_img_alt = os.path.join(artifacts_dir, "media__1783708049173.png")

target_path = uploaded_img_alt if os.path.exists(uploaded_img_alt) else uploaded_img

if not os.path.exists(target_path):
    print(f"Error: Uploaded image not found at {target_path}")
    exit(1)

# Open the uploaded logo and convert to RGBA
img = Image.open(target_path).convert("RGBA")
width, height = img.size

# Convert white background to transparent
pixels = img.load()
for y in range(height):
    for x in range(width):
        r, g, b, a = pixels[x, y]
        if r > 245 and g > 245 and b > 245:
            pixels[x, y] = (0, 0, 0, 0)

# Crop the image
bbox = img.getbbox()
if bbox:
    cropped = img.crop(bbox)
    out_path = r"c:\Users\luisf\Documents\Politómetro\website\public\logo.png"
    cropped.save(out_path, "PNG")
    print(f"Successfully processed user's logo! Saved to {out_path}")
    print(f"Dimensions: {cropped.size}")
else:
    print("Error: Could not crop image")
