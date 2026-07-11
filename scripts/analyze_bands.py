from PIL import Image
import numpy as np

img = Image.open("right.png")
width, height = img.size

# Let's count active pixels in 20 vertical bands of the right part
band_height = height // 20
for i in range(20):
    y_start = i * band_height
    y_end = (i + 1) * band_height
    count = sum(1 for y in range(y_start, y_end) for x in range(width) if img.getpixel((x, y))[3] > 0)
    print(f"Band {i:02d} (Y={y_start:03d} to {y_end:03d}): {count} active pixels")
