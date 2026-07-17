"""
Compatibility wrapper around the strict atomic recommendation resolver.

No browser scraping or first-result Google selection remains here.  In
particular, a cover cache never suppresses link resolution: only a complete
``resolutionStatus=verified`` item with a matching identity manifest may be
reused.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from recommendation_resolver import (
    RecommendationResolutionError,
    ResolutionError,
    _cache_key,
    load_cover_for_item,
    resolve_recommendation,
    validate_cached_cover,
)


def _result_payload(resolved: Mapping[str, Any]) -> dict[str, Any]:
    image_url = str(resolved.get("imageUrl", ""))
    cover_bytes = None
    if image_url.startswith("/covers/") and validate_cached_cover(resolved):
        filename = image_url[len("/covers/") :]
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "website",
            "public",
            "covers",
            filename,
        )
        try:
            with open(path, "rb") as handle:
                cover_bytes = handle.read()
        except OSError:
            cover_bytes = None
    return {
        "link": resolved.get("link"),
        "cover_url": image_url or None,
        "cover_bytes": cover_bytes,
        "external_id": resolved.get("externalId"),
        "verification": resolved.get("verification"),
        "resolved_item": dict(resolved),
    }


def resolve_all(
    selected_items: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """
    Atomically resolve each selected quadrant.

    The original mutable item dictionaries are updated only after their whole
    entity succeeds.  Any unresolved quadrant raises a clear aggregate error so
    callers cannot silently compose a post with stale covers.
    """

    results: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for quadrant in ("q1", "q2", "q3", "q4"):
        item = selected_items.get(quadrant)
        if not item:
            continue
        try:
            resolved = resolve_recommendation(item)
        except RecommendationResolutionError as exc:
            failures[quadrant] = str(exc)
            continue
        if isinstance(item, dict):
            item.clear()
            item.update(resolved)
        results[quadrant] = _result_payload(resolved)

    if failures:
        detail = "; ".join(
            f"{quadrant.upper()}: {message}"
            for quadrant, message in failures.items()
        )
        raise RecommendationResolutionError(
            "BATCH_RESOLUTION_FAILED",
            f"Há quadrantes sem identidade/capa verificadas: {detail}",
            details={"failures": failures},
        )
    return results


def _compat_item(
    media_type: str, title: str, author: str | None = None
) -> dict[str, Any]:
    return {
        "type": media_type,
        "category": media_type,
        "title": title,
        "authorOrMeta": author or "",
        "description": "",
        "link": "",
        "imageUrl": "",
    }


def resolve_book(
    page: Any, title: str, author: str | None = None
) -> dict[str, Any]:
    del page
    return _result_payload(
        resolve_recommendation(_compat_item("book", title, author))
    )


def resolve_podcast(
    page: Any, title: str, author: str | None = None
) -> dict[str, Any]:
    del page
    return _result_payload(
        resolve_recommendation(_compat_item("podcast", title, author))
    )


def resolve_movie(
    page: Any, title: str, author: str | None = None
) -> dict[str, Any]:
    del page
    return _result_payload(
        resolve_recommendation(_compat_item("movie", title, author))
    )


def resolve_highlight(
    page: Any, title: str, author: str | None = None
) -> dict[str, Any]:
    del page
    return _result_payload(
        resolve_recommendation(_compat_item("highlight", title, author))
    )


__all__ = [
    "RecommendationResolutionError",
    "ResolutionError",
    "_cache_key",
    "load_cover_for_item",
    "resolve_all",
    "resolve_book",
    "resolve_highlight",
    "resolve_movie",
    "resolve_podcast",
    "resolve_recommendation",
    "validate_cached_cover",
]
