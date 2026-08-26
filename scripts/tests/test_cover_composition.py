import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_post


class CoverCompositionTests(unittest.TestCase):
    def test_off_centre_informative_artwork_moves_the_crop_towards_it(self):
        source = Image.new("RGB", (1200, 600), (128, 128, 128))
        draw = ImageDraw.Draw(source)
        draw.rectangle((0, 0, 420, 599), fill=(225, 24, 35))
        for x in range(30, 390, 36):
            draw.line((x, 40, x, 560), fill=(255, 255, 255), width=5)

        crop_box, decision = generate_post._content_aware_crop_box(
            source, (300, 300)
        )
        rendered = generate_post.fit_cover_art(source, (300, 300))
        mean = ImageStat.Stat(rendered).mean

        self.assertLessEqual(crop_box[0], 50)
        self.assertFalse(decision["preserve_full_frame"])
        self.assertGreater(mean[0], 180)
        self.assertLess(mean[1], 95)

    def test_distributed_artwork_is_contained_instead_of_losing_either_side(self):
        source = Image.new("RGB", (1200, 600), (128, 128, 128))
        draw = ImageDraw.Draw(source)
        draw.rectangle((0, 0, 300, 599), fill=(220, 30, 35))
        draw.rectangle((900, 0, 1199, 599), fill=(30, 65, 220))

        _, decision = generate_post._content_aware_crop_box(source, (300, 300))
        rendered = generate_post.fit_cover_art(source, (300, 300))
        left = rendered.getpixel((35, 150))
        right = rendered.getpixel((265, 150))

        self.assertTrue(decision["preserve_full_frame"])
        self.assertGreater(left[0], left[2])
        self.assertGreater(right[2], right[0])


if __name__ == "__main__":
    unittest.main()
