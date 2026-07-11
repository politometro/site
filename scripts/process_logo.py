from PIL import Image
import os

img_path = r"c:\Users\luisf\Documents\Politómetro\Politómetro.png"
out_path = r"c:\Users\luisf\Documents\Politómetro\website\public\logo.png"

# Open image and convert to RGBA
img = Image.open(img_path).convert("RGBA")
width, height = img.size

# Target cream background color to replace with transparency
bg_r, bg_g, bg_b = 252, 234, 209
threshold = 28

pixels = img.load()
for y in range(height):
    for x in range(width):
        r, g, b, a = pixels[x, y]
        if abs(r - bg_r) < threshold and abs(g - bg_g) < threshold and abs(b - bg_b) < threshold:
            pixels[x, y] = (0, 0, 0, 0)

# Crop the Lupa (from Y=102 to Y=589)
lupa = img.crop((235, 102, 778, 590))

# Crop the Text (from Y=590 to Y=865)
text = img.crop((112, 590, 931, 866))

# Scale the text up by 1.25x to make it more prominent next to the lupa
scale_factor = 1.25
new_text_w = int(text.width * scale_factor)
new_text_h = int(text.height * scale_factor)
scaled_text = text.resize((new_text_w, new_text_h), Image.Resampling.LANCZOS)

# Create composite landscape logo (Lupa on left, Text on right)
gap = 40
composite_w = lupa.width + scaled_text.width + gap
composite_h = max(lupa.height, scaled_text.height)

composite = Image.new("RGBA", (composite_w, composite_h), (0, 0, 0, 0))

# Paste Lupa centered vertically
lupa_y = (composite_h - lupa.height) // 2
composite.paste(lupa, (0, lupa_y))

# Paste Text centered vertically next to Lupa
text_y = (composite_h - scaled_text.height) // 2
composite.paste(scaled_text, (lupa.width + gap, text_y))

# Save the landscape logo
composite.save(out_path, "PNG")

print("Success! Created a perfect transparent landscape logo.")
print(f"Lupa size: {lupa.size}, Text size (scaled): {scaled_text.size}, Composite size: {composite.size}")
