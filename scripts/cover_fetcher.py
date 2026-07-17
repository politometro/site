"""
Compatibility façade for the strict recommendation resolver.

Historically this module independently searched for a cover and trusted any
downloadable image.  That allowed stale/generic images to become authoritative
and, worse, made the link resolver skip its work.  All discovery now goes
through ``recommendation_resolver.resolve_recommendation`` so link, identity
and cover are verified together.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

from recommendation_resolver import (
    CACHE_DIR,
    RecommendationResolutionError,
    ResolutionError,
    _cache_key,
    load_cover_for_item,
    resolve_recommendation,
    validate_cached_cover,
)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_cover(
    title: str,
    media_type: str,
    author_or_meta: str | None = None,
    image_url_hint: str | None = None,
    category: str | None = None,
    allow_placeholder: bool = False,
    link: str | None = None,
) -> Image.Image | None:
    """
    Resolve a recommendation and return its verified raster cover.

    ``image_url_hint`` is retained for call compatibility but is never trusted
    as an independent source.  Remote LLM/Unsplash hints and unmanifested local
    files therefore cannot bypass entity verification.  ``allow_placeholder``
    is also retained for compatibility; placeholders are deliberately not
    returned because they are not publishable verified covers.
    """

    item: dict[str, Any] = {
        "id": "compat:" + _cache_key(title, media_type),
        "type": media_type,
        "category": category or media_type,
        "title": title,
        "authorOrMeta": author_or_meta or "",
        "description": "",
        "imageUrl": image_url_hint or "",
        "link": link or "",
    }
    try:
        resolved = resolve_recommendation(item)
        return load_cover_for_item(resolved)
    except RecommendationResolutionError as exc:
        suffix = (
            " Placeholders estão desativados."
            if allow_placeholder
            else ""
        )
        print(f"    [RESOLUTION FAILED] {title}: {exc}.{suffix}")
        return None


def fetch_cover_for_item(
    item: Mapping[str, Any], allow_placeholder: bool = False
) -> Image.Image | None:
    """
    Resolve an item atomically, update mutable dictionaries, and load its cover.

    A valid verified cache can be loaded without network.  An old cache file
    without an identity manifest is ignored and cannot stop link resolution.
    """

    cached = load_cover_for_item(item)
    if cached is not None:
        return cached
    try:
        resolved = resolve_recommendation(item)
    except RecommendationResolutionError as exc:
        suffix = (
            " Placeholders estão desativados."
            if allow_placeholder
            else ""
        )
        print(
            f"    [RESOLUTION FAILED] {item.get('title', '<sem título>')}: "
            f"{exc}.{suffix}"
        )
        return None
    if isinstance(item, dict):
        item.clear()
        item.update(resolved)
    return load_cover_for_item(resolved)


def generate_placeholder(
    title: str,
    width: int = 180,
    height: int = 240,
    bg_color: tuple[int, int, int] = (220, 215, 205),
    text_color: tuple[int, int, int] = (10, 49, 74),
) -> Image.Image:
    """
    Create an in-memory visual placeholder for non-public diagnostic tooling.

    The strict resolver never calls, caches or marks this image as verified.
    Production selection must reject a recommendation when no real cover is
    available.
    """

    image = Image.new("RGBA", (width, height), bg_color + (255,))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [0, 0, width - 1, height - 1], outline=text_color, width=2
    )
    try:
        font = ImageFont.truetype(
            os.path.join(SCRIPT_DIR, "fonts", "Oswald-Regular.ttf"), 16
        )
    except (OSError, ValueError):
        font = ImageFont.load_default()

    words = title.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        bbox = font.getbbox(candidate)
        if current and bbox[2] - bbox[0] > width - 20:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))

    start_y = height // 2 - (len(lines[:5]) * 20) // 2
    for index, line in enumerate(lines[:5]):
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        draw.text(
            ((width - line_width) // 2, start_y + index * 20),
            line,
            fill=text_color,
            font=font,
        )
    return image


__all__ = [
    "CACHE_DIR",
    "RecommendationResolutionError",
    "ResolutionError",
    "_cache_key",
    "fetch_cover",
    "fetch_cover_for_item",
    "generate_placeholder",
    "load_cover_for_item",
    "resolve_recommendation",
    "validate_cached_cover",
]
