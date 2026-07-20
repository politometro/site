"""
Strict, atomic recommendation resolver for Politometro.

The resolver treats a recommendation as one indivisible entity: the canonical
link, external identifier, source metadata and cover must all describe the same
book, episode, film or article.  A result is only returned after the cover has
been downloaded, decoded, normalised to JPEG and bound to that identity through
an adjacent cache manifest.

This module intentionally fails closed.  It never treats placeholders,
Unsplash images, SVG, HTML or an unmanifested legacy cache file as a verified
cover.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import email.utils
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Iterable, Mapping, Sequence

import requests
from PIL import Image, ImageOps, UnidentifiedImageError


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(ROOT_DIR, "website", "public", "covers")

CACHE_VERSION = 2
HTTP_TIMEOUT = (5, 18)
MAX_REDIRECTS = 5
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_FEED_BYTES = 5 * 1024 * 1024
MIN_IMAGE_WIDTH = 160
MIN_IMAGE_HEIGHT = 120
MAX_IMAGE_PIXELS = 30_000_000
MAX_OUTPUT_DIMENSION = 2400
VERIFICATION_TTL_HOURS = 24
MIN_REVIEW_VALIDITY_HOURS = 24
HTTP_ATTEMPTS = 3
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}

USER_AGENT = (
    "PolitometroResolver/2.0 "
    "(https://politometro.politiza-te.pt; recommendation verification)"
)

RASTER_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
RASTER_PIL_FORMATS = {"JPEG", "PNG", "WEBP"}
HTML_MIME_TYPES = {"text/html", "application/xhtml+xml"}
JSON_MIME_TYPES = {
    "application/json",
    "application/ld+json",
    "text/json",
    "text/javascript",
    "application/javascript",
    "text/plain",
}
XML_MIME_TYPES = {
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
}

BLOCKED_IMAGE_HOSTS = {
    "unsplash.com",
    "images.unsplash.com",
    "source.unsplash.com",
    "placeholder.com",
    "via.placeholder.com",
    "placehold.co",
    "placehold.it",
    "dummyimage.com",
    "picsum.photos",
    "loremflickr.com",
}
BLOCKED_IMAGE_URL_TOKENS = {
    "placeholder",
    "placehold.",
    "no-image",
    "no_image",
    "noimage",
    "dummy-image",
    "dummyimage",
    "default-cover",
    "default_cover",
    "default-image",
    "default_image",
    "spacer.gif",
    "transparent.gif",
}
BLOCKED_LINK_HOSTS = {
    "wikipedia.org",
    "wikimedia.org",
    "google.com",
    "google.pt",
    "bing.com",
    "duckduckgo.com",
}

# This is the exact generic business-photo asset already found under several
# unrelated titles in the repository.  Blocking it also makes old bad caches
# self-heal instead of silently being migrated.
BLOCKED_RAW_IMAGE_HASHES = {
    "51c69bef3b31d7416a98a0e7773f0917b86064d54e3ecbe44777696f7d144e58",
}

TRUSTED_HIGHLIGHT_DOMAINS = {
    "rtp.pt",
    "publico.pt",
    "expresso.pt",
    "observador.pt",
    "sicnoticias.pt",
    "cnnportugal.iol.pt",
    "tvi.iol.pt",
    "dn.pt",
    "jn.pt",
    "jornaldenegocios.pt",
    "eco.sapo.pt",
    "rr.pt",
    "tsf.pt",
    "visao.pt",
    "sabado.pt",
    "poligrafo.sapo.pt",
    "youtube.com",
    "youtu.be",
}

BOOK_SEARCH_DOMAINS = (
    "wook.pt",
    "bertrand.pt",
    "fnac.pt",
    "almedina.net",
    "leyaonline.com",
)
TRUSTED_BOOK_DOMAINS = {
    *BOOK_SEARCH_DOMAINS,
    "openlibrary.org",
    "books.google.com",
}

TRUSTED_PODCAST_DOMAINS = {
    "podcasts.apple.com",
    "open.spotify.com",
    "podcasters.spotify.com",
    "perguntarnaoofende.pt",
    "sicnoticias.pt",
    "tvi.iol.pt",
    "observador.pt",
    "expresso.pt",
    "rtp.pt",
}

SOURCE_DOMAIN_HINTS = {
    "rtp": {"rtp.pt"},
    "publico": {"publico.pt"},
    "expresso": {"expresso.pt"},
    "observador": {"observador.pt"},
    "sic": {"sicnoticias.pt"},
    "sic noticias": {"sicnoticias.pt"},
    "cnn portugal": {"cnnportugal.iol.pt", "tvi.iol.pt"},
    "tvi": {"tvi.iol.pt"},
    "diario de noticias": {"dn.pt"},
    "dn": {"dn.pt"},
    "jornal de noticias": {"jn.pt"},
    "jn": {"jn.pt"},
    "jornal de negocios": {"jornaldenegocios.pt"},
    "eco": {"eco.sapo.pt"},
    "renascenca": {"rr.pt"},
    "tsf": {"tsf.pt"},
    "visao": {"visao.pt"},
    "sabado": {"sabado.pt"},
    "poligrafo": {"poligrafo.sapo.pt"},
    "perguntar nao ofende": {"perguntarnaoofende.pt"},
}

TYPE_ALIASES = {
    "book": "book",
    "podcast": "podcast",
    "movie": "movie",
    "film": "movie",
    "series": "movie",
    "documentary": "movie",
    "highlight": "highlight",
    "article": "highlight",
    # Stable, episode-level editorial video. These reuse the strict
    # first-party/YouTube resolver used by highlights, but deliberately do not
    # inherit the short news expiry contract.
    "nostalgia": "highlight",
    "investigation": "highlight",
}


class RecommendationResolutionError(RuntimeError):
    """A fail-closed recommendation resolution error with a stable code."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        item: Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if message is None:
            message = code
            code = "RESOLUTION_FAILED"
        self.code = code
        self.item_id = (item or {}).get("id")
        self.title = (item or {}).get("title")
        self.details = dict(details or {})
        suffix = f" [{self.title}]" if self.title else ""
        super().__init__(f"{code}{suffix}: {message}")


# Short name used by the integration code.
ResolutionError = RecommendationResolutionError


@dataclass(frozen=True)
class EntityResolution:
    link: str
    image_url: str
    external_id: str
    source: str
    score: float
    resolved_title: str
    resolved_author: str = ""
    description: str = ""
    isbn: str = ""
    published_at: str = ""


@dataclass(frozen=True)
class NormalizedCover:
    data: bytes
    sha256: str
    width: int
    height: int
    source_url: str
    source_mime: str


def _normalise_datetime(value: Any, *, allow_future: bool = False) -> str:
    """Return an ISO-8601 UTC timestamp or an empty string."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed: _dt.datetime | None = None
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    parsed = parsed.astimezone(_dt.timezone.utc)
    if (
        not allow_future
        and parsed > _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=1)
    ):
        return ""
    return parsed.isoformat().replace("+00:00", "Z")


def _podcast_expiry_days(item: Mapping[str, Any]) -> int:
    discovery = item.get("_discovery")
    discovery_frequency = (
        str(discovery.get("frequency", ""))
        if isinstance(discovery, Mapping)
        else ""
    )
    cadence_text = _normalise_text(
        " ".join(
            [
                str(item.get("cadence", "")),
                str(item.get("frequency", "")),
                discovery_frequency,
            ]
        )
    )
    if any(token in cadence_text for token in ("daily", "diario", "diaria")):
        return 3
    if any(token in cadence_text for token in ("weekly", "semanal")):
        return 10
    if any(token in cadence_text for token in ("monthly", "mensal")):
        return 35
    return 21


def _expiry_for(
    media_type: str, published_at: str, item: Mapping[str, Any]
) -> str | None:
    """Preserve a valid existing expiry or derive a short source-based one."""

    existing = _normalise_datetime(
        item.get("expiryDate"), allow_future=True
    )
    if existing:
        return existing
    published = _normalise_datetime(published_at)
    if not published:
        return None
    parsed = _dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
    if media_type == "podcast":
        days = _podcast_expiry_days(item)
    elif media_type == "highlight":
        days = int(os.environ.get("HIGHLIGHT_EXPIRY_DAYS", "60"))
    else:
        return None
    days = max(1, min(days, 60 if media_type == "highlight" else 35))
    return (parsed + _dt.timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _cache_key(title: str, media_type: str, identity: str | None = None) -> str:
    """
    Return a cache key.

    With two arguments this is byte-for-byte compatible with the legacy key.
    New verified covers pass an identity and receive a suffix derived from the
    canonical external ID/link, preventing homonymous works from overwriting
    each other.
    """

    raw = f"{media_type}_{title}".lower()
    safe = re.sub(r"[^a-z0-9]", "_", raw)[:60]
    title_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    legacy = f"{safe}_{title_hash}"
    if not identity:
        return legacy
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{legacy}_{identity_hash}"


def _normalise_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


HIGHLIGHT_EDITORIAL_PATH_MARKERS = {
    "opiniao",
    "editorial",
    "cronica",
    "analise",
    "investigacao",
    "grande reportagem",
    "reportagem especial",
    "entrevista",
    "explicador",
    "fact check",
    "documentario",
    "ensaio",
    "debate",
}
HIGHLIGHT_EDITORIAL_TITLE_MARKERS = {
    "opiniao",
    "editorial",
    "analise",
    "investigacao",
    "grande reportagem",
    "reportagem especial",
    "entrevista",
    "explicador",
    "fact check",
    "documentario",
    "ensaio",
    "debate",
}
HIGHLIGHT_EDITORIAL_DESCRIPTION_MARKERS = {
    "artigo de opiniao",
    "texto de opiniao",
    "jornalismo de investigacao",
    "grande reportagem",
    "reportagem especial",
    "entrevista completa",
    "analise aprofundada",
}


def is_eligible_highlight(
    *,
    title: Any,
    description: Any,
    link: Any,
    categories: Iterable[Any] | None = None,
) -> bool:
    """Accept editorial/long-form work and fail closed on ordinary news."""

    raw_link = str(link or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw_link)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False

    path_segments = {
        _normalise_text(urllib.parse.unquote(segment))
        for segment in parsed.path.split("/")
        if _normalise_text(urllib.parse.unquote(segment))
    }
    query_segments = {
        _normalise_text(value)
        for key, values in urllib.parse.parse_qs(parsed.query).items()
        for value in (key, *values)
        if _normalise_text(value)
    }
    category_segments = {
        _normalise_text(value)
        for value in (categories or [])
        if _normalise_text(value)
    }
    title_label = unicodedata.normalize("NFKD", str(title or ""))
    title_label = "".join(
        ch for ch in title_label if not unicodedata.combining(ch)
    ).casefold().strip()
    description_evidence = _normalise_text(description)

    if any(
        marker in path_segments
        or marker in query_segments
        or marker in category_segments
        for marker in HIGHLIGHT_EDITORIAL_PATH_MARKERS
    ):
        return True
    title_pattern = "|".join(
        re.escape(marker)
        for marker in sorted(
            HIGHLIGHT_EDITORIAL_TITLE_MARKERS, key=len, reverse=True
        )
    )
    if re.match(
        rf"^(?:{title_pattern})\s*(?::|\||-|–|—)",
        title_label,
    ):
        return True
    return any(
        marker in description_evidence
        for marker in HIGHLIGHT_EDITORIAL_DESCRIPTION_MARKERS
    )


def _text_tokens(value: Any) -> list[str]:
    return [token for token in _normalise_text(value).split() if len(token) > 1]


def _title_metrics(expected: str, actual: str) -> tuple[float, float, float]:
    left = _normalise_text(expected)
    right = _normalise_text(actual)
    if not left or not right:
        return 0.0, 0.0, 0.0
    sequence = difflib.SequenceMatcher(None, left, right).ratio()
    left_tokens = set(_text_tokens(left))
    right_tokens = set(_text_tokens(right))
    if not left_tokens or not right_tokens:
        token_f1 = 0.0
    else:
        common = len(left_tokens & right_tokens)
        precision = common / len(right_tokens)
        recall = common / len(left_tokens)
        token_f1 = (
            (2 * precision * recall / (precision + recall))
            if precision + recall
            else 0.0
        )
    combined = (sequence * 0.72) + (token_f1 * 0.28)
    return combined, sequence, token_f1


def _author_score(expected: str, candidates: Iterable[str]) -> float:
    expected_norm = _normalise_text(expected)
    if not expected_norm:
        return 1.0
    expected_tokens = set(_text_tokens(expected_norm))
    scores: list[float] = []
    for candidate in candidates:
        candidate_norm = _normalise_text(candidate)
        if not candidate_norm:
            continue
        candidate_tokens = set(_text_tokens(candidate_norm))
        coverage = (
            len(expected_tokens & candidate_tokens) / len(expected_tokens)
            if expected_tokens
            else 0.0
        )
        sequence = difflib.SequenceMatcher(
            None, expected_norm, candidate_norm
        ).ratio()
        scores.append(max(sequence, coverage))
    return max(scores, default=0.0)


def _name_identity_matches(expected: str, actual: str) -> bool:
    """Require evidence for the whole show/creator name, not one shared word."""

    expected_norm = _normalise_text(expected)
    actual_norm = _normalise_text(actual)
    if not expected_norm or not actual_norm:
        return False
    if (
        expected_norm == actual_norm
        or expected_norm in actual_norm
        or actual_norm in expected_norm
    ):
        return True
    connector_words = {"a", "as", "da", "das", "de", "do", "dos", "e", "the"}
    expected_tokens = {
        token
        for token in _text_tokens(expected_norm)
        if token not in connector_words
    }
    actual_tokens = {
        token
        for token in _text_tokens(actual_norm)
        if token not in connector_words
    }
    if not expected_tokens or not actual_tokens:
        return False
    coverage = len(expected_tokens & actual_tokens) / len(expected_tokens)
    sequence = difflib.SequenceMatcher(
        None, expected_norm, actual_norm
    ).ratio()
    return coverage >= 0.8 and sequence >= 0.62


def _strip_media_prefix(value: str) -> str:
    return re.sub(
        r"^(filme|serie|série|documentario|documentário|podcast)\s*/\s*",
        "",
        value or "",
        flags=re.IGNORECASE,
    ).strip()


def _clean_page_title(value: str) -> str:
    title = html.unescape(str(value or "")).strip()
    title = re.sub(
        r"\s*[-|]\s*(IMDb|Wook|Fnac|Bertrand|Apple Podcasts).*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return re.sub(r"\s+", " ", title).strip()


def _clean_description(value: str, max_chars: int = 360) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars + 1]
    sentence_end = max(shortened.rfind(". "), shortened.rfind("! "), shortened.rfind("? "))
    if sentence_end >= max_chars // 2:
        return shortened[: sentence_end + 1].strip()
    return shortened[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"

def _has_content_description(
    description: str,
    media_type: str,
    title: str = "",
    author: str = "",
) -> bool:
    text = _normalise_text(description)
    if len(text) < 35:
        return False
    words = text.split()
    if len(words) < 5:
        return False

    title_text = _normalise_text(title)
    author_text = _normalise_text(author)
    generic_patterns = (
        r"^livro .+ de .+$",
        r"^livro .+ confirmado em catalogo bibliografico$",
        r"^filme .+ realizado por .+$",
        r"^filme .+ confirmado por wikidata e imdb$",
    )
    if media_type in {"book", "movie"} and any(
        re.match(pattern, text) for pattern in generic_patterns
    ):
        return False

    if title_text and author_text:
        contentless = text.replace(title_text, "").replace(author_text, "")
        contentless = re.sub(
            r"\b(livro|filme|de|por|realizado|realizada|autor|autora)\b",
            " ",
            contentless,
        )
        if len(contentless.split()) < 4:
            return False
    return True


def _host_matches(host: str, domain: str) -> bool:
    host = host.casefold().rstrip(".")
    domain = domain.casefold().rstrip(".")
    return host == domain or host.endswith("." + domain)


def _hostname(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").casefold().rstrip(".")


def _host_in(host: str, domains: Iterable[str]) -> bool:
    return any(_host_matches(host, domain) for domain in domains)


def _canonicalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    query = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.casefold()
        if key_lower.startswith("utm_") or key_lower in {
            "fbclid",
            "gclid",
            "srsltid",
            "ref",
            "ref_",
        }:
            continue
        query.append((key, value))
    host = (parsed.hostname or "").casefold()
    port = parsed.port
    netloc = host
    if port and not (
        (parsed.scheme.casefold() == "https" and port == 443)
        or (parsed.scheme.casefold() == "http" and port == 80)
    ):
        netloc = f"{host}:{port}"
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            parsed.path or "/",
            urllib.parse.urlencode(query, doseq=True),
            "",
        )
    )


def _assert_safe_url(
    url: str,
    *,
    purpose: str = "link",
    allowed_domains: Iterable[str] | None = None,
) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise RecommendationResolutionError(
            "UNSAFE_URL", f"URL inválido: {url!r}"
        ) from exc
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise RecommendationResolutionError(
            "UNSAFE_URL", "Apenas URLs HTTP(S) são permitidos."
        )
    if not parsed.hostname or parsed.username or parsed.password:
        raise RecommendationResolutionError(
            "UNSAFE_URL", "Host em falta ou credenciais embutidas no URL."
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise RecommendationResolutionError(
            "UNSAFE_URL", "Porta inválida no URL."
        ) from exc
    if port not in {None, 80, 443}:
        raise RecommendationResolutionError(
            "UNSAFE_URL", f"Porta não permitida: {port}."
        )

    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise RecommendationResolutionError(
            "UNSAFE_URL", f"Host local bloqueado: {host}."
        )
    if allowed_domains and not _host_in(host, allowed_domains):
        raise RecommendationResolutionError(
            "DOMAIN_NOT_ALLOWED", f"Domínio não permitido: {host}."
        )
    if purpose == "image" and _host_in(host, BLOCKED_IMAGE_HOSTS):
        raise RecommendationResolutionError(
            "PLACEHOLDER_IMAGE", f"Fornecedor de imagem genérica bloqueado: {host}."
        )
    if purpose == "image":
        lowered = url.casefold()
        if any(token in lowered for token in BLOCKED_IMAGE_URL_TOKENS):
            raise RecommendationResolutionError(
                "PLACEHOLDER_IMAGE", "URL de placeholder/no-image bloqueado."
            )

    try:
        literal_ip = ipaddress.ip_address(host.strip("[]"))
        addresses = [literal_ip]
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise RecommendationResolutionError(
                "DNS_FAILED", f"Não foi possível resolver {host}."
            ) from exc
        addresses = []
        for record in records:
            try:
                addresses.append(ipaddress.ip_address(record[4][0]))
            except ValueError:
                continue
        if not addresses:
            raise RecommendationResolutionError(
                "DNS_FAILED", f"Nenhum endereço válido para {host}."
            )

    for address in addresses:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise RecommendationResolutionError(
                "SSRF_BLOCKED", f"Endereço não público bloqueado: {address}."
            )


def _http_get(
    url: str,
    *,
    accept: str,
    max_bytes: int,
    allowed_mimes: set[str],
    purpose: str = "link",
    allowed_domains: Iterable[str] | None = None,
) -> tuple[str, str, bytes]:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _assert_safe_url(
            current, purpose=purpose, allowed_domains=allowed_domains
        )
        response = None
        try:
            for attempt in range(HTTP_ATTEMPTS):
                try:
                    response = requests.get(
                        current,
                        headers={"User-Agent": USER_AGENT, "Accept": accept},
                        timeout=HTTP_TIMEOUT,
                        allow_redirects=False,
                        stream=True,
                    )
                except requests.RequestException as exc:
                    if attempt + 1 >= HTTP_ATTEMPTS:
                        raise RecommendationResolutionError(
                            "NETWORK_ERROR",
                            f"Falha de rede ao obter {current}: {exc}",
                        ) from exc
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if (
                    response.status_code in TRANSIENT_HTTP_STATUSES
                    and attempt + 1 < HTTP_ATTEMPTS
                ):
                    response.close()
                    response = None
                    time.sleep(0.5 * (attempt + 1))
                    continue
                break
            if response is None:
                raise RecommendationResolutionError(
                    "NETWORK_ERROR", f"Sem resposta ao obter {current}."
                )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "").strip()
                if not location:
                    raise RecommendationResolutionError(
                        "BAD_REDIRECT", "Redirecionamento sem Location."
                    )
                current = urllib.parse.urljoin(current, location)
                continue
            if response.status_code != 200:
                raise RecommendationResolutionError(
                    "HTTP_ERROR",
                    f"HTTP {response.status_code} ao obter {current}.",
                )
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            if mime not in allowed_mimes:
                raise RecommendationResolutionError(
                    "BAD_MIME",
                    f"MIME inesperado {mime or '<vazio>'} em {current}.",
                )
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise RecommendationResolutionError(
                            "TOO_LARGE",
                            f"Resposta excede {max_bytes} bytes.",
                        )
                except ValueError:
                    pass
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise RecommendationResolutionError(
                        "TOO_LARGE",
                        f"Resposta excede {max_bytes} bytes.",
                    )
                chunks.append(chunk)
            final_url = _canonicalize_url(
                getattr(response, "url", None) or current
            )
            _assert_safe_url(
                final_url, purpose=purpose, allowed_domains=allowed_domains
            )
            return final_url, mime, b"".join(chunks)
        except RecommendationResolutionError:
            raise
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
    raise RecommendationResolutionError(
        "TOO_MANY_REDIRECTS", f"Demasiados redirecionamentos para {url}."
    )


def _probe_link_state(url: str) -> str:
    """Return available, missing or transient without touching cache files."""

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            _assert_safe_url(current)
        except RecommendationResolutionError:
            return "missing"
        response = None
        try:
            response = requests.get(
                current,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                    "Range": "bytes=0-2047",
                },
                timeout=HTTP_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "").strip()
                if not location:
                    return "missing"
                current = urllib.parse.urljoin(current, location)
                continue
            if response.status_code in {
                202,
                401,
                403,
                408,
                425,
                429,
                500,
                502,
                503,
                504,
            }:
                return "transient"
            if response.status_code not in {200, 206}:
                return "missing"
            final_url = getattr(response, "url", None) or current
            _assert_safe_url(final_url)
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].casefold()
            valid_mime = mime in HTML_MIME_TYPES or mime in {
                "application/json",
                "text/plain",
            }
            return "available" if valid_mime else "missing"
        except requests.RequestException:
            return "transient"
        except RecommendationResolutionError:
            return "missing"
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
    return "missing"


def _probe_link_available(url: str) -> bool:
    """Backward-compatible boolean availability probe."""

    return _probe_link_state(url) == "available"


def _get_json(url: str) -> tuple[str, dict[str, Any]]:
    final_url, _, body = _http_get(
        url,
        accept="application/json,text/javascript;q=0.9,*/*;q=0.1",
        max_bytes=MAX_PAGE_BYTES,
        allowed_mimes=JSON_MIME_TYPES,
    )
    try:
        parsed = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecommendationResolutionError(
            "BAD_JSON", f"Resposta JSON inválida de {final_url}."
        ) from exc
    if not isinstance(parsed, dict):
        raise RecommendationResolutionError(
            "BAD_JSON", f"Objeto JSON inesperado de {final_url}."
        )
    return final_url, parsed


def _decode_and_normalize_image(
    data: bytes, mime: str, source_url: str
) -> NormalizedCover:
    if mime.casefold() not in RASTER_MIME_TYPES:
        raise RecommendationResolutionError(
            "BAD_IMAGE_MIME", f"MIME raster não permitido: {mime}."
        )
    if len(data) < 1024 or len(data) > MAX_IMAGE_BYTES:
        raise RecommendationResolutionError(
            "BAD_IMAGE_SIZE", f"Tamanho de imagem inválido: {len(data)} bytes."
        )
    head = data[:512].lstrip().lower()
    if (
        head.startswith(b"<svg")
        or head.startswith(b"<?xml")
        or head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"%pdf")
    ):
        raise RecommendationResolutionError(
            "NON_RASTER_IMAGE", "SVG, HTML, XML e PDF não são capas raster."
        )
    raw_hash = hashlib.sha256(data).hexdigest()
    if raw_hash in BLOCKED_RAW_IMAGE_HASHES:
        raise RecommendationResolutionError(
            "KNOWN_PLACEHOLDER", "Imagem genérica já identificada como placeholder."
        )
    try:
        probe = Image.open(BytesIO(data))
        detected_format = (probe.format or "").upper()
        probe.verify()
        if detected_format not in RASTER_PIL_FORMATS:
            raise RecommendationResolutionError(
                "BAD_IMAGE_FORMAT",
                f"Formato raster não permitido: {detected_format or '<vazio>'}.",
            )
        reopened = Image.open(BytesIO(data))
        reopened.seek(0)
        image = ImageOps.exif_transpose(reopened)
        image.load()
    except RecommendationResolutionError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RecommendationResolutionError(
            "INVALID_IMAGE", "Pillow não conseguiu verificar/reabrir a imagem."
        ) from exc

    width, height = image.size
    if (
        width < MIN_IMAGE_WIDTH
        or height < MIN_IMAGE_HEIGHT
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise RecommendationResolutionError(
            "BAD_DIMENSIONS", f"Dimensões não permitidas: {width}x{height}."
        )
    ratio = width / height
    if ratio < 0.30 or ratio > 3.2:
        raise RecommendationResolutionError(
            "BAD_ASPECT_RATIO", f"Proporção de imagem suspeita: {ratio:.2f}."
        )

    if image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")

    if max(image.size) > MAX_OUTPUT_DIMENSION:
        image.thumbnail(
            (MAX_OUTPUT_DIMENSION, MAX_OUTPUT_DIMENSION),
            Image.Resampling.LANCZOS,
        )

    output = BytesIO()
    image.save(output, format="JPEG", quality=91, optimize=True, progressive=True)
    normalised = output.getvalue()
    try:
        verified = Image.open(BytesIO(normalised))
        verified.verify()
        verified = Image.open(BytesIO(normalised))
        verified.load()
        out_width, out_height = verified.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RecommendationResolutionError(
            "NORMALIZATION_FAILED", "JPEG normalizado não passou a reabertura."
        ) from exc
    return NormalizedCover(
        data=normalised,
        sha256=hashlib.sha256(normalised).hexdigest(),
        width=out_width,
        height=out_height,
        source_url=source_url,
        source_mime=mime,
    )


def _download_and_normalize_image(url: str) -> NormalizedCover:
    _assert_safe_url(url, purpose="image")
    final_url, mime, data = _http_get(
        url,
        accept="image/jpeg,image/png,image/webp;q=0.9",
        max_bytes=MAX_IMAGE_BYTES,
        allowed_mimes=RASTER_MIME_TYPES,
        purpose="image",
    )
    return _decode_and_normalize_image(data, mime, final_url)


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.canonical = ""
        self.jsonld: list[str] = []
        self._in_title = False
        self._in_jsonld = False
        self._script_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {key.casefold(): (value or "") for key, value in attrs}
        tag = tag.casefold()
        if tag == "meta":
            key = (
                values.get("property")
                or values.get("name")
                or values.get("itemprop")
            ).casefold()
            content = values.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "link":
            rel = values.get("rel", "").casefold()
            if "canonical" in rel and values.get("href"):
                self.canonical = values["href"].strip()
        elif tag == "title":
            self._in_title = True
        elif tag == "script" and "ld+json" in values.get("type", "").casefold():
            self._in_jsonld = True
            self._script_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            payload = "".join(self._script_parts).strip()
            if payload:
                self.jsonld.append(payload)
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_jsonld:
            self._script_parts.append(data)


def _iter_json_objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _iter_json_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_json_objects(nested)


def _value_to_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        candidates = []
        for key in ("name", "url", "contentUrl", "@id"):
            if isinstance(value.get(key), str):
                candidates.append(value[key])
        return candidates
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_value_to_strings(item))
        return values
    return []


def _page_metadata(url: str) -> dict[str, Any]:
    final_url, _, body = _http_get(
        url,
        accept="text/html,application/xhtml+xml;q=0.9",
        max_bytes=MAX_PAGE_BYTES,
        allowed_mimes=HTML_MIME_TYPES,
    )
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = body.decode("latin-1", errors="replace")
    parser = _MetadataParser()
    try:
        parser.feed(text)
    except Exception as exc:
        raise RecommendationResolutionError(
            "BAD_HTML", f"HTML inválido em {final_url}."
        ) from exc

    ld_objects: list[Mapping[str, Any]] = []
    for payload in parser.jsonld:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        ld_objects.extend(_iter_json_objects(parsed))

    ld_names: list[str] = []
    ld_images: list[str] = []
    ld_authors: list[str] = []
    ld_descriptions: list[str] = []
    ld_dates: list[str] = []
    isbns: list[str] = []
    for obj in ld_objects:
        ld_names.extend(_value_to_strings(obj.get("headline")))
        ld_names.extend(_value_to_strings(obj.get("name")))
        ld_images.extend(_value_to_strings(obj.get("image")))
        ld_images.extend(_value_to_strings(obj.get("thumbnailUrl")))
        ld_descriptions.extend(_value_to_strings(obj.get("description")))
        ld_dates.extend(_value_to_strings(obj.get("datePublished")))
        ld_dates.extend(_value_to_strings(obj.get("uploadDate")))
        ld_authors.extend(_value_to_strings(obj.get("author")))
        ld_authors.extend(_value_to_strings(obj.get("creator")))
        isbns.extend(_value_to_strings(obj.get("isbn")))

    meta = parser.meta
    title_candidates = [
        meta.get("og:title", ""),
        meta.get("twitter:title", ""),
        *ld_names,
        "".join(parser.title_parts),
    ]
    image_candidates = [
        meta.get("og:image:secure_url", ""),
        meta.get("og:image", ""),
        meta.get("twitter:image", ""),
        *ld_images,
    ]
    description_candidates = [
        meta.get("og:description", ""),
        meta.get("description", ""),
        meta.get("twitter:description", ""),
        *ld_descriptions,
    ]
    date_candidates = [
        meta.get("article:published_time", ""),
        meta.get("date", ""),
        meta.get("datepublished", ""),
        *ld_dates,
    ]
    canonical = parser.canonical or meta.get("og:url", "") or final_url
    canonical = urllib.parse.urljoin(final_url, canonical)
    canonical = _canonicalize_url(canonical)
    _assert_safe_url(canonical)

    image = ""
    for candidate in image_candidates:
        candidate = urllib.parse.urljoin(canonical, candidate.strip())
        if not candidate:
            continue
        try:
            _assert_safe_url(candidate, purpose="image")
        except RecommendationResolutionError:
            continue
        image = candidate
        break

    title = next(
        (_clean_page_title(candidate) for candidate in title_candidates if candidate.strip()),
        "",
    )
    description = next(
        (_clean_description(candidate) for candidate in description_candidates if candidate.strip()),
        "",
    )
    published_at = next(
        (
            normalised
            for candidate in date_candidates
            if (normalised := _normalise_datetime(candidate))
        ),
        "",
    )
    authors = [value.strip() for value in ld_authors if value.strip()]
    meta_author = meta.get("author", "").strip()
    if meta_author:
        authors.append(meta_author)

    isbn_matches = re.findall(
        r"(?:ISBN(?:-1[03])?[\s:=]*)?((?:97[89][\s-]?)?\d(?:[\d\s-]{8,16})[\dXx])",
        text,
        flags=re.IGNORECASE,
    )
    isbns.extend(isbn_matches)
    clean_isbns = []
    for value in isbns:
        clean = re.sub(r"[^0-9Xx]", "", value)
        if len(clean) in {10, 13}:
            clean_isbns.append(clean.upper())

    return {
        "finalUrl": final_url,
        "canonical": canonical,
        "title": title,
        "image": image,
        "description": description,
        "publishedAt": published_at,
        "authors": list(dict.fromkeys(authors)),
        "isbns": list(dict.fromkeys(clean_isbns)),
        "meta": meta,
    }


def _source_domains_for(author: str) -> set[str]:
    normalised = _normalise_text(author)
    matches: set[str] = set()
    for name, domains in SOURCE_DOMAIN_HINTS.items():
        if name in normalised:
            matches.update(domains)
    return matches


def _url_external_id(prefix: str, url: str) -> str:
    return f"{prefix}:url:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]}"


def _openlibrary_work_key(link: str) -> str:
    parsed = urllib.parse.urlsplit(link)
    if parsed.hostname and parsed.hostname.casefold() not in {
        "openlibrary.org",
        "www.openlibrary.org",
    }:
        return ""
    match = re.match(r"^/works/(OL\d+W)(?:/|$)", parsed.path, flags=re.I)
    return f"/works/{match.group(1).upper()}" if match else ""


def _validate_book_page(item: Mapping[str, Any], link: str) -> EntityResolution:
    host = _hostname(link)
    if not _host_in(host, TRUSTED_BOOK_DOMAINS):
        raise RecommendationResolutionError(
            "BOOK_DOMAIN_NOT_ALLOWED",
            f"Catálogo de livros não autorizado: {host}.",
            item=item,
        )
    openlibrary_key = _openlibrary_work_key(link)
    if openlibrary_key:
        # Open Library's public HTML is occasionally protected or unavailable
        # while its JSON catalogue remains healthy. Verify the exact work key
        # through the catalogue API instead of parsing that HTML.
        return _resolve_book_openlibrary(
            item, expected_work_key=openlibrary_key
        )
    metadata = _page_metadata(link)
    expected_title = str(item.get("title", "")).strip()
    actual_title = metadata["title"]
    combined, sequence, _ = _title_metrics(expected_title, actual_title)
    if combined < 0.88 or sequence < 0.78:
        raise RecommendationResolutionError(
            "TITLE_MISMATCH",
            f"Livro pedido {expected_title!r}, página descreve {actual_title!r}.",
            item=item,
            details={"score": combined, "sequence": sequence},
        )
    expected_author = _strip_media_prefix(str(item.get("authorOrMeta", "")))
    authors = metadata["authors"]
    author_match = _author_score(expected_author, authors)
    if expected_author and authors and author_match < 0.58:
        raise RecommendationResolutionError(
            "AUTHOR_MISMATCH",
            f"Autor {expected_author!r} não corresponde a {authors!r}.",
            item=item,
        )
    if not authors:
        raise RecommendationResolutionError(
            "AUTHOR_UNVERIFIED",
            "A página do livro não expõe autores estruturados.",
            item=item,
        )
    if not metadata["image"]:
        raise RecommendationResolutionError(
            "COVER_NOT_FOUND", "Página do livro sem capa verificável.", item=item
        )
    isbn = metadata["isbns"][0] if metadata["isbns"] else ""
    if not isbn:
        raise RecommendationResolutionError(
            "ISBN_UNVERIFIED",
            "A página comercial não expõe um ISBN-10/ISBN-13 verificável.",
            item=item,
        )
    external_id = f"isbn:{isbn}"
    host = _hostname(metadata["canonical"])
    return EntityResolution(
        link=metadata["canonical"],
        image_url=metadata["image"],
        external_id=external_id,
        source=f"book-page:{host}",
        score=min(1.0, (combined * 0.85) + (author_match * 0.15)),
        resolved_title=actual_title,
        resolved_author=", ".join(authors),
        description=metadata["description"],
        isbn=isbn,
    )


def _google_cse_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    api_key = os.environ.get("GOOGLE_CSE_API_KEY", "").strip()
    cse_id = os.environ.get("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        return []
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "num": max(1, min(limit, 10)),
        "safe": "active",
    }
    url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(params)
    _, payload = _get_json(url)
    results = []
    for entry in payload.get("items", []):
        if not isinstance(entry, Mapping):
            continue
        link = str(entry.get("link", "")).strip()
        if not link:
            continue
        try:
            _assert_safe_url(link)
        except RecommendationResolutionError:
            continue
        results.append(
            {
                "link": _canonicalize_url(link),
                "title": str(entry.get("title", "")).strip(),
                "snippet": _clean_description(str(entry.get("snippet", ""))),
            }
        )
    return results


def _resolve_book_openlibrary(
    item: Mapping[str, Any], expected_work_key: str = ""
) -> EntityResolution:
    title = str(item.get("title", "")).strip()
    author = _strip_media_prefix(str(item.get("authorOrMeta", "")))
    params = {"title": title, "author": author, "limit": 20}
    url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(params)
    _, payload = _get_json(url)
    best: tuple[float, Mapping[str, Any]] | None = None
    for doc in payload.get("docs", []):
        if not isinstance(doc, Mapping):
            continue
        work_key = str(doc.get("key", "")).strip()
        if expected_work_key and work_key.casefold() != expected_work_key.casefold():
            continue
        actual_title = str(doc.get("title", ""))
        combined, sequence, _ = _title_metrics(title, actual_title)
        if combined < 0.90 or sequence < 0.82:
            continue
        author_names = [str(value) for value in doc.get("author_name", [])]
        author_match = _author_score(author, author_names)
        if author and author_match < 0.58:
            continue
        score = (combined * 0.82) + (author_match * 0.18)
        if not best or score > best[0]:
            best = (score, doc)
    if not best:
        raise RecommendationResolutionError(
            "BOOK_NOT_FOUND",
            "Nenhuma edição exata encontrada no Open Library.",
            item=item,
        )
    score, doc = best
    work_key = str(doc.get("key", "")).strip()
    isbns = [re.sub(r"[^0-9Xx]", "", str(v)) for v in doc.get("isbn", [])]
    isbns = [value.upper() for value in isbns if len(value) in {10, 13}]
    existing_isbn = ""
    external_id = str(item.get("externalId") or "")
    if external_id.startswith("isbn:"):
        existing_isbn = external_id.split(":", 1)[1].upper()
    isbn = existing_isbn if existing_isbn in isbns else next(iter(isbns), "")
    cover_id = doc.get("cover_i")
    if cover_id:
        image = f"https://covers.openlibrary.org/b/id/{int(cover_id)}-L.jpg"
    elif isbn:
        image = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
    else:
        raise RecommendationResolutionError(
            "COVER_NOT_FOUND", "Registo Open Library sem capa.", item=item
        )
    link = urllib.parse.urljoin("https://openlibrary.org", work_key)
    external_id = f"isbn:{isbn}" if isbn else f"openlibrary:{work_key}"
    return EntityResolution(
        link=_canonicalize_url(link),
        image_url=image,
        external_id=external_id,
        source="openlibrary",
        score=score,
        resolved_title=str(doc.get("title", "")),
        resolved_author=", ".join(str(v) for v in doc.get("author_name", [])),
        description="",
        isbn=isbn,
    )


def _has_recent_verified_cache(item: Mapping[str, Any]) -> bool:
    verification = item.get("verification")
    if not isinstance(verification, Mapping):
        return False
    verified_at = _normalise_datetime(verification.get("verifiedAt"))
    if (
        verification.get("status") != "verified"
        or not verification.get("entityId")
        or not verification.get("coverHash")
        or not verified_at
    ):
        return False
    timestamp = _dt.datetime.fromisoformat(
        verified_at.replace("Z", "+00:00")
    )
    age = _dt.datetime.now(_dt.timezone.utc) - timestamp
    return (
        _dt.timedelta(0)
        <= age
        <= _dt.timedelta(hours=VERIFICATION_TTL_HOURS)
        and validate_cached_cover(item)
    )


def _is_transient_source_error(exc: RecommendationResolutionError) -> bool:
    return exc.code in {"NETWORK_ERROR", "BAD_JSON"} or (
        exc.code == "HTTP_ERROR"
        and bool(re.search(r"HTTP (?:202|429|5\d\d)\b", str(exc)))
    )


def probe_verified_source(item: Mapping[str, Any]) -> bool:
    """Check a verified source using its most stable public endpoint."""

    link = str(item.get("link") or "").strip()
    work_key = _openlibrary_work_key(link)
    if work_key:
        try:
            _, payload = _get_json(
                f"https://openlibrary.org{work_key}.json"
            )
        except RecommendationResolutionError as exc:
            # A temporary catalogue outage must not invalidate an exact entity
            # and local cover verified earlier the same day.
            return (
                _is_transient_source_error(exc)
                and _has_recent_verified_cache(item)
            )
        if str(payload.get("key", "")).casefold() != work_key.casefold():
            return False
        expected_title = str(item.get("title") or "")
        actual_title = str(payload.get("title") or "")
        combined, sequence, _ = _title_metrics(expected_title, actual_title)
        return combined >= 0.88 and sequence >= 0.78

    imdb_id = _imdb_id_from_url(link)
    if imdb_id:
        expected_external_id = str(item.get("externalId") or "")
        if expected_external_id and expected_external_id != f"imdb:{imdb_id}":
            return False
        try:
            confirmation = _wikidata_confirms_imdb(
                str(item.get("title") or ""),
                imdb_id,
                _strip_media_prefix(str(item.get("authorOrMeta") or "")),
            )
        except RecommendationResolutionError as exc:
            return (
                _is_transient_source_error(exc)
                and _has_recent_verified_cache(item)
            )
        return confirmation is not None

    state = _probe_link_state(link)
    if state == "available":
        return True
    if state == "transient":
        return _has_recent_verified_cache(item)
    return False


def _resolve_book(item: Mapping[str, Any]) -> EntityResolution:
    supplied_link = str(item.get("link", "")).strip()
    if supplied_link:
        return _validate_book_page(item, supplied_link)

    title = str(item.get("title", "")).strip()
    author = _strip_media_prefix(str(item.get("authorOrMeta", "")))
    site_query = " OR ".join(f"site:{domain}" for domain in BOOK_SEARCH_DOMAINS)
    query = f'"{title}" "{author}" ({site_query})'
    candidates = _google_cse_search(query, limit=10)
    failures: list[str] = []
    for candidate in candidates:
        try:
            return _validate_book_page(item, candidate["link"])
        except RecommendationResolutionError as exc:
            failures.append(exc.code)
    try:
        return _resolve_book_openlibrary(item)
    except RecommendationResolutionError as exc:
        raise RecommendationResolutionError(
            "BOOK_NOT_FOUND",
            "Livro não foi confirmado por página comercial nem Open Library.",
            item=item,
            details={"candidateFailures": failures, "fallback": exc.code},
        ) from exc


def _podcast_parts(value: str) -> list[str]:
    cleaned = _strip_media_prefix(value)
    parts = [part.strip() for part in re.split(r"\s*/\s*|\s+\|\s+", cleaned) if part.strip()]
    return parts or ([cleaned] if cleaned else [])


def _apple_episode_from_result(
    item: Mapping[str, Any], result: Mapping[str, Any]
) -> EntityResolution | None:
    expected_title = str(item.get("title", "")).strip()
    actual_title = str(result.get("trackName", "")).strip()
    combined, sequence, _ = _title_metrics(expected_title, actual_title)
    if combined < 0.91 or sequence < 0.86:
        return None
    metadata_parts = _podcast_parts(str(item.get("authorOrMeta", "")))
    collection = str(result.get("collectionName", ""))
    artist = str(result.get("artistName", ""))
    show_score = (
        _author_score(metadata_parts[0], [collection])
        if metadata_parts
        else 1.0
    )
    creator_score = (
        _author_score(metadata_parts[1], [artist])
        if len(metadata_parts) > 1
        else 1.0
    )
    if metadata_parts and (
        show_score < 0.58
        or not _name_identity_matches(metadata_parts[0], collection)
    ):
        return None
    # A shared generic token (for example, "Autor") is not enough to prove
    # that the episode belongs to the requested creator.
    if len(metadata_parts) > 1 and (
        creator_score < 0.58
        or not _name_identity_matches(metadata_parts[1], artist)
    ):
        return None
    link = str(
        result.get("trackViewUrl") or result.get("collectionViewUrl") or ""
    ).strip()
    image = str(
        result.get("artworkUrl600")
        or result.get("artworkUrl100")
        or ""
    ).strip()
    track_id = result.get("trackId")
    published_at = _normalise_datetime(result.get("releaseDate"))
    if not link or not image or track_id in {None, ""} or not published_at:
        return None
    return EntityResolution(
        link=_canonicalize_url(link),
        image_url=image.replace("100x100bb", "1200x1200bb"),
        external_id=f"apple-episode:{track_id}",
        source="apple-podcasts",
        score=min(
            1.0,
            (combined * 0.78)
            + (show_score * 0.14)
            + (creator_score * 0.08),
        ),
        resolved_title=actual_title,
        resolved_author=str(
            result.get("collectionName") or result.get("artistName") or ""
        ),
        description=_clean_description(str(result.get("description", ""))),
        published_at=published_at,
    )


def _apple_search_episodes(item: Mapping[str, Any]) -> list[EntityResolution]:
    title = str(item.get("title", "")).strip()
    metadata = " ".join(_podcast_parts(str(item.get("authorOrMeta", ""))))
    params = {
        "term": f"{title} {metadata}".strip(),
        "media": "podcast",
        "entity": "podcastEpisode",
        "country": "PT",
        "limit": 25,
    }
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(params)
    _, payload = _get_json(url)
    matches = []
    for result in payload.get("results", []):
        if not isinstance(result, Mapping):
            continue
        resolved = _apple_episode_from_result(item, result)
        if resolved:
            matches.append(resolved)
    return sorted(matches, key=lambda value: value.score, reverse=True)


def _apple_lookup_episode(
    item: Mapping[str, Any], episode_id: str
) -> EntityResolution:
    params = {
        "id": episode_id,
        "entity": "podcastEpisode",
        "country": "PT",
    }
    url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(params)
    _, payload = _get_json(url)
    for result in payload.get("results", []):
        if isinstance(result, Mapping):
            resolved = _apple_episode_from_result(item, result)
            if resolved:
                return resolved
    raise RecommendationResolutionError(
        "PODCAST_EPISODE_MISMATCH",
        "O link Apple não corresponde ao episódio indicado.",
        item=item,
    )


def _validate_podcast_page(
    item: Mapping[str, Any], link: str
) -> EntityResolution:
    host = _hostname(link)
    if not _host_in(host, TRUSTED_PODCAST_DOMAINS):
        raise RecommendationResolutionError(
            "PODCAST_DOMAIN_NOT_ALLOWED",
            f"Plataforma/editor de podcast não autorizado: {host}.",
            item=item,
        )
    metadata = _page_metadata(link)
    expected = str(item.get("title", "")).strip()
    combined, sequence, _ = _title_metrics(expected, metadata["title"])
    if combined < 0.90 or sequence < 0.84:
        raise RecommendationResolutionError(
            "PODCAST_EPISODE_MISMATCH",
            f"Episódio {expected!r} não corresponde a {metadata['title']!r}.",
            item=item,
        )
    if not metadata["image"]:
        raise RecommendationResolutionError(
            "COVER_NOT_FOUND", "Página do episódio sem artwork.", item=item
        )
    if not metadata["publishedAt"]:
        raise RecommendationResolutionError(
            "PODCAST_DATE_UNVERIFIED",
            "A página do episódio não expõe uma data de publicação verificável.",
            item=item,
        )
    expected_parts = _podcast_parts(str(item.get("authorOrMeta", "")))
    expected_domains = _source_domains_for(str(item.get("authorOrMeta", "")))
    author_match = max(
        (
            _author_score(part, metadata["authors"])
            for part in expected_parts
        ),
        default=1.0,
    )
    if (
        expected_parts
        and author_match < 0.58
        and not (expected_domains and _host_in(host, expected_domains))
    ):
        raise RecommendationResolutionError(
            "PODCAST_SOURCE_MISMATCH",
            "Programa/criador não corresponde aos metadados da página.",
            item=item,
        )
    return EntityResolution(
        link=metadata["canonical"],
        image_url=metadata["image"],
        external_id=_url_external_id("podcast", metadata["canonical"]),
        source=f"podcast-page:{_hostname(metadata['canonical'])}",
        score=combined,
        resolved_title=metadata["title"],
        resolved_author=", ".join(metadata["authors"]),
        description=metadata["description"],
        published_at=metadata["publishedAt"],
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _element_text(element: ET.Element, names: set[str]) -> str:
    for child in list(element):
        if _local_name(child.tag) in names and (child.text or "").strip():
            return (child.text or "").strip()
    return ""


def _element_link(element: ET.Element) -> str:
    for child in list(element):
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        rel = child.attrib.get("rel", "alternate").casefold()
        if href and rel in {"alternate", ""}:
            return href
        if (child.text or "").strip():
            return (child.text or "").strip()
    return ""


def _element_image(element: ET.Element) -> str:
    for child in element.iter():
        if _local_name(child.tag) in {"image", "thumbnail"}:
            href = (
                child.attrib.get("href")
                or child.attrib.get("url")
                or (child.text or "")
            ).strip()
            if href.startswith(("http://", "https://")):
                return href
    return ""


def _parse_feed(body: bytes, feed_url: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise RecommendationResolutionError(
            "BAD_FEED", f"RSS/Atom inválido: {feed_url}."
        ) from exc
    channel = next(
        (node for node in root.iter() if _local_name(node.tag) in {"channel", "feed"}),
        root,
    )
    feed_image = _element_image(channel)
    entries = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = _element_text(node, {"title"})
        link = _element_link(node)
        guid = _element_text(node, {"guid", "id"})
        description = _element_text(
            node, {"description", "summary", "content", "encoded"}
        )
        published = _element_text(node, {"pubdate", "published", "updated", "date"})
        categories = []
        for child in node.iter():
            if _local_name(child.tag) != "category":
                continue
            value = (
                child.attrib.get("term")
                or child.attrib.get("label")
                or (child.text or "")
            ).strip()
            if value:
                categories.append(value)
        published_at = _normalise_datetime(published)
        image = _element_image(node) or feed_image
        if not image and description:
            image_match = re.search(
                r"<img[^>]+src=[\"']([^\"']+)",
                html.unescape(description),
                flags=re.IGNORECASE,
            )
            if image_match:
                image = image_match.group(1).strip()
        if not title or not link:
            continue
        link = urllib.parse.urljoin(feed_url, link)
        try:
            _assert_safe_url(link)
            if image:
                image = urllib.parse.urljoin(feed_url, image)
                _assert_safe_url(image, purpose="image")
        except RecommendationResolutionError:
            continue
        timestamp = 0.0
        if published_at:
            try:
                timestamp = _dt.datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                pass
        entries.append(
            {
                "title": _clean_page_title(title),
                "link": _canonicalize_url(link),
                "guid": guid or link,
                "description": _clean_description(description),
                "image": image,
                "published": published,
                "publishedAt": published_at,
                "timestamp": timestamp,
                "feedUrl": feed_url,
                "categories": list(dict.fromkeys(categories)),
            }
        )
    return entries


def _fetch_feed(feed_url: str) -> list[dict[str, Any]]:
    final_url, _, body = _http_get(
        feed_url,
        accept=(
            "application/rss+xml,application/atom+xml,"
            "application/xml,text/xml;q=0.9"
        ),
        max_bytes=MAX_FEED_BYTES,
        allowed_mimes=XML_MIME_TYPES,
    )
    return _parse_feed(body, final_url)


def _apple_find_show(name: str, author: str = "") -> Mapping[str, Any] | None:
    params = {
        "term": f"{name} {author}".strip(),
        "entity": "podcast",
        "country": "PT",
        "limit": 15,
    }
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(params)
    _, payload = _get_json(url)
    best: tuple[float, Mapping[str, Any]] | None = None
    for result in payload.get("results", []):
        if not isinstance(result, Mapping):
            continue
        collection = str(result.get("collectionName", ""))
        combined, sequence, _ = _title_metrics(name, collection)
        if combined < 0.78 or sequence < 0.70:
            continue
        author_score = _author_score(
            author, [str(result.get("artistName", ""))]
        ) if author else 1.0
        score = (combined * 0.85) + (author_score * 0.15)
        if not best or score > best[0]:
            best = (score, result)
    return best[1] if best else None


def discover_podcast_episodes(
    watchlist: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    seen_ids: Iterable[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Discover real podcast episodes from Apple-provided RSS feeds.

    Returned dictionaries are candidates, not verified publication records;
    passing them through ``resolve_recommendation`` re-checks the feed entry and
    normalises its cover.
    """

    shows = [watchlist] if isinstance(watchlist, Mapping) else list(watchlist)
    seen = {str(value) for value in (seen_ids or [])}
    candidates: list[dict[str, Any]] = []
    for show in shows:
        name = str(show.get("name", "")).strip()
        author = str(show.get("author", "")).strip()
        if not name:
            continue
        apple_show = _apple_find_show(name, author)
        feed_url = str((apple_show or {}).get("feedUrl", "")).strip()
        if not feed_url:
            supplied = str(show.get("feedUrl", "")).strip()
            feed_url = supplied
        if not feed_url:
            continue
        try:
            entries = _fetch_feed(feed_url)
        except RecommendationResolutionError:
            continue
        show_image = str(
            (apple_show or {}).get("artworkUrl600")
            or (apple_show or {}).get("artworkUrl100")
            or ""
        )
        frequency = str(
            show.get("cadence") or show.get("frequency") or ""
        ).strip()
        for entry in entries:
            identity_seed = entry["guid"] or entry["link"]
            external_id = (
                "rss:" + hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:24]
            )
            if external_id in seen or entry["link"] in seen:
                continue
            image = entry["image"] or show_image
            if not image:
                continue
            if not entry["publishedAt"]:
                continue
            candidate = {
                "type": "podcast",
                "category": "Podcast",
                "title": entry["title"],
                "authorOrMeta": f"{name} / {author}".strip(" /"),
                "description": entry["description"],
                "link": entry["link"],
                "imageUrl": "",
                "externalId": external_id,
                "sourcePublishedAt": entry["publishedAt"],
                "frequency": frequency,
                "_discovery": {
                    "kind": "rss",
                    "feedUrl": feed_url,
                    "guid": entry["guid"],
                    "imageUrl": image,
                    "show": name,
                    "frequency": frequency,
                },
                "_publishedTimestamp": entry["timestamp"],
            }
            expiry = _expiry_for("podcast", entry["publishedAt"], candidate)
            if not expiry:
                continue
            if _dt.datetime.fromisoformat(
                expiry.replace("Z", "+00:00")
            ) <= _dt.datetime.now(_dt.timezone.utc):
                continue
            candidate["expiryDate"] = expiry
            candidates.append(candidate)
    candidates.sort(
        key=lambda value: float(value.get("_publishedTimestamp", 0.0)),
        reverse=True,
    )
    for candidate in candidates:
        candidate.pop("_publishedTimestamp", None)
    return candidates[: max(0, limit)]


def _resolve_podcast_rss_discovery(
    item: Mapping[str, Any], discovery: Mapping[str, Any]
) -> EntityResolution:
    feed_url = str(discovery.get("feedUrl", "")).strip()
    guid = str(discovery.get("guid", "")).strip()
    expected_title = str(item.get("title", "")).strip()
    entries = _fetch_feed(feed_url)
    best: tuple[float, Mapping[str, Any]] | None = None
    for entry in entries:
        combined, sequence, _ = _title_metrics(expected_title, entry["title"])
        guid_match = bool(guid and entry["guid"] == guid)
        if not guid_match and (combined < 0.92 or sequence < 0.88):
            continue
        score = 1.0 if guid_match else combined
        if not best or score > best[0]:
            best = (score, entry)
    if not best:
        raise RecommendationResolutionError(
            "PODCAST_EPISODE_MISMATCH",
            "O episódio deixou de existir ou não corresponde ao RSS.",
            item=item,
        )
    score, entry = best
    if not entry.get("publishedAt"):
        raise RecommendationResolutionError(
            "PODCAST_DATE_UNVERIFIED",
            "O RSS não expõe uma data de publicação verificável para o episódio.",
            item=item,
        )
    image = entry["image"] or str(discovery.get("imageUrl", "")).strip()
    if not image:
        raise RecommendationResolutionError(
            "COVER_NOT_FOUND", "RSS do episódio sem artwork.", item=item
        )
    external_id = str(item.get("externalId", "")).strip()
    if not external_id:
        external_id = (
            "rss:"
            + hashlib.sha256(entry["guid"].encode("utf-8")).hexdigest()[:24]
        )
    return EntityResolution(
        link=entry["link"],
        image_url=image,
        external_id=external_id,
        source="podcast-rss",
        score=score,
        resolved_title=entry["title"],
        resolved_author=str(discovery.get("show", "")),
        description=entry["description"],
        published_at=entry["publishedAt"],
    )


def _resolve_podcast(item: Mapping[str, Any]) -> EntityResolution:
    discovery = item.get("_discovery")
    if isinstance(discovery, Mapping) and discovery.get("kind") == "rss":
        return _resolve_podcast_rss_discovery(item, discovery)

    supplied_link = str(item.get("link", "")).strip()
    if supplied_link:
        host = _hostname(supplied_link)
        if _host_matches(host, "podcasts.apple.com"):
            match = re.search(r"[?&]i=(\d+)", supplied_link)
            if match:
                return _apple_lookup_episode(item, match.group(1))
        return _validate_podcast_page(item, supplied_link)

    matches = _apple_search_episodes(item)
    if matches:
        return matches[0]
    raise RecommendationResolutionError(
        "PODCAST_EPISODE_NOT_FOUND",
        "Apple Podcasts não confirmou um episódio com título e programa exatos.",
        item=item,
    )


def _wikidata_search(title: str) -> list[Mapping[str, Any]]:
    results: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for language in ("pt", "en"):
        params = {
            "action": "wbsearchentities",
            "search": title,
            "language": language,
            "uselang": language,
            "type": "item",
            "limit": 15,
            "format": "json",
            "origin": "*",
        }
        url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(params)
        _, payload = _get_json(url)
        for entry in payload.get("search", []):
            if not isinstance(entry, Mapping):
                continue
            entity_id = str(entry.get("id", ""))
            if entity_id and entity_id not in seen:
                seen.add(entity_id)
                results.append(entry)
    return results


def _claim_string(entity: Mapping[str, Any], property_id: str) -> str:
    for claim in entity.get("claims", {}).get(property_id, []):
        try:
            value = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if isinstance(value, str):
            return value
    return ""


def _claim_entity_ids(entity: Mapping[str, Any], property_id: str) -> list[str]:
    ids = []
    for claim in entity.get("claims", {}).get(property_id, []):
        try:
            numeric_id = claim["mainsnak"]["datavalue"]["value"]["numeric-id"]
        except (KeyError, TypeError):
            continue
        ids.append(f"Q{numeric_id}")
    return ids


def _wikidata_entity(entity_id: str) -> Mapping[str, Any]:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{urllib.parse.quote(entity_id)}.json"
    _, payload = _get_json(url)
    entity = payload.get("entities", {}).get(entity_id)
    if not isinstance(entity, Mapping):
        raise RecommendationResolutionError(
            "WIKIDATA_MISSING", f"Entidade {entity_id} não encontrada."
        )
    return entity


def _wikidata_matching_title(
    entity: Mapping[str, Any], expected: str
) -> tuple[str, float, float]:
    """Use the closest Wikidata label/alias, regardless of search language."""
    candidates: list[str] = []
    for language in ("pt", "pt-br", "en", "es", "fr"):
        label = entity.get("labels", {}).get(language, {}).get("value")
        if label:
            candidates.append(str(label))
        for alias in entity.get("aliases", {}).get(language, []):
            value = alias.get("value") if isinstance(alias, Mapping) else ""
            if value:
                candidates.append(str(value))
    best = ("", 0.0, 0.0)
    for candidate in dict.fromkeys(candidates):
        combined, sequence, _ = _title_metrics(expected, candidate)
        if (combined, sequence) > (best[1], best[2]):
            best = (candidate, combined, sequence)
    return best


def _wikidata_labels(entity_ids: Sequence[str]) -> list[str]:
    if not entity_ids:
        return []
    params = {
        "action": "wbgetentities",
        "ids": "|".join(entity_ids[:25]),
        "props": "labels",
        "languages": "pt|en",
        "format": "json",
        "origin": "*",
    }
    url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(params)
    _, payload = _get_json(url)
    labels = []
    for entity in payload.get("entities", {}).values():
        if not isinstance(entity, Mapping):
            continue
        label_map = entity.get("labels", {})
        for lang in ("pt", "en"):
            value = label_map.get(lang, {}).get("value")
            if value:
                labels.append(str(value))
                break
    return labels


def _commons_image_url(filename: str) -> str:
    quoted = urllib.parse.quote(filename.replace(" ", "_"), safe="")
    return f"https://commons.wikimedia.org/wiki/Special:Redirect/file/{quoted}?width=1200"


def _imdb_id_from_url(url: str) -> str:
    match = re.search(r"/title/(tt\d+)", url)
    return match.group(1) if match else ""


def _validate_imdb_page(
    item: Mapping[str, Any], imdb_url: str, imdb_id: str
) -> tuple[dict[str, Any], float]:
    metadata = _page_metadata(imdb_url)
    combined, sequence, _ = _title_metrics(
        str(item.get("title", "")), metadata["title"]
    )
    if combined < 0.84 or sequence < 0.78:
        raise RecommendationResolutionError(
            "MOVIE_TITLE_MISMATCH",
            f"IMDb descreve {metadata['title']!r}, não {item.get('title')!r}.",
            item=item,
        )
    page_id = _imdb_id_from_url(metadata["canonical"])
    if page_id and page_id != imdb_id:
        raise RecommendationResolutionError(
            "IMDB_ID_MISMATCH", "IMDb redirecionou para outro título.", item=item
        )
    return metadata, combined


def _wikidata_confirms_imdb(
    title: str, imdb_id: str, director_expected: str
) -> tuple[str, list[str], float, Mapping[str, Any]] | None:
    transient_errors: list[RecommendationResolutionError] = []
    for search_result in _wikidata_search(title):
        entity_id = str(search_result.get("id", ""))
        try:
            entity = _wikidata_entity(entity_id)
        except RecommendationResolutionError as exc:
            if _is_transient_source_error(exc):
                transient_errors.append(exc)
            continue
        label, combined, sequence = _wikidata_matching_title(entity, title)
        if combined < 0.84 or sequence < 0.78:
            continue
        if _claim_string(entity, "P345") != imdb_id:
            continue
        directors = _wikidata_labels(_claim_entity_ids(entity, "P57"))
        if (
            director_expected
            and directors
            and _author_score(director_expected, directors) < 0.58
        ):
            continue
        return label, directors, combined, entity
    if transient_errors:
        raise transient_errors[-1]
    return None


def _resolve_movie(item: Mapping[str, Any]) -> EntityResolution:
    supplied_link = str(item.get("link", "")).strip()
    if supplied_link:
        if not _host_matches(_hostname(supplied_link), "imdb.com"):
            raise RecommendationResolutionError(
                "MOVIE_LINK_NOT_IMDB",
                "O hostname do link de filme tem de ser imdb.com.",
                item=item,
            )
        imdb_id = _imdb_id_from_url(supplied_link)
        if not imdb_id:
            raise RecommendationResolutionError(
                "MOVIE_LINK_NOT_IMDB",
                "Filmes exigem um link IMDb canónico.",
                item=item,
            )
        canonical_imdb = f"https://www.imdb.com/title/{imdb_id}/"
        director_expected = _strip_media_prefix(
            str(item.get("authorOrMeta", ""))
        )
        confirmation = _wikidata_confirms_imdb(
            str(item.get("title", "")), imdb_id, director_expected
        )
        if not confirmation:
            raise RecommendationResolutionError(
                "WIKIDATA_IMDB_MISMATCH",
                "Wikidata não confirma o par título/IMDb/realizador.",
                item=item,
            )
        canonical_title, directors, wikidata_score, entity = confirmation
        metadata: dict[str, Any] = {}
        imdb_score = wikidata_score
        try:
            metadata, imdb_score = _validate_imdb_page(
                item, canonical_imdb, imdb_id
            )
        except RecommendationResolutionError:
            # IMDb commonly answers automated, otherwise valid requests with
            # HTTP 202. Wikidata already proves title, director and IMDb ID.
            pass
        image = str(metadata.get("image", ""))
        if not image:
            commons_filename = _claim_string(entity, "P18")
            if commons_filename:
                image = _commons_image_url(commons_filename)
        if not image:
            raise RecommendationResolutionError(
                "COVER_NOT_FOUND",
                "Wikidata/IMDb não disponibilizam um poster verificável.",
                item=item,
            )
        return EntityResolution(
            link=canonical_imdb,
            image_url=image,
            external_id=f"imdb:{imdb_id}",
            source="wikidata+imdb",
            score=(imdb_score * 0.5) + (wikidata_score * 0.5),
            resolved_title=canonical_title or str(metadata.get("title", "")),
            resolved_author=", ".join(directors),
            description=str(metadata.get("description", "")),
        )

    title = str(item.get("title", "")).strip()
    director_expected = _strip_media_prefix(str(item.get("authorOrMeta", "")))
    failures: list[str] = []
    for search_result in _wikidata_search(title):
        description = _normalise_text(search_result.get("description", ""))
        if not any(
            marker in description
            for marker in (
                "filme",
                "film",
                "documentario",
                "documentary",
                "television series",
                "serie de televisao",
            )
        ):
            continue
        entity_id = str(search_result.get("id", ""))
        try:
            entity = _wikidata_entity(entity_id)
        except RecommendationResolutionError as exc:
            failures.append(exc.code)
            continue
        label, combined, sequence = _wikidata_matching_title(entity, title)
        if combined < 0.86 or sequence < 0.80:
            continue
        imdb_id = _claim_string(entity, "P345")
        if not re.fullmatch(r"tt\d+", imdb_id):
            continue
        director_labels = _wikidata_labels(_claim_entity_ids(entity, "P57"))
        director_score = _author_score(director_expected, director_labels)
        if director_expected and director_labels and director_score < 0.58:
            continue
        imdb_url = f"https://www.imdb.com/title/{imdb_id}/"
        metadata: dict[str, Any] = {}
        imdb_score = combined
        try:
            metadata, imdb_score = _validate_imdb_page(
                item, imdb_url, imdb_id
            )
        except RecommendationResolutionError as exc:
            failures.append(exc.code)
        image = str(metadata.get("image", ""))
        if not image:
            commons_filename = _claim_string(entity, "P18")
            if commons_filename:
                image = _commons_image_url(commons_filename)
        if not image:
            continue
        source_description = str(metadata.get("description", "")).strip()
        score = (
            (combined * 0.55)
            + (imdb_score * 0.30)
            + ((director_score if director_labels else 1.0) * 0.15)
        )
        return EntityResolution(
            link=imdb_url,
            image_url=image,
            external_id=f"imdb:{imdb_id}",
            source="wikidata+imdb",
            score=min(1.0, score),
            resolved_title=label,
            resolved_author=", ".join(director_labels),
            description=source_description,
        )
    raise RecommendationResolutionError(
        "MOVIE_NOT_FOUND",
        "Wikidata/IMDb não confirmaram título, realizador e poster.",
        item=item,
        details={"failures": failures},
    )


def _youtube_video_id(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").casefold()
    if _host_matches(host, "youtu.be"):
        return parsed.path.strip("/").split("/", 1)[0]
    if _host_matches(host, "youtube.com"):
        if parsed.path == "/watch":
            return urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        match = re.match(r"/(?:shorts|embed)/([^/?#]+)", parsed.path)
        if match:
            return match.group(1)
    return ""


def _validate_youtube_highlight(
    item: Mapping[str, Any], link: str
) -> EntityResolution:
    video_id = _youtube_video_id(link)
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        raise RecommendationResolutionError(
            "BAD_YOUTUBE_URL", "URL YouTube não identifica um vídeo.", item=item
        )
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    oembed_url = (
        "https://www.youtube.com/oembed?"
        + urllib.parse.urlencode({"url": watch_url, "format": "json"})
    )
    _, payload = _get_json(oembed_url)
    actual_title = str(payload.get("title", ""))
    combined, sequence, _ = _title_metrics(
        str(item.get("title", "")), actual_title
    )
    if combined < 0.74 or sequence < 0.68:
        raise RecommendationResolutionError(
            "HIGHLIGHT_TITLE_MISMATCH",
            f"Vídeo {actual_title!r} não corresponde ao destaque.",
            item=item,
        )
    expected_source = _strip_media_prefix(str(item.get("authorOrMeta", "")))
    channel = str(payload.get("author_name", ""))
    if expected_source and _author_score(expected_source, [channel]) < 0.48:
        raise RecommendationResolutionError(
            "SOURCE_MISMATCH",
            f"Canal {channel!r} não corresponde a {expected_source!r}.",
            item=item,
        )
    thumbnail = str(payload.get("thumbnail_url", "")).strip()
    if not thumbnail:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    published_at = ""
    source_description = ""
    try:
        page_metadata = _page_metadata(watch_url)
        published_at = page_metadata.get("publishedAt", "")
        source_description = page_metadata.get("description", "")
    except RecommendationResolutionError:
        pass
    return EntityResolution(
        link=watch_url,
        image_url=thumbnail,
        external_id=f"youtube:{video_id}",
        source=f"youtube:{channel}",
        score=combined,
        resolved_title=actual_title,
        resolved_author=channel,
        description=source_description,
        published_at=published_at,
    )


def _validate_highlight_page(
    item: Mapping[str, Any], link: str
) -> EntityResolution:
    host = _hostname(link)
    if _host_in(host, BLOCKED_LINK_HOSTS):
        raise RecommendationResolutionError(
            "DISALLOWED_SOURCE",
            f"Fonte não permitida para destaque: {host}.",
            item=item,
        )
    if not _host_in(host, TRUSTED_HIGHLIGHT_DOMAINS):
        raise RecommendationResolutionError(
            "DISALLOWED_SOURCE",
            f"Destaque deve vir de jornal/canal autorizado, não {host}.",
            item=item,
        )
    if _host_matches(host, "youtube.com") or _host_matches(host, "youtu.be"):
        return _validate_youtube_highlight(item, link)

    expected_domains = _source_domains_for(str(item.get("authorOrMeta", "")))
    if expected_domains and not _host_in(host, expected_domains):
        raise RecommendationResolutionError(
            "SOURCE_MISMATCH",
            f"Editor indicado não corresponde ao domínio {host}.",
            item=item,
        )
    metadata = _page_metadata(link)
    canonical_host = _hostname(metadata["canonical"])
    if not _host_in(canonical_host, TRUSTED_HIGHLIGHT_DOMAINS):
        raise RecommendationResolutionError(
            "DISALLOWED_SOURCE",
            f"URL canónico saiu da allowlist: {canonical_host}.",
            item=item,
        )
    combined, sequence, _ = _title_metrics(
        str(item.get("title", "")), metadata["title"]
    )
    if combined < 0.74 or sequence < 0.66:
        raise RecommendationResolutionError(
            "HIGHLIGHT_TITLE_MISMATCH",
            f"Artigo {metadata['title']!r} não corresponde ao título candidato.",
            item=item,
        )
    categories = [
        metadata.get("meta", {}).get(key, "")
        for key in (
            "article:section",
            "section",
            "parsely-section",
        )
    ]
    if not is_eligible_highlight(
        title=metadata["title"],
        description=metadata["description"],
        link=metadata["canonical"],
        categories=categories,
    ):
        raise RecommendationResolutionError(
            "NEWS_NOT_ALLOWED",
            "Notícias correntes não são elegíveis para Destaque.",
            item=item,
        )
    if not metadata["image"]:
        raise RecommendationResolutionError(
            "COVER_NOT_FOUND", "Artigo sem imagem editorial.", item=item
        )
    return EntityResolution(
        link=metadata["canonical"],
        image_url=metadata["image"],
        external_id=_url_external_id("article", metadata["canonical"]),
        source=f"news:{canonical_host}",
        score=combined,
        resolved_title=metadata["title"],
        resolved_author=", ".join(metadata["authors"]) or canonical_host,
        description=metadata["description"],
        published_at=metadata["publishedAt"],
    )


def _resolve_highlight(item: Mapping[str, Any]) -> EntityResolution:
    discovery = item.get("_discovery")
    if (
        isinstance(discovery, Mapping)
        and discovery.get("kind") == "rss-highlight"
    ):
        return _resolve_highlight_rss_discovery(item, discovery)

    supplied_link = str(item.get("link", "")).strip()
    if supplied_link:
        return _validate_highlight_page(item, supplied_link)

    title = str(item.get("title", "")).strip()
    author = str(item.get("authorOrMeta", "")).strip()
    expected_domains = _source_domains_for(author)
    if expected_domains:
        domain_query = " OR ".join(f"site:{domain}" for domain in expected_domains)
    else:
        domain_query = " OR ".join(
            f"site:{domain}"
            for domain in sorted(TRUSTED_HIGHLIGHT_DOMAINS)
            if domain not in {"youtube.com", "youtu.be"}
        )
    query = f'"{title}" "{author}" ({domain_query})'
    failures: list[str] = []
    for candidate in _google_cse_search(query, limit=10):
        try:
            return _validate_highlight_page(item, candidate["link"])
        except RecommendationResolutionError as exc:
            failures.append(exc.code)
    raise RecommendationResolutionError(
        "HIGHLIGHT_NOT_FOUND",
        "Nenhum artigo/vídeo autorizado corresponde exatamente ao destaque.",
        item=item,
        details={"failures": failures, "cseConfigured": bool(os.environ.get("GOOGLE_CSE_API_KEY"))},
    )


def _resolve_highlight_rss_discovery(
    item: Mapping[str, Any], discovery: Mapping[str, Any]
) -> EntityResolution:
    """Bind a highlight to a current entry in a trusted publisher feed."""
    feed_url = str(discovery.get("feedUrl", "")).strip()
    feed_host = _hostname(feed_url)
    if not _host_in(feed_host, TRUSTED_HIGHLIGHT_DOMAINS):
        raise RecommendationResolutionError(
            "DISALLOWED_SOURCE",
            f"RSS de destaque não autorizado: {feed_host}.",
            item=item,
        )
    expected_title = str(item.get("title", "")).strip()
    expected_guid = str(discovery.get("guid", "")).strip()
    best: tuple[float, Mapping[str, Any]] | None = None
    for entry in _fetch_feed(feed_url):
        combined, sequence, _ = _title_metrics(
            expected_title, entry["title"]
        )
        guid_match = bool(
            expected_guid
            and expected_guid in {entry["guid"], entry["link"]}
        )
        if not guid_match and (combined < 0.92 or sequence < 0.88):
            continue
        link_host = _hostname(entry["link"])
        if not _host_in(link_host, TRUSTED_HIGHLIGHT_DOMAINS):
            continue
        score = 1.0 if guid_match else combined
        if not best or score > best[0]:
            best = (score, entry)
    if not best:
        raise RecommendationResolutionError(
            "HIGHLIGHT_NOT_FOUND",
            "O artigo deixou de constar do RSS autorizado.",
            item=item,
        )
    score, entry = best
    if not is_eligible_highlight(
        title=entry["title"],
        description=entry["description"],
        link=entry["link"],
        categories=entry.get("categories", []),
    ):
        raise RecommendationResolutionError(
            "NEWS_NOT_ALLOWED",
            "Notícias correntes não são elegíveis para Destaque.",
            item=item,
        )
    image = entry.get("image") or str(
        discovery.get("imageUrl", "")
    ).strip()
    if not image:
        raise RecommendationResolutionError(
            "COVER_NOT_FOUND",
            "O RSS do artigo não fornece uma imagem editorial.",
            item=item,
        )
    if not entry.get("publishedAt"):
        raise RecommendationResolutionError(
            "SOURCE_DATE_UNVERIFIED",
            "O RSS do artigo não fornece uma data verificável.",
            item=item,
        )
    return EntityResolution(
        link=entry["link"],
        image_url=image,
        external_id=_url_external_id("article", entry["link"]),
        source=f"news-rss:{feed_host}",
        score=score,
        resolved_title=entry["title"],
        resolved_author=feed_host,
        description=entry["description"],
        published_at=entry["publishedAt"],
    )


def _rss_discovery_candidates(feed_url: str) -> list[dict[str, Any]]:
    entries = _fetch_feed(feed_url)
    host = _hostname(feed_url)
    if not _host_in(host, TRUSTED_HIGHLIGHT_DOMAINS):
        return []
    source = host
    candidates = []
    for entry in entries:
        if not entry["image"] or not entry["publishedAt"]:
            continue
        if not is_eligible_highlight(
            title=entry["title"],
            description=entry["description"],
            link=entry["link"],
            categories=entry.get("categories", []),
        ):
            continue
        candidate = {
            "type": "highlight",
            "category": "Destaque",
            "title": entry["title"],
            "authorOrMeta": source,
            "description": entry["description"],
            "link": entry["link"],
            "imageUrl": "",
            "externalId": _url_external_id("article", entry["link"]),
            "sourcePublishedAt": entry["publishedAt"],
        }
        expiry = _expiry_for("highlight", entry["publishedAt"], candidate)
        if not expiry:
            continue
        if _dt.datetime.fromisoformat(
            expiry.replace("Z", "+00:00")
        ) <= _dt.datetime.now(_dt.timezone.utc):
            continue
        candidate["expiryDate"] = expiry
        candidates.append(candidate)
    return candidates


def discover_highlights(
    limit: int = 10, seen_urls: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    """
    Discover actual highlight pages from CSE and optional trusted RSS feeds.

    Configure search themes with ``HIGHLIGHT_SEARCH_QUERIES`` separated by
    ``||`` and RSS URLs with ``HIGHLIGHT_RSS_FEEDS`` separated by commas/newlines.
    """

    seen = {_canonicalize_url(value) for value in (seen_urls or []) if value}
    candidates: list[dict[str, Any]] = []
    queries_env = os.environ.get("HIGHLIGHT_SEARCH_QUERIES", "").strip()
    queries = [
        value.strip()
        for value in queries_env.split("||")
        if value.strip()
    ] or [
        "opinião política Portugal",
        "investigação política Portugal",
        "grande reportagem sociedade Portugal",
        "debate político Portugal",
    ]
    trusted_query = " OR ".join(
        f"site:{domain}"
        for domain in sorted(TRUSTED_HIGHLIGHT_DOMAINS)
        if domain not in {"youtu.be"}
    )
    for query in queries:
        for result in _google_cse_search(f"{query} ({trusted_query})", limit=10):
            link = result["link"]
            if link in seen or _host_in(_hostname(link), BLOCKED_LINK_HOSTS):
                continue
            host = _hostname(link)
            if not _host_in(host, TRUSTED_HIGHLIGHT_DOMAINS):
                continue
            if not is_eligible_highlight(
                title=result["title"],
                description=result["snippet"],
                link=link,
            ):
                continue
            candidate = {
                "type": "highlight",
                "category": "Destaque",
                "title": _clean_page_title(result["title"]),
                "authorOrMeta": host,
                "description": result["snippet"],
                "link": link,
                "imageUrl": "",
                "externalId": _url_external_id("article", link),
            }
            candidates.append(candidate)
            seen.add(link)
            if len(candidates) >= limit:
                return candidates

    feeds_env = os.environ.get("HIGHLIGHT_RSS_FEEDS", "")
    feed_urls = [
        value.strip()
        for value in re.split(r"[\r\n,]+", feeds_env)
        if value.strip()
    ]
    for feed_url in feed_urls:
        try:
            feed_candidates = _rss_discovery_candidates(feed_url)
        except RecommendationResolutionError:
            continue
        for candidate in feed_candidates:
            link = candidate["link"]
            if link in seen:
                continue
            candidates.append(candidate)
            seen.add(link)
            if len(candidates) >= limit:
                return candidates
    return candidates[: max(0, limit)]


def _resolve_entity(item: Mapping[str, Any]) -> EntityResolution:
    raw_type = str(item.get("type", "")).casefold().strip()
    media_type = TYPE_ALIASES.get(raw_type)
    if not media_type:
        raise RecommendationResolutionError(
            "UNSUPPORTED_TYPE", f"Tipo não suportado: {raw_type!r}.", item=item
        )
    if media_type == "book":
        return _resolve_book(item)
    if media_type == "podcast":
        return _resolve_podcast(item)
    if media_type == "movie":
        return _resolve_movie(item)
    return _resolve_highlight(item)


def _manifest_path(image_path: str) -> str:
    return os.path.splitext(image_path)[0] + ".json"


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".tmp-politometro-", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _local_cover_path(image_url: str) -> str | None:
    if not isinstance(image_url, str) or not image_url.startswith("/covers/"):
        return None
    relative = urllib.parse.unquote(image_url[len("/covers/") :])
    if (
        not relative
        or "/" in relative
        or "\\" in relative
        or relative in {".", ".."}
        or not relative.casefold().endswith(".jpg")
    ):
        return None
    candidate = os.path.abspath(os.path.join(CACHE_DIR, relative))
    cache_root = os.path.abspath(CACHE_DIR)
    try:
        if os.path.commonpath([candidate, cache_root]) != cache_root:
            return None
    except ValueError:
        return None
    return candidate


def validate_cached_cover(item: Mapping[str, Any]) -> bool:
    """Validate the local JPEG and its identity-bound manifest without network."""

    if item.get("resolutionStatus") != "verified":
        return False
    verification = item.get("verification")
    if not isinstance(verification, Mapping) or verification.get("status") != "verified":
        return False
    image_path = _local_cover_path(str(item.get("imageUrl", "")))
    if not image_path or not os.path.isfile(image_path):
        return False
    manifest_path = _manifest_path(image_path)
    if not os.path.isfile(manifest_path):
        return False
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("cacheVersion") != CACHE_VERSION:
            return False
        if manifest.get("type") != str(item.get("type", "")).casefold():
            return False
        if manifest.get("titleNormalized") != _normalise_text(item.get("title", "")):
            return False
        entity_id = str(
            verification.get("entityId") or item.get("externalId") or ""
        )
        if not entity_id or manifest.get("entityId") != entity_id:
            return False
        canonical_link = _canonicalize_url(str(item.get("link", "")))
        if not canonical_link or manifest.get("canonicalLink") != canonical_link:
            return False
        expected_hash = str(verification.get("coverHash", ""))
        if not expected_hash or manifest.get("coverHash") != expected_hash:
            return False
        size = os.path.getsize(image_path)
        if size < 1024 or size > MAX_IMAGE_BYTES:
            return False
        with open(image_path, "rb") as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return False
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != expected_hash:
            return False
        probe = Image.open(BytesIO(data))
        if (probe.format or "").upper() != "JPEG":
            return False
        probe.verify()
        reopened = Image.open(BytesIO(data))
        reopened.load()
        if (
            reopened.width < MIN_IMAGE_WIDTH
            or reopened.height < MIN_IMAGE_HEIGHT
            or reopened.width * reopened.height > MAX_IMAGE_PIXELS
        ):
            return False
    except (OSError, ValueError, TypeError, json.JSONDecodeError, UnidentifiedImageError):
        return False
    return True


def load_cover_for_item(item: Mapping[str, Any]) -> Image.Image | None:
    """Return a detached RGBA Pillow image only for a fully valid cache entry."""

    if not validate_cached_cover(item):
        return None
    image_path = _local_cover_path(str(item.get("imageUrl", "")))
    if not image_path:
        return None
    try:
        with Image.open(image_path) as image:
            return image.convert("RGBA").copy()
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def _already_verified(item: Mapping[str, Any]) -> dict[str, Any] | None:
    if not validate_cached_cover(item):
        return None
    verification = item.get("verification")
    if not isinstance(verification, Mapping):
        return None
    verified_at = _normalise_datetime(verification.get("verifiedAt"))
    if not verified_at:
        return None
    try:
        ttl_hours = int(
            os.environ.get(
                "RESOLUTION_TTL_HOURS", str(VERIFICATION_TTL_HOURS)
            )
        )
    except ValueError:
        ttl_hours = VERIFICATION_TTL_HOURS
    ttl_hours = max(1, min(ttl_hours, 168))
    timestamp = _dt.datetime.fromisoformat(
        verified_at.replace("Z", "+00:00")
    )
    age = _dt.datetime.now(_dt.timezone.utc) - timestamp
    if age < _dt.timedelta(0) or age > _dt.timedelta(hours=ttl_hours):
        return None
    expiry = _normalise_datetime(
        item.get("expiryDate"), allow_future=True
    )
    if item.get("type") in {"podcast", "highlight"}:
        if not _normalise_datetime(item.get("sourcePublishedAt")) or not expiry:
            return None
        if _dt.datetime.fromisoformat(
            expiry.replace("Z", "+00:00")
        ) <= _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(
            hours=MIN_REVIEW_VALIDITY_HOURS
        ):
            return None
    if not probe_verified_source(item):
        return None
    if validate_cached_cover(item):
        canonical_title = str(verification.get("resolvedTitle", "")).strip()
        canonical_author = str(verification.get("resolvedAuthor", "")).strip()
        grounded_description = str(
            verification.get("sourceDescription", "")
        ).strip()
        if not canonical_title or not grounded_description:
            return None
        if item.get("sourceHint") == "ai-catalogue-candidate" and not _has_content_description(
            grounded_description,
            str(item.get("type") or ""),
            canonical_title,
            canonical_author,
        ):
            return None
        cached = dict(item)
        cached["title"] = canonical_title
        if canonical_author:
            cached["authorOrMeta"] = canonical_author
        editorial_description = str(
            verification.get("editorialDescription", "")
        ).strip()
        if (
            item.get("type") == "podcast"
            and _has_content_description(
                editorial_description,
                "podcast",
                canonical_title,
                canonical_author,
            )
        ):
            cached["description"] = editorial_description
        else:
            cached["description"] = grounded_description
        return cached
    return None


def _factual_description(
    media_type: str,
    title: str,
    author: str,
    published_at: str,
    source: str,
) -> str:
    date_text = ""
    normalised_date = _normalise_datetime(published_at)
    if normalised_date:
        parsed = _dt.datetime.fromisoformat(
            normalised_date.replace("Z", "+00:00")
        )
        date_text = parsed.strftime("%d/%m/%Y")
    if media_type == "book":
        return (
            f'Livro “{title}”, de {author}.'
            if author
            else f'Livro “{title}”, confirmado em catálogo bibliográfico.'
        )
    if media_type == "podcast":
        base = (
            f'Episódio “{title}” do podcast {author}.'
            if author
            else f'Episódio de podcast “{title}”.'
        )
        return base[:-1] + (f", publicado em {date_text}." if date_text else ".")
    if media_type == "movie":
        return (
            f'Filme “{title}”, realizado por {author}.'
            if author
            else f'Filme “{title}”, confirmado por Wikidata e IMDb.'
        )
    label = "Vídeo" if source.startswith("youtube:") else "Artigo"
    base = (
        f'{label} “{title}”, publicado por {author}'
        if author
        else f'{label} “{title}”'
    )
    return base + (f" em {date_text}." if date_text else ".")


def resolve_recommendation(
    item: Mapping[str, Any], force: bool = False
) -> dict[str, Any]:
    """
    Resolve and verify a recommendation atomically.

    The input mapping is not partially mutated on failure.  The returned mapping
    contains a canonical link, identity-specific local JPEG path and complete
    verification block.
    """

    if not isinstance(item, Mapping):
        raise RecommendationResolutionError(
            "BAD_ITEM", "A recomendação deve ser um objeto."
        )
    title = str(item.get("title", "")).strip()
    media_type = str(item.get("type", "")).casefold().strip()
    if not title or len(title) < 2:
        raise RecommendationResolutionError(
            "BAD_TITLE", "Título em falta ou demasiado curto.", item=item
        )
    if media_type not in TYPE_ALIASES:
        raise RecommendationResolutionError(
            "UNSUPPORTED_TYPE", f"Tipo não suportado: {media_type!r}.", item=item
        )
    if not force:
        cached = _already_verified(item)
        if cached is not None:
            return cached

    entity_type = TYPE_ALIASES[media_type]
    resolved_type = (
        media_type
        if media_type in {"nostalgia", "investigation"}
        else entity_type
    )
    entity = _resolve_entity(item)
    if not entity.link or not entity.external_id or not entity.image_url:
        raise RecommendationResolutionError(
            "INCOMPLETE_ENTITY",
            "A fonte não forneceu link, identidade e imagem em conjunto.",
            item=item,
        )
    _assert_safe_url(entity.link)
    _assert_safe_url(entity.image_url, purpose="image")

    canonical_title = _clean_page_title(entity.resolved_title)
    if not canonical_title:
        raise RecommendationResolutionError(
            "CANONICAL_TITLE_MISSING",
            "A fonte verificada não forneceu um título canónico.",
            item=item,
        )
    canonical_author = re.sub(
        r"\s+", " ", str(entity.resolved_author or "").strip()
    )
    if not canonical_author and resolved_type in {
        "highlight",
        "nostalgia",
        "investigation",
    }:
        canonical_author = _hostname(entity.link)
    if not canonical_author and resolved_type in {"book", "podcast", "movie"}:
        raise RecommendationResolutionError(
            "CANONICAL_AUTHOR_MISSING",
            "A fonte verificada não forneceu autor/programa/realizador.",
            item=item,
        )

    identity = entity.external_id or entity.link
    existing_published = _normalise_datetime(item.get("sourcePublishedAt"))
    source_published_at = existing_published or _normalise_datetime(
        entity.published_at
    )
    if resolved_type in {"podcast", "highlight"} and not source_published_at:
        raise RecommendationResolutionError(
            "SOURCE_DATE_UNVERIFIED",
            "Podcast/destaque sem data de publicação verificável.",
            item=item,
        )
    derived_expiry = _expiry_for(
        resolved_type, source_published_at, item
    )
    if resolved_type in {"podcast", "highlight"} and not derived_expiry:
        raise RecommendationResolutionError(
            "EXPIRY_UNVERIFIED",
            "Podcast/destaque sem data de expiração válida.",
            item=item,
        )
    if derived_expiry:
        expiry_dt = _dt.datetime.fromisoformat(
            derived_expiry.replace("Z", "+00:00")
        )
        now = _dt.datetime.now(_dt.timezone.utc)
        if expiry_dt <= now:
            raise RecommendationResolutionError(
                "CONTENT_EXPIRED",
                f"Conteúdo expirou em {derived_expiry}.",
                item=item,
            )
        minimum_expiry = now + _dt.timedelta(
            hours=MIN_REVIEW_VALIDITY_HOURS
        )
        if expiry_dt <= minimum_expiry:
            raise RecommendationResolutionError(
                "CONTENT_TOO_CLOSE_TO_EXPIRY",
                (
                    f"Conteúdo expira em {derived_expiry}; são necessárias "
                    f"pelo menos {MIN_REVIEW_VALIDITY_HOURS} horas para "
                    "revisão e publicação."
                ),
                item=item,
            )

    source_description = _clean_description(entity.description)
    item_description = _clean_description(str(item.get("description", "")))
    if not _has_content_description(
        source_description,
        resolved_type,
        canonical_title,
        canonical_author,
    ):
        source_description = ""
    if not _has_content_description(
        item_description,
        resolved_type,
        title,
        str(item.get("authorOrMeta", "")),
    ):
        item_description = ""
    factual_description = source_description or item_description
    if not factual_description:
        if item.get("sourceHint") == "ai-catalogue-candidate":
            raise RecommendationResolutionError(
                "SOURCE_DESCRIPTION_MISSING",
                "A fonte verificou o título e a imagem, mas não trouxe uma descrição útil.",
                item=item,
            )
        factual_description = _factual_description(
            resolved_type,
            canonical_title,
            canonical_author,
            source_published_at,
            entity.source,
        )
    key = _cache_key(canonical_title, resolved_type, identity)
    image_path = os.path.join(CACHE_DIR, key + ".jpg")
    public_image_url = f"/covers/{key}.jpg"
    provisional = dict(item)
    provisional.update(
        {
            "type": resolved_type,
            "title": canonical_title,
            "authorOrMeta": canonical_author,
            "description": factual_description,
            "link": _canonicalize_url(entity.link),
            "imageUrl": public_image_url,
            "externalId": entity.external_id,
            "resolutionStatus": "verified",
            "verification": {
                "status": "verified",
                "source": entity.source,
                "entityId": entity.external_id,
                "score": round(float(entity.score), 4),
                "coverHash": "",
                "verifiedAt": "",
                "resolvedTitle": canonical_title,
                "resolvedAuthor": canonical_author,
            },
        }
    )
    if source_published_at:
        provisional["verification"]["sourcePublishedAt"] = source_published_at
    if source_published_at:
        provisional["sourcePublishedAt"] = source_published_at
    if derived_expiry:
        provisional["expiryDate"] = derived_expiry

    if not force:
        # Reuse is permitted only after link/entity resolution and only when the
        # manifest proves that this exact entity owns the bytes.
        manifest_path = _manifest_path(image_path)
        if os.path.exists(image_path) and os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
                provisional["verification"]["coverHash"] = manifest.get(
                    "coverHash", ""
                )
                provisional["verification"]["verifiedAt"] = manifest.get(
                    "verifiedAt", ""
                )
                if validate_cached_cover(provisional):
                    provisional["description"] = factual_description
                    provisional["verification"][
                        "sourceDescription"
                    ] = factual_description
                    return provisional
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass

    cover = _download_and_normalize_image(entity.image_url)
    verified_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    verification: dict[str, Any] = {
        "status": "verified",
        "source": entity.source,
        "entityId": entity.external_id,
        "score": round(float(entity.score), 4),
        "coverHash": cover.sha256,
        "verifiedAt": verified_at,
        "resolvedTitle": canonical_title,
        "resolvedAuthor": canonical_author,
    }
    if source_published_at:
        verification["sourcePublishedAt"] = source_published_at
    verification["sourceDescription"] = factual_description

    manifest = {
        "cacheVersion": CACHE_VERSION,
        "type": resolved_type,
        "title": canonical_title,
        "titleNormalized": _normalise_text(canonical_title),
        "canonicalLink": _canonicalize_url(entity.link),
        "entityId": entity.external_id,
        "source": entity.source,
        "score": round(float(entity.score), 4),
        "coverHash": cover.sha256,
        "coverSourceUrl": cover.source_url,
        "coverSourceMime": cover.source_mime,
        "width": cover.width,
        "height": cover.height,
        "verifiedAt": verified_at,
        "resolvedTitle": entity.resolved_title,
        "resolvedAuthor": entity.resolved_author,
        "isbn": entity.isbn,
        "sourcePublishedAt": source_published_at,
        "expiryDate": derived_expiry,
    }

    try:
        _atomic_write(image_path, cover.data)
        _atomic_write(
            _manifest_path(image_path),
            json.dumps(
                manifest, ensure_ascii=False, indent=2, sort_keys=True
            ).encode("utf-8"),
        )
    except OSError as exc:
        raise RecommendationResolutionError(
            "CACHE_WRITE_FAILED",
            f"Não foi possível guardar capa verificada: {exc}",
            item=item,
        ) from exc

    result = dict(item)
    result.update(
        {
            "type": resolved_type,
            "title": canonical_title,
            "authorOrMeta": canonical_author,
            "description": factual_description,
            "link": _canonicalize_url(entity.link),
            "imageUrl": public_image_url,
            "externalId": entity.external_id,
            "resolutionStatus": "verified",
            "verification": verification,
        }
    )
    if source_published_at:
        result["sourcePublishedAt"] = source_published_at
    if derived_expiry:
        result["expiryDate"] = derived_expiry
    return result


__all__ = [
    "RecommendationResolutionError",
    "ResolutionError",
    "discover_highlights",
    "discover_podcast_episodes",
    "is_eligible_highlight",
    "load_cover_for_item",
    "probe_verified_source",
    "resolve_recommendation",
    "validate_cached_cover",
    "_cache_key",
]
