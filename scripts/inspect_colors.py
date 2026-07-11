from PIL import Image
import os
from collections import Counter

images = ["Politómetro.png", "Politómetro - Apresentação.png"]

for img_name in images:
    if os.path.exists(img_name):
        print(f"\n--- Colors for {img_name} ---")
        try:
            img = Image.open(img_name)
            img = img.convert("RGB")
            # Resize to get dominant colors faster
            img.thumbnail((100, 100))
            pixels = list(img.getdata())
            # Find the most common colors
            counter = Counter(pixels)
            most_common = counter.most_common(15)
            for idx, (color, count) in enumerate(most_common):
                hex_color = "#{:02x}{:02x}{:02x}".format(*color)
                print(f"  {idx+1}: {hex_color} ({count} pixels) - RGB: {color}")
        except Exception as e:
            print(f"Error opening image {img_name}: {e}")
    else:
        print(f"Image {img_name} not found.")
