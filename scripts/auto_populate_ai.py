"""
Populate the recommendation pool from real source entities.

The language model proposes only book/movie catalogue candidates. Every
candidate is resolved against a structured or first-party source before it can
enter the queue. Podcast episodes come from the watchlist RSS feeds and weekly
highlights come from trusted news/YouTube search results. No unresolved title,
generic image or empty link is ever admitted as publishable.
"""

import datetime
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from statistics import median
from urllib.parse import urlparse

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
REC_FILE = os.path.join(ROOT_DIR, "website", "public", "recommendations.json")
WATCHLIST_FILE = os.path.join(ROOT_DIR, "website", "public", "watchlist.json")

sys.path.insert(0, SCRIPT_DIR)
from recommendation_resolver import (
    ResolutionError,
    is_eligible_highlight,
    resolve_recommendation,
)


TARGET_PER_TYPE = 4
MIN_TIME_SENSITIVE_VALIDITY_HOURS = 24
REQUEST_TIMEOUT = 20
PODCAST_DESCRIPTION_VERSION = 1
PODCAST_DESCRIPTION_MIN_CHARS = 70
PODCAST_DESCRIPTION_MAX_CHARS = 138
PODCAST_SUMMARY_MODELS = (
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
)
ALLOWED_TYPES = (
    "book",
    "podcast",
    "movie",
    "nostalgia",
    "investigation",
    "highlight",
)
CATEGORIES = {
    "book": "Livro",
    "podcast": "Podcast",
    "movie": "Filme",
    "nostalgia": "Nostalgia",
    "investigation": "Investigação",
    "highlight": "Destaque",
}

# This catalogue reserve is made of stable, well-known entities that have
# passed the live resolver. They are still revalidated on every insertion, and
# history prevents a previously used work from returning.
VERIFIED_CATALOGUE_CANDIDATES = (
    {"type": "book", "title": "1984", "authorOrMeta": "George Orwell"},
    {
        "type": "book",
        "title": "The Road to Serfdom",
        "authorOrMeta": "Friedrich Hayek",
    },
    {
        "type": "book",
        "title": "Why Nations Fail",
        "authorOrMeta": "Daron Acemoglu",
    },
    {
        "type": "book",
        "title": "The Origins of Totalitarianism",
        "authorOrMeta": "Hannah Arendt",
    },
    {"type": "book", "title": "Animal Farm", "authorOrMeta": "George Orwell"},
    {
        "type": "book",
        "title": "Democracy in America",
        "authorOrMeta": "Alexis de Tocqueville",
    },
    {
        "type": "book",
        "title": "The Open Society and Its Enemies",
        "authorOrMeta": "Karl Popper",
    },
    {
        "type": "book",
        "title": "How Democracies Die",
        "authorOrMeta": "Steven Levitsky",
    },
    {
        "type": "book",
        "title": "The Shock Doctrine",
        "authorOrMeta": "Naomi Klein",
    },
    {"type": "book", "title": "The Republic", "authorOrMeta": "Plato"},
    {"type": "book", "title": "Capital", "authorOrMeta": "Karl Marx"},
    {
        "type": "book",
        "title": "On Liberty",
        "authorOrMeta": "John Stuart Mill",
    },
    {
        "type": "book",
        "title": "The Prince",
        "authorOrMeta": "Niccolò Machiavelli",
    },
    {
        "type": "movie",
        "title": "The Great Dictator",
        "authorOrMeta": "Charlie Chaplin",
    },
    {
        "type": "movie",
        "title": "Dr. Strangelove",
        "authorOrMeta": "Stanley Kubrick",
    },
    {
        "type": "movie",
        "title": "V for Vendetta",
        "authorOrMeta": "James McTeigue",
    },
    {
        "type": "movie",
        "title": "The Death of Stalin",
        "authorOrMeta": "Armando Iannucci",
    },
    {
        "type": "movie",
        "title": "Frost/Nixon",
        "authorOrMeta": "Ron Howard",
    },
    {
        "type": "movie",
        "title": "The Manchurian Candidate",
        "authorOrMeta": "John Frankenheimer",
    },
    {
        "type": "movie",
        "title": "The Battle of Algiers",
        "authorOrMeta": "Gillo Pontecorvo",
    },
    {"type": "movie", "title": "Milk", "authorOrMeta": "Gus Van Sant"},
    {
        "type": "movie",
        "title": "Darkest Hour",
        "authorOrMeta": "Joe Wright",
    },
    {"type": "movie", "title": "Malcolm X", "authorOrMeta": "Spike Lee"},
    {"type": "movie", "title": "Selma", "authorOrMeta": "Ava DuVernay"},
    {"type": "movie", "title": "Nixon", "authorOrMeta": "Oliver Stone"},
    {
        "type": "movie",
        "title": "The Last King of Scotland",
        "authorOrMeta": "Kevin Macdonald",
    },
    {
        "type": "movie",
        "title": "Argentina, 1985",
        "authorOrMeta": "Santiago Mitre",
    },
)

CATALOGUE_DESCRIPTIONS = {
    ("book", "1984"): (
        "Distopia sobre vigilância, propaganda e controlo político numa sociedade "
        "onde até a linguagem é usada para limitar o pensamento."
    ),
    ("book", "The Road to Serfdom"): (
        "Ensaio clássico sobre liberdade económica, planeamento estatal e os riscos "
        "políticos da concentração de poder."
    ),
    ("book", "Why Nations Fail"): (
        "Análise sobre instituições políticas e económicas, e sobre porque alguns "
        "países prosperam enquanto outros ficam presos à desigualdade."
    ),
    ("book", "The Origins of Totalitarianism"): (
        "Estudo sobre antissemitismo, imperialismo e regimes totalitários no século XX."
    ),
    ("book", "Animal Farm"): (
        "Fábula política sobre revolução, poder e a forma como ideais libertadores "
        "podem ser capturados por novas elites."
    ),
    ("book", "Democracy in America"): (
        "Observação fundamental sobre democracia, igualdade, costumes cívicos e "
        "instituições nos Estados Unidos."
    ),
    ("book", "The Open Society and Its Enemies"): (
        "Defesa da sociedade aberta contra projetos políticos fechados, autoritários "
        "e hostis ao pluralismo."
    ),
    ("book", "How Democracies Die"): (
        "Explica como democracias podem degradar-se por dentro através de normas "
        "quebradas, polarização e abuso institucional."
    ),
    ("book", "The Shock Doctrine"): (
        "Investigação sobre crises políticas e económicas usadas para impor reformas "
        "radicais com pouco escrutínio democrático."
    ),
    ("book", "The Republic"): (
        "Diálogo filosófico sobre justiça, educação, poder e a organização ideal da cidade."
    ),
    ("book", "Capital"): (
        "Crítica da economia política centrada no trabalho, no capital e nas dinâmicas "
        "de exploração do capitalismo."
    ),
    ("book", "On Liberty"): (
        "Defesa influente da liberdade individual, do debate público e dos limites do "
        "poder social e estatal."
    ),
    ("book", "The Prince"): (
        "Tratado sobre poder, estratégia e sobrevivência política, ainda útil para "
        "ler decisões de liderança."
    ),
    ("movie", "The Great Dictator"): (
        "Sátira política ao fascismo e ao culto do líder, com humor e crítica moral "
        "ao autoritarismo."
    ),
    ("movie", "Dr. Strangelove"): (
        "Comédia negra sobre guerra nuclear, paranoia militar e decisões absurdas "
        "em tempos de tensão geopolítica."
    ),
    ("movie", "V for Vendetta"): (
        "Distopia sobre resistência civil, medo, propaganda e poder do Estado sobre "
        "as liberdades individuais."
    ),
    ("movie", "The Death of Stalin"): (
        "Sátira sobre sucessão política, medo e luta pelo poder no círculo dirigente soviético."
    ),
    ("movie", "Frost/Nixon"): (
        "Drama político sobre entrevistas que expõem responsabilidade, poder mediático "
        "e memória pública."
    ),
    ("movie", "The Manchurian Candidate"): (
        "Thriller sobre manipulação política, conspiração e medo ideológico durante "
        "a Guerra Fria."
    ),
    ("movie", "The Battle of Algiers"): (
        "Retrato duro de colonialismo, resistência, repressão e guerra urbana pela independência."
    ),
    ("movie", "Milk"): (
        "Biografia política de Harvey Milk e da mobilização pelos direitos LGBT nos Estados Unidos."
    ),
    ("movie", "Darkest Hour"): (
        "Drama sobre liderança, pressão parlamentar e decisões críticas no início "
        "da Segunda Guerra Mundial."
    ),
    ("movie", "Malcolm X"): (
        "Biografia sobre identidade, racismo, mobilização política e transformação "
        "do pensamento de Malcolm X."
    ),
    ("movie", "Selma"): (
        "Retrato da campanha pelo direito de voto e da luta pelos direitos civis nos EUA."
    ),
    ("movie", "Nixon"): (
        "Drama político sobre poder presidencial, ambição, paranoia e queda institucional."
    ),
    ("movie", "The Last King of Scotland"): (
        "Drama sobre ditadura, cumplicidade e violência política no Uganda de Idi Amin."
    ),
    ("movie", "Argentina, 1985"): (
        "Drama judicial sobre memória, justiça e responsabilização após a ditadura argentina."
    ),
}

VERIFIED_CATALOGUE_CANDIDATES = tuple(
    {
        **candidate,
        "description": CATALOGUE_DESCRIPTIONS.get(
            (candidate["type"], candidate["title"]),
            candidate.get("description", ""),
        ),
    }
    for candidate in VERIFIED_CATALOGUE_CANDIDATES
)

TRUSTED_HIGHLIGHT_DOMAINS = (
    "expresso.pt",
    "publico.pt",
    "observador.pt",
    "rtp.pt",
    "sicnoticias.pt",
    "cnnportugal.iol.pt",
    "tvi.iol.pt",
    "dn.pt",
    "jornaldenegocios.pt",
    "eco.sapo.pt",
    "rr.pt",
    "tsf.pt",
    "youtube.com",
    "youtu.be",
)

DEFAULT_HIGHLIGHT_RSS_FEEDS = (
    "https://feeds.feedburner.com/PublicoRSS",
    "https://www.rtp.pt/noticias/rss",
    "https://www.rtp.pt/noticias/rss/pais",
    "https://observador.pt/feed/",
)

HEADERS = {
    "User-Agent": "Politometro/2.0 (+https://politometro.pt)",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.5",
}


def _normalise(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _title_key(media_type, title):
    return f"{media_type}:{_normalise(title)}"


def _similarity(left, right):
    a = _normalise(left)
    b = _normalise(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _clean_source_text(value, max_chars=360):
    value = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_chars:
        return value
    shortened = value[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:.!?")
    return shortened


_PODCAST_DESCRIPTION_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "agora",
    "ainda",
    "ano",
    "com",
    "como",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "entre",
    "esta",
    "este",
    "foi",
    "há",
    "mais",
    "mas",
    "muitas",
    "muitos",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "outros",
    "ou",
    "para",
    "pela",
    "pelo",
    "por",
    "que",
    "já",
    "não",
    "se",
    "sem",
    "ser",
    "sobre",
    "um",
    "uma",
}


def _clean_podcast_source_text(value):
    """Remove player boilerplate while preserving the publisher's evidence."""
    text = _clean_source_text(value, max_chars=600)
    boilerplate_patterns = (
        r"\s*see\s+omnystudio\.com/listener.*$",
        r"\s*for\s+privacy\s+information.*$",
        r"\s*consulte\s+a\s+pol[ií]tica\s+de\s+privacidade.*$",
    )
    for pattern in boilerplate_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-")


def _podcast_description_tokens(value):
    return {
        token
        for token in _normalise(value).split()
        if len(token) >= 3 and token not in _PODCAST_DESCRIPTION_STOPWORDS
    }


def _podcast_description_is_valid(description, title, source_description):
    text = re.sub(r"\s+", " ", str(description or "")).strip()
    if not (
        PODCAST_DESCRIPTION_MIN_CHARS
        <= len(text)
        <= PODCAST_DESCRIPTION_MAX_CHARS
    ):
        return False
    if len(text.split()) < 10 or "…" in text or "..." in text:
        return False
    if text[-1:] not in ".!?":
        return False

    normalised = _normalise(text)
    if not normalised.startswith("analise sobre "):
        return False
    poor_patterns = (
        r"\bsee omnystudio\b",
        r"\bprivacy information\b",
        r"\bna noite em que (?:este|o) programa\b",
        r"\b(?:hoje|amanha|ontem)\b",
        r"\b(?:neste|nesse) (?:episodio|programa)\b",
        r"\b(?:ouve|escuta|subscreve|nao percas|saiba mais)\b",
    )
    if any(re.search(pattern, normalised) for pattern in poor_patterns):
        return False
    if re.match(r"^com .+\bmoderacao de\b", normalised):
        return False

    evidence = f"{title} {source_description}"
    evidence_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", evidence))
    summary_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", text))
    if not summary_numbers.issubset(evidence_numbers):
        return False

    summary_tokens = _podcast_description_tokens(text)
    evidence_tokens = _podcast_description_tokens(evidence)
    shared = summary_tokens & evidence_tokens
    minimum_shared = min(4, max(2, len(summary_tokens) // 4))
    if len(_podcast_description_tokens(title)) == 1:
        minimum_shared = 1
    if len(shared) < minimum_shared:
        return False

    # A useful card must add concrete information from the source rather than
    # merely paraphrasing the episode title.
    title_tokens = _podcast_description_tokens(title)
    source_tokens = _podcast_description_tokens(source_description)
    source_details = (summary_tokens & source_tokens) - title_tokens
    return len(source_details) >= 3


def _podcast_sentence_candidates(source_description):
    source = _clean_podcast_source_text(source_description)
    source = re.sub(
        r"\b(?:mas\s+)?na noite em que (?:este|o) programa "
        r"(?:é|foi) emitido,?\s*",
        "",
        source,
        flags=re.IGNORECASE,
    )
    candidates = []
    for raw in re.split(r"(?<=[.!?])\s+", source):
        sentence = re.sub(
            r"^(?:mas|contudo|porém|entretanto)\s*,?\s*",
            "",
            raw.strip(),
            flags=re.IGNORECASE,
        )
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if len(sentence) < 25:
            continue
        sentence = sentence[:1].upper() + sentence[1:]
        if sentence[-1:] not in ".!?":
            sentence += "."
        if "…" not in sentence and "..." not in sentence:
            candidates.append(sentence)
    return candidates


def _extractive_podcast_description(title, source_description):
    """Build a grounded, complete fallback without inventing any facts."""
    title_tokens = _podcast_description_tokens(title)
    ranked = []
    for index, sentence in enumerate(
        _podcast_sentence_candidates(source_description)
    ):
        sentence_tokens = _podcast_description_tokens(sentence)
        overlap = len(title_tokens & sentence_tokens)
        information = min(len(sentence_tokens), 12)
        ranked.append((overlap * 5 + information, -index, index, sentence))

    selected = []
    total = 0
    for _, _, index, sentence in sorted(ranked, reverse=True):
        extra = len(sentence) + (1 if selected else 0)
        if total + extra <= PODCAST_DESCRIPTION_MAX_CHARS:
            selected.append((index, sentence))
            total += extra
        if total >= PODCAST_DESCRIPTION_MIN_CHARS:
            break
    if selected:
        details = " ".join(
            sentence for _, sentence in sorted(selected, key=lambda entry: entry[0])
        )
        summary = f"Análise sobre {details[:1].lower() + details[1:]}"
        if _podcast_description_is_valid(summary, title, source_description):
            return summary

    participant_match = re.search(
        r"\bCom\s+(.+?)\.\s*Moderaç[aã]o\s+de\s+(.+?)(?:\.|$)",
        _clean_podcast_source_text(source_description),
        flags=re.IGNORECASE,
    )
    if participant_match:
        participants = participant_match.group(1).strip(" ,;:.")
        moderator = participant_match.group(2).strip(" ,;:.")
        summary = (
            f"Análise sobre {str(title or '').strip().rstrip(' .!?').lower()}, "
            f"com {participants}, moderado por {moderator}."
        )
        if len(summary) <= PODCAST_DESCRIPTION_MAX_CHARS and (
            _podcast_description_is_valid(
                summary, title, source_description
            )
        ):
            return summary

    clean_title = re.sub(r'^[“"«]+|[”"»]+$', "", str(title or "").strip())
    clean_title = clean_title.rstrip(" .!?")
    if clean_title:
        summary = f"Análise sobre os temas e questões de {clean_title.lower()}."
        if len(summary) < PODCAST_DESCRIPTION_MIN_CHARS:
            summary = (
                "Análise sobre as principais questões políticas e sociais "
                f"relacionadas com {clean_title.lower()}."
            )
        if len(summary) > PODCAST_DESCRIPTION_MAX_CHARS:
            shortened = summary[:PODCAST_DESCRIPTION_MAX_CHARS].rsplit(" ", 1)[0]
            summary = shortened.rstrip(" ,;:.!?") + "."
        return summary
    return "Análise sobre os principais temas políticos e sociais apresentados pela fonte."


def _request_podcast_editorial_description(
    api_key,
    *,
    title,
    show,
    source_description,
):
    if not api_key:
        return ""
    system_prompt = (
        "És editor do Politómetro. Mantém o título original fora da resposta. "
        "Escreve uma única frase informativa em português de Portugal, entre "
        "70 e 138 caracteres, que revele detalhes concretos do conteúdo que "
        "não estejam já explícitos no título — acontecimentos, argumentos, "
        "consequências ou questões debatidas. Usa apenas factos presentes no "
        "resumo da fonte. Não reformules nem resumas apenas o título. Não "
        "acrescentes contexto, nomes, números ou conclusões. Não uses "
        "A frase tem de começar por \"Análise sobre\". Não uses reticências, "
        "apelos, linguagem promocional, \"neste episódio\", "
        "\"este programa\", \"hoje\", \"ontem\" ou \"amanhã\", nem listes "
        "apenas participantes. Devolve apenas JSON com a chave description."
    )
    evidence = {
        "title": str(title or "").strip(),
        "podcast": str(show or "").strip(),
        "sourceDescription": _clean_podcast_source_text(source_description),
    }
    for model in PODCAST_SUMMARY_MODELS:
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(evidence, ensure_ascii=False),
                        },
                    ],
                    "temperature": 0.05,
                    "response_format": {"type": "json_object"},
                },
                timeout=35,
            )
            if not response.ok:
                print(
                    f"  [PODCAST COPY/{model}] HTTP {response.status_code}"
                )
                continue
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            description = _clean_source_text(
                parsed.get("description"), PODCAST_DESCRIPTION_MAX_CHARS
            )
            if _podcast_description_is_valid(
                description, title, source_description
            ):
                return description
            print(
                f"  [PODCAST COPY/{model}] "
                "Descrição rejeitada pelo controlo editorial."
            )
        except (
            requests.RequestException,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            print(f"  [PODCAST COPY/{model}] {exc}")
    return ""


def _editorialize_podcast_item(item, api_key):
    if (
        item.get("type") != "podcast"
        or item.get("status") != "queue"
        or item.get("resolutionStatus") != "verified"
    ):
        return False
    verification = item.get("verification")
    if not isinstance(verification, dict):
        return False

    source_description = _clean_podcast_source_text(
        verification.get("sourceDescription") or item.get("description")
    )
    title = str(item.get("title") or "").strip()
    if not source_description or not title:
        return False
    source_hash = hashlib.sha256(
        json.dumps(
            {"title": title, "sourceDescription": source_description},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    existing = str(verification.get("editorialDescription") or "").strip()
    if (
        verification.get("editorialDescriptionVersion")
        == PODCAST_DESCRIPTION_VERSION
        and verification.get("editorialSourceHash") == source_hash
        and _podcast_description_is_valid(existing, title, source_description)
    ):
        changed = item.get("description") != existing
        item["description"] = existing
        return changed

    generated = _request_podcast_editorial_description(
        api_key,
        title=title,
        show=item.get("sourceSeriesTitle") or item.get("authorOrMeta"),
        source_description=source_description,
    )
    description = generated or _extractive_podcast_description(
        title, source_description
    )
    metadata_changed = any(
        verification.get(key) != value
        for key, value in (
            ("editorialDescription", description),
            ("editorialDescriptionVersion", PODCAST_DESCRIPTION_VERSION),
            ("editorialSourceHash", source_hash),
            (
                "editorialDescriptionProvider",
                "groq" if generated else "extractive",
            ),
        )
    )
    changed = item.get("description") != description
    item["description"] = description
    verification["editorialDescription"] = description
    verification["editorialDescriptionVersion"] = PODCAST_DESCRIPTION_VERSION
    verification["editorialSourceHash"] = source_hash
    verification["editorialDescriptionProvider"] = (
        "groq" if generated else "extractive"
    )
    return changed or metadata_changed


def _refresh_podcast_editorial_descriptions(queue, api_key):
    changed = False
    for item in queue:
        changed |= _editorialize_podcast_item(item, api_key)
    return changed


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    else:
        text = str(value).strip()
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _iso_datetime(value):
    return value.astimezone(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _is_expired(item, now=None):
    expiry = _parse_datetime(item.get("expiryDate"))
    return bool(expiry and expiry <= (now or datetime.datetime.now(datetime.timezone.utc)))


def _new_id(prefix, identity):
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _load_database():
    if not os.path.exists(REC_FILE):
        return {"queue": [], "history": []}
    with open(REC_FILE, "r", encoding="utf-8") as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        return {"queue": [], "history": []}
    parsed.setdefault("queue", [])
    parsed.setdefault("history", [])
    return parsed


def _write_database(data):
    tmp_path = REC_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp_path, REC_FILE)


def _identity_sets(items, include_cover_hashes=True):
    titles = set()
    links = set()
    external_ids = set()
    cover_hashes = set()
    for item in items:
        title = _title_key(item.get("type"), item.get("title"))
        link = (item.get("link") or "").strip()
        external_id = item.get("externalId") or (
            item.get("verification") or {}
        ).get("entityId")
        cover_hash = (item.get("verification") or {}).get("coverHash")
        if title:
            titles.add(title)
        if link:
            links.add(link)
        if external_id:
            external_ids.add(str(external_id))
        if cover_hash and include_cover_hashes:
            cover_hashes.add(cover_hash)
    return titles, links, external_ids, cover_hashes


def _rss_text(element, local_name):
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1].lower() == local_name.lower():
            if child.text and child.text.strip():
                return child.text.strip()
    return ""


def _rss_image(item, channel, fallback):
    image_names = {"image", "thumbnail", "content"}
    for parent in (item, channel):
        for child in parent.iter():
            if child.tag.rsplit("}", 1)[-1].lower() not in image_names:
                continue
            candidate = (
                child.attrib.get("href")
                or child.attrib.get("url")
                or (child.text or "").strip()
            )
            if candidate.startswith("http"):
                return candidate
    for parent in (item, channel):
        for child in parent.iter():
            text = child.text or ""
            match = re.search(
                r"<img[^>]+src=[\"']([^\"']+)",
                html.unescape(text),
                flags=re.IGNORECASE,
            )
            if match and match.group(1).startswith("http"):
                return match.group(1)
    return fallback


def _rss_date(item):
    for field in ("pubDate", "published", "updated", "date"):
        parsed = _parse_datetime(_rss_text(item, field))
        if parsed:
            return parsed
    return None


def _podcast_freshness_days(episodes):
    """Infer a short validity window from the feed's actual publication cadence."""
    dates = sorted(
        (published for published in (_rss_date(item) for item in episodes[:12]) if published),
        reverse=True,
    )
    gaps = [
        (dates[index] - dates[index + 1]).total_seconds() / 86400
        for index in range(len(dates) - 1)
        if dates[index] > dates[index + 1]
    ]
    cadence_days = median(gaps) if gaps else 7
    if cadence_days <= 2:
        return 3
    if cadence_days <= 9:
        return 10
    if cadence_days <= 18:
        return 21
    return 35


def _apple_show(show):
    collection_id = str(show.get("appleCollectionId") or "").strip()
    feed_url = str(show.get("feedUrl") or "").strip()
    if collection_id:
        response = requests.get(
            "https://itunes.apple.com/lookup",
            params={
                "id": collection_id,
                "media": "podcast",
                "entity": "podcast",
                "country": "PT",
            },
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        exact = next(
            (
                candidate
                for candidate in results
                if str(
                    candidate.get("collectionId")
                    or candidate.get("trackId")
                    or ""
                )
                == collection_id
            ),
            None,
        )
        if exact:
            if feed_url and not exact.get("feedUrl"):
                exact["feedUrl"] = feed_url
            return exact
    if feed_url:
        return {
            "collectionName": show.get("name", ""),
            "artistName": show.get("author", ""),
            "feedUrl": feed_url,
            "collectionId": collection_id,
            "artworkUrl600": show.get("imageUrl", ""),
            "collectionViewUrl": show.get("link", ""),
        }

    response = requests.get(
        "https://itunes.apple.com/search",
        params={
            "term": f"{show.get('name', '')} {show.get('author', '')}",
            "entity": "podcast",
            "limit": 25,
            "country": "PT",
        },
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    candidates = response.json().get("results", [])
    ranked = []
    for candidate in candidates:
        name_score = _similarity(show.get("name"), candidate.get("collectionName"))
        author_score = _similarity(show.get("author"), candidate.get("artistName"))
        ranked.append((name_score * 0.8 + author_score * 0.2, candidate))
    ranked.sort(key=lambda entry: entry[0], reverse=True)
    if not ranked or ranked[0][0] < 0.72:
        return None
    return ranked[0][1]


def discover_podcast_candidates(watchlist, seen_titles, seen_ids, limit):
    """Return recent, real episodes from distinct watchlist RSS feeds."""
    discovered = []
    for show in watchlist.get("podcasts", []):
        if len(discovered) >= limit:
            break
        try:
            apple = _apple_show(show)
            if not apple or not apple.get("feedUrl"):
                print(f"  [RSS] Feed não encontrado para {show.get('name')}")
                continue
            response = requests.get(
                apple["feedUrl"],
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            channel = next(
                (
                    node
                    for node in root.iter()
                    if node.tag.rsplit("}", 1)[-1].lower() == "channel"
                ),
                root,
            )
            episodes = [
                node
                for node in channel.iter()
                if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
            ]
            episodes.sort(
                key=lambda episode: _rss_date(episode)
                or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
                reverse=True,
            )
            freshness_days = _podcast_freshness_days(episodes)
            now = datetime.datetime.now(datetime.timezone.utc)
            fallback_image = (
                apple.get("artworkUrl600")
                or apple.get("artworkUrl100")
                or show.get("imageUrl", "")
            )
            for episode in episodes[:20]:
                published_at = _rss_date(episode)
                if not published_at:
                    # A podcast episode without a source date cannot be kept fresh
                    # automatically, so it is not safe for the autonomous pool.
                    continue
                expiry_at = published_at + datetime.timedelta(days=freshness_days)
                if expiry_at <= now:
                    break
                title = _rss_text(episode, "title")
                link = _rss_text(episode, "link")
                guid = _rss_text(episode, "guid") or link
                if not link and guid.startswith("http"):
                    link = guid
                if (
                    not title
                    or not link.startswith("http")
                ):
                    continue
                if str(guid) in seen_ids:
                    # The newest episode was already published/queued; do not
                    # backfill an older episode from the same recurring show.
                    break
                description = _clean_source_text(
                    _rss_text(episode, "description")
                    or _rss_text(episode, "summary")
                )
                discovered.append(
                    {
                        "id": _new_id("rss_podcast", guid),
                        "type": "podcast",
                        "category": "Podcast",
                        "title": title,
                        "authorOrMeta": (
                            f"{show.get('name', apple.get('collectionName', 'Podcast'))}"
                            f" / {show.get('author', apple.get('artistName', ''))}"
                        ).strip(" /"),
                        "description": description
                        or f"Episódio de {show.get('name', 'um podcast português')} "
                        "sobre temas de atualidade.",
                        "imageUrl": _rss_image(
                            episode, channel, fallback_image
                        ),
                        "link": link,
                        "externalId": str(guid),
                        "sourceSeriesId": str(
                            apple.get("collectionId") or apple["feedUrl"]
                        ),
                        "sourceSeriesTitle": str(
                            show.get("name")
                            or apple.get("collectionName")
                            or ""
                        ),
                        "priority": 4,
                        "sourcePublishedAt": _iso_datetime(published_at),
                        "expiryDate": _iso_datetime(expiry_at),
                        "createdAt": _utc_now(),
                        "status": "queue",
                        "sourceHint": "podcast-rss",
                        "_discovery": {
                            "kind": "rss",
                            "feedUrl": apple["feedUrl"],
                            "guid": str(guid),
                            "imageUrl": _rss_image(
                                episode, channel, fallback_image
                            ),
                            "show": str(
                                show.get("name")
                                or apple.get("collectionName")
                                or ""
                            ),
                        },
                    }
                )
                # One episode from each show keeps the pool diverse.
                break
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            print(f"  [RSS] {show.get('name', 'Podcast')}: {exc}")
    return discovered


def _cse_search(query, api_key, engine_id, num=10):
    response = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={
            "key": api_key,
            "cx": engine_id,
            "q": query,
            "num": min(num, 10),
            "dateRestrict": "m3",
            "lr": "lang_pt",
            "safe": "active",
        },
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("items", [])


def _publisher_from_link(link):
    hostname = (urlparse(link).hostname or "").lower().removeprefix("www.")
    labels = {
        "expresso.pt": "Expresso",
        "publico.pt": "PÚBLICO",
        "observador.pt": "Observador",
        "rtp.pt": "RTP",
        "sicnoticias.pt": "SIC Notícias",
        "cnnportugal.iol.pt": "CNN Portugal",
        "tvi.iol.pt": "TVI/CNN Portugal",
        "dn.pt": "Diário de Notícias",
        "jornaldenegocios.pt": "Jornal de Negócios",
        "eco.sapo.pt": "ECO",
        "rr.pt": "Renascença",
        "tsf.pt": "TSF",
        "youtube.com": "YouTube",
        "youtu.be": "YouTube",
    }
    for domain, label in labels.items():
        if hostname == domain or hostname.endswith("." + domain):
            return label
    return hostname


def discover_highlight_candidates(api_key, engine_id, seen_links, limit):
    """Discover editorial work, never ordinary news, on trusted publishers."""
    if not api_key or not engine_id:
        print("  [CSE] GOOGLE_CSE_API_KEY/GOOGLE_CSE_ID em falta.")
        return []

    site_clause = " OR ".join(f"site:{domain}" for domain in TRUSTED_HIGHLIGHT_DOMAINS)
    queries = (
        f'("opinião" OR "artigo de opinião" OR editorial OR "análise aprofundada") Portugal política ({site_clause})',
        f'("investigação" OR "grande reportagem") Portugal política ({site_clause})',
        f'("documentário" OR "reportagem especial") Portugal sociedade ({site_clause})',
        f'("corrupção" OR "transparência") investigação Portugal ({site_clause})',
    )

    candidates = []
    emitted_links = set()
    for query in queries:
        if len(candidates) >= limit * 3:
            break
        try:
            results = _cse_search(query, api_key, engine_id)
        except requests.RequestException as exc:
            print(f"  [CSE] Pesquisa falhou: {exc}")
            continue
        for result in results:
            link = (result.get("link") or "").strip()
            hostname = (urlparse(link).hostname or "").lower()
            if (
                not link.startswith("http")
                or link in seen_links
                or link in emitted_links
                or "wikipedia.org" in hostname
            ):
                continue
            if not any(
                hostname == domain or hostname.endswith("." + domain)
                for domain in TRUSTED_HIGHLIGHT_DOMAINS
            ):
                continue
            title = _clean_source_text(result.get("title"), max_chars=180)
            if not title:
                continue
            description = _clean_source_text(result.get("snippet"))
            if not is_eligible_highlight(
                title=title,
                description=description,
                link=link,
            ):
                continue
            pagemap = result.get("pagemap") or {}
            images = pagemap.get("cse_image") or []
            image_url = images[0].get("src", "") if images else ""
            metatags = (pagemap.get("metatags") or [{}])[0]
            published_at = None
            for field in (
                "article:published_time",
                "og:published_time",
                "datepublished",
                "datePublished",
                "date",
                "parsely-pub-date",
            ):
                published_at = _parse_datetime(metatags.get(field))
                if published_at:
                    break
            now = datetime.datetime.now(datetime.timezone.utc)
            # Investigations retain value longer than recurring episodes, but
            # missing date evidence gets only a short provisional window.
            expiry_at = (
                published_at + datetime.timedelta(days=60)
                if published_at
                else now + datetime.timedelta(days=14)
            )
            if expiry_at <= now:
                continue
            candidates.append(
                {
                    "id": _new_id("cse_highlight", link),
                    "type": "highlight",
                    "category": "Destaque",
                    "title": title,
                    "authorOrMeta": _publisher_from_link(link),
                    "description": description,
                    "imageUrl": image_url,
                    "link": link,
                    "externalId": link,
                    "priority": 4,
                    "sourcePublishedAt": (
                        _iso_datetime(published_at) if published_at else None
                    ),
                    "expiryDate": _iso_datetime(expiry_at),
                    "createdAt": _utc_now(),
                    "status": "queue",
                    "sourceHint": "trusted-cse",
                }
            )
            emitted_links.add(link)
    return candidates


def discover_rss_highlight_candidates(seen_links, limit):
    """Discover recent editorial work without depending on a search API key."""
    candidates = []
    emitted_links = set()
    now = datetime.datetime.now(datetime.timezone.utc)
    topic_markers = (
        "política",
        "governo",
        "parlamento",
        "presidente",
        "eleições",
        "partido",
        "democracia",
        "economia",
        "justiça",
        "corrupção",
        "investigação",
        "reportagem",
        "entrevista",
        "análise",
        "explicador",
        "sociedade",
        "educação",
        "saúde",
        "habitação",
        "união europeia",
    )
    for feed_url in DEFAULT_HIGHLIGHT_RSS_FEEDS:
        try:
            response = requests.get(
                feed_url, headers=HEADERS, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            print(f"  [RSS/highlight] {feed_url}: {exc}")
            continue
        channel = next(
            (
                node
                for node in root.iter()
                if node.tag.rsplit("}", 1)[-1].lower()
                in {"channel", "feed"}
            ),
            root,
        )
        entries = [
            node
            for node in channel.iter()
            if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
        ]
        entries.sort(
            key=lambda entry: _rss_date(entry)
            or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            reverse=True,
        )
        for entry in entries[:30]:
            title = _clean_source_text(_rss_text(entry, "title"), 180)
            link = _rss_text(entry, "link")
            published_at = _rss_date(entry)
            if (
                not title
                or not link.startswith("http")
                or link in seen_links
                or link in emitted_links
                or not published_at
                or published_at > now + datetime.timedelta(minutes=10)
                or published_at < now - datetime.timedelta(days=14)
            ):
                continue
            hostname = (urlparse(link).hostname or "").lower()
            if not any(
                hostname == domain or hostname.endswith("." + domain)
                for domain in TRUSTED_HIGHLIGHT_DOMAINS
            ):
                continue
            if (
                re.match(r"^\s*\d{1,2}h(?:[.:]|\s)", title, re.IGNORECASE)
                or "/noticiario/" in urlparse(link).path.lower()
            ):
                # Hourly bulletins are valid news links but not substantial
                # enough for the weekly highlight quadrant.
                continue
            expiry_at = published_at + datetime.timedelta(days=30)
            description = _clean_source_text(
                _rss_text(entry, "description")
                or _rss_text(entry, "summary")
            )
            categories = [
                (
                    node.attrib.get("term")
                    or node.attrib.get("label")
                    or node.text
                    or ""
                ).strip()
                for node in entry.iter()
                if node.tag.rsplit("}", 1)[-1].lower() == "category"
            ]
            if not is_eligible_highlight(
                title=title,
                description=description,
                link=link,
                categories=categories,
            ):
                continue
            searchable = _normalise(f"{title} {description}")
            topic_score = sum(
                _normalise(marker) in searchable for marker in topic_markers
            )
            if topic_score == 0:
                continue
            image_url = _rss_image(entry, channel, "")
            candidates.append(
                {
                    "id": _new_id("rss_highlight", link),
                    "type": "highlight",
                    "category": "Destaque",
                    "title": title,
                    "authorOrMeta": _publisher_from_link(link),
                    "description": description,
                    "imageUrl": image_url,
                    "link": link,
                    "externalId": link,
                    "priority": 4,
                    "sourcePublishedAt": _iso_datetime(published_at),
                    "expiryDate": _iso_datetime(expiry_at),
                    "createdAt": _utc_now(),
                    "status": "queue",
                    "sourceHint": "trusted-rss",
                    "_discovery": {
                        "kind": "rss-highlight",
                        "feedUrl": feed_url,
                        "guid": _rss_text(entry, "guid") or link,
                        "imageUrl": image_url,
                        "categories": categories,
                    },
                    "_topicScore": topic_score,
                }
            )
            emitted_links.add(link)
    candidates.sort(
        key=lambda item: (
            int(item.get("_topicScore", 0)),
            _parse_datetime(item.get("sourcePublishedAt"))
            or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
        ),
        reverse=True,
    )
    for candidate in candidates:
        candidate.pop("_topicScore", None)
    return candidates[:limit]


def _groq_catalogue_candidates(api_key, excluded_titles, attempt):
    """Ask the model for catalogue leads, never for unverified final records."""
    if not api_key:
        return []
    exclusions = sorted(excluded_titles)[-200:]
    system_prompt = f"""
És um curador de catálogo especializado em cultura política e história.
Fornece candidatos que depois serão confirmados por catálogos externos. É proibido
inventar, traduzir livremente, fundir ou aproximar títulos.

Gera 20 candidatos:
- 10 livros reais;
- 10 filmes, documentários ou séries reais.

Regras:
- para livros, escolhe obras bem catalogadas no Open Library, com ISBN e capa,
  e usa o título original exato que consta desse catálogo;
- para filmes, escolhe obras com IMDb ID, realizador e imagem (P18) no Wikidata;
- usa o autor ou realizador exato;
- escolhe apenas obras cuja existência tens a certeza de conseguir confirmar;
- privilegia política, democracia, história, economia e sociedade, incluindo
  Portugal quando a obra estiver bem catalogada;
- não repitas estes títulos normalizados: {json.dumps(exclusions, ensure_ascii=False)};
- não incluas podcasts, artigos, links nem imagens;
- a descrição deve ter uma frase curta, sem datas ou factos incertos.

Responde apenas com um objeto JSON com a chave "candidates", contendo objetos:
{{"type":"book|movie","title":"...","authorOrMeta":"...","description":"..."}}.
""".strip()
    models = (
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
    )
    for model in models:
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": (
                                f"Produz o lote de candidatos verificáveis "
                                f"(tentativa {attempt})."
                            ),
                        },
                    ],
                    "temperature": 0.25,
                    "response_format": {"type": "json_object"},
                },
                timeout=45,
            )
            if not response.ok:
                print(f"  [Groq/{model}] HTTP {response.status_code}")
                continue
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            values = parsed.get("candidates", [])
            if isinstance(values, list):
                return values
        except (requests.RequestException, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(f"  [Groq/{model}] {exc}")
    return []


def _canonical_candidate(raw):
    media_type = raw.get("type")
    title = str(raw.get("title") or "").strip()
    author = str(raw.get("authorOrMeta") or "").strip()
    if media_type not in ("book", "movie") or not title or not author:
        return None
    return {
        "id": f"ai_{media_type}_{uuid.uuid4().hex[:16]}",
        "type": media_type,
        "category": CATEGORIES[media_type],
        "title": title,
        "authorOrMeta": author,
        "description": _clean_source_text(raw.get("description")),
        "imageUrl": "",
        "link": "",
        "priority": 3,
        "expiryDate": None,
        "createdAt": _utc_now(),
        "status": "queue",
        "sourceHint": "ai-catalogue-candidate",
    }


def _add_if_verified(
    candidate,
    queue,
    identities,
    needed_by_type,
    force=False,
    allow_when_full=False,
):
    titles, links, external_ids, cover_hashes = identities
    media_type = candidate.get("type")
    if needed_by_type.get(media_type, 0) <= 0 and not allow_when_full:
        return False
    title_key = _title_key(media_type, candidate.get("title"))
    if media_type != "podcast" and title_key in titles:
        return False

    try:
        resolved = resolve_recommendation(candidate, force=force)
    except ResolutionError as exc:
        print(f"  [REJECTED/{media_type}] {candidate.get('title')}: {exc}")
        return False
    except (requests.RequestException, OSError, ValueError) as exc:
        print(f"  [SOURCE ERROR/{media_type}] {candidate.get('title')}: {exc}")
        return False

    verification = resolved.get("verification") or {}
    link = (resolved.get("link") or "").strip()
    external_id = str(
        resolved.get("externalId") or verification.get("entityId") or ""
    )
    cover_hash = verification.get("coverHash")
    resolved["status"] = "queue"
    resolved["category"] = CATEGORIES[media_type]
    if (
        not _is_publishable_record(resolved)
        or link in links
        or (external_id and external_id in external_ids)
        or (cover_hash and cover_hash in cover_hashes)
    ):
        print(
            f"  [REJECTED/{media_type}] {candidate.get('title')}: "
            "evidência incompleta ou duplicada"
        )
        return False

    resolved.setdefault("createdAt", _utc_now())
    queue.append(resolved)
    titles.add(_title_key(media_type, resolved.get("title")))
    links.add(link)
    if external_id:
        external_ids.add(external_id)
    if cover_hash:
        cover_hashes.add(cover_hash)
    if needed_by_type.get(media_type, 0) > 0:
        needed_by_type[media_type] -= 1
    print(
        f"  [VERIFIED/{media_type}] {resolved.get('title')} -> "
        f"{verification.get('source', 'source')}"
    )
    return True


def _is_publishable_record(item):
    verification = item.get("verification") or {}
    time_sensitive = item.get("type") in {"podcast", "highlight"}
    source_published = _parse_datetime(item.get("sourcePublishedAt"))
    expiry = _parse_datetime(item.get("expiryDate"))
    temporal_contract = (
        bool(
            source_published
            and expiry
            and expiry > source_published
            and expiry
            > datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=MIN_TIME_SENSITIVE_VALIDITY_HOURS)
        )
        if time_sensitive
        else True
    )
    discovery = item.get("_discovery")
    editorial_contract = (
        is_eligible_highlight(
            title=item.get("title"),
            description=item.get("description"),
            link=item.get("link"),
            categories=(
                discovery.get("categories", [])
                if isinstance(discovery, dict)
                else []
            ),
        )
        if item.get("type") == "highlight"
        else True
    )
    return bool(
        item.get("status") == "queue"
        and item.get("resolutionStatus") == "verified"
        and verification.get("status") == "verified"
        and verification.get("entityId")
        and verification.get("coverHash")
        and str(item.get("link") or "").startswith(("http://", "https://"))
        and str(item.get("imageUrl") or "").startswith("/covers/")
        and bool(str(item.get("description") or "").strip())
        and temporal_contract
        and editorial_contract
        and not _is_expired(item)
    )


def _same_podcast_series(item, candidate):
    item_series = str(item.get("sourceSeriesId") or "")
    candidate_series = str(candidate.get("sourceSeriesId") or "")
    if item_series and candidate_series:
        return item_series == candidate_series
    item_title = _normalise(
        item.get("sourceSeriesTitle") or item.get("authorOrMeta")
    )
    candidate_title = _normalise(
        candidate.get("sourceSeriesTitle") or candidate.get("authorOrMeta")
    )
    return bool(
        item_title
        and candidate_title
        and (item_title in candidate_title or candidate_title in item_title)
    )


def _upsert_latest_podcast(candidate, queue, history, needed_by_type):
    """Replace an older episode of the same show only after resolving the new one."""
    same_series = [
        item
        for item in queue
        if item.get("type") == "podcast"
        and item.get("status") == "queue"
        and _same_podcast_series(item, candidate)
    ]
    historical_same_series = [
        item
        for item in history
        if item.get("type") == "podcast"
        and _same_podcast_series(item, candidate)
    ]
    candidate_external_id = str(candidate.get("externalId") or "")
    if candidate_external_id and any(
        str(item.get("externalId") or "") == candidate_external_id
        for item in same_series
    ):
        return False

    candidate_date = _parse_datetime(candidate.get("sourcePublishedAt"))
    if not candidate_date:
        return False
    newest_existing = max(
        (
            parsed
            for parsed in (
                _parse_datetime(item.get("sourcePublishedAt"))
                for item in same_series + historical_same_series
            )
            if parsed
        ),
        default=None,
    )
    # This source-series watermark prevents a published episode returning when
    # a feed later changes its GUID or canonical URL.
    if newest_existing and candidate_date <= newest_existing:
        return False

    try:
        resolved = resolve_recommendation(candidate, force=False)
    except (ResolutionError, requests.RequestException, OSError, ValueError) as exc:
        print(f"  [REJECTED/podcast] {candidate.get('title')}: {exc}")
        return False

    for field in (
        "sourceSeriesId",
        "sourceSeriesTitle",
        "sourcePublishedAt",
        "expiryDate",
    ):
        if candidate.get(field) and not resolved.get(field):
            resolved[field] = candidate[field]
    resolved["status"] = "queue"
    resolved["category"] = CATEGORIES["podcast"]

    remaining_queue = [item for item in queue if item not in same_series]
    _, all_links, all_external_ids, _ = _identity_sets(
        remaining_queue + history, include_cover_hashes=False
    )
    current_cover_hashes = _identity_sets(remaining_queue)[3]
    verification = resolved.get("verification") or {}
    link = str(resolved.get("link") or "")
    external_id = str(
        resolved.get("externalId") or verification.get("entityId") or ""
    )
    cover_hash = verification.get("coverHash")
    if (
        not _is_publishable_record(resolved)
        or link in all_links
        or (external_id and external_id in all_external_ids)
        or (cover_hash and cover_hash in current_cover_hashes)
    ):
        print(
            f"  [REJECTED/podcast] {candidate.get('title')}: "
            "evidência incompleta ou episódio duplicado"
        )
        return False

    queue[:] = remaining_queue
    queue.append(resolved)
    needed_by_type["podcast"] = max(
        0,
        TARGET_PER_TYPE
        - sum(_is_publishable_record(item) and item.get("type") == "podcast" for item in queue),
    )
    print(
        f"  [LATEST/podcast] {resolved.get('sourceSeriesTitle') or resolved.get('authorOrMeta')}: "
        f"{resolved.get('title')}"
    )
    return True


def _time_sensitive_sort_key(item):
    published = _parse_datetime(item.get("sourcePublishedAt"))
    created = _parse_datetime(item.get("createdAt"))
    timestamp = (published or created or datetime.datetime.min.replace(
        tzinfo=datetime.timezone.utc
    )).timestamp()
    # Recency dominates for expiring content; priority is only a tie-breaker.
    return (timestamp, int(item.get("priority", 3)))


def _trim_time_sensitive_pool(queue, media_type, limit=TARGET_PER_TYPE):
    eligible = [
        item
        for item in queue
        if item.get("type") == media_type and _is_publishable_record(item)
    ]
    if len(eligible) <= limit:
        return False
    keep_ids = {
        item.get("id")
        for item in sorted(
            eligible, key=_time_sensitive_sort_key, reverse=True
        )[:limit]
    }
    before = len(queue)
    queue[:] = [
        item
        for item in queue
        if item not in eligible or item.get("id") in keep_ids
    ]
    removed = before - len(queue)
    if removed:
        print(f"  [ROLLING/{media_type}] Removidos {removed} conteúdos ultrapassados.")
    return removed > 0


def _refresh_verified_queue(queue):
    """Re-run the resolver so cached links must satisfy its availability TTL."""
    changed = False
    for item in queue:
        if (
            item.get("status") != "queue"
            or item.get("resolutionStatus") != "verified"
        ):
            continue
        temporal = {
            field: item.get(field)
            for field in (
                "sourceSeriesId",
                "sourceSeriesTitle",
                "sourcePublishedAt",
                "expiryDate",
            )
            if item.get(field)
        }
        try:
            resolved = resolve_recommendation(dict(item), force=False)
            for field, value in temporal.items():
                if not resolved.get(field):
                    resolved[field] = value
            resolved["status"] = "queue"
            if not _is_publishable_record(resolved):
                raise ValueError("contrato de verificação incompleto")
            if resolved != item:
                item.clear()
                item.update(resolved)
                changed = True
        except (ResolutionError, requests.RequestException, OSError, ValueError) as exc:
            item["status"] = "invalid"
            item["resolutionStatus"] = "rejected"
            item["validationError"] = str(exc)
            changed = True
            print(f"  [STALE] {item.get('title')}: {exc}")
    return changed


def _quarantine_unverified_queue(queue):
    """Upgrade old queue entries or make them explicitly non-publishable."""
    changed = False
    for item in queue:
        if item.get("status") != "queue" or item.get("resolutionStatus") == "verified":
            continue
        try:
            resolved = resolve_recommendation(dict(item), force=True)
            item.clear()
            item.update(resolved)
            item["status"] = "queue"
            changed = True
            print(f"  [MIGRATED] {item.get('title')}")
        except (ResolutionError, requests.RequestException, OSError, ValueError) as exc:
            item["status"] = "invalid"
            item["resolutionStatus"] = "rejected"
            item["validationError"] = str(exc)
            changed = True
            print(f"  [QUARANTINE] {item.get('title')}: {exc}")
    return changed


def _prune_expired_queue(queue):
    original_length = len(queue)
    queue[:] = [
        item
        for item in queue
        if not (item.get("status") == "queue" and _is_expired(item))
    ]
    removed = original_length - len(queue)
    if removed:
        print(f"  [EXPIRED] Removidas {removed} recomendações fora de prazo.")
    return removed > 0


def auto_populate():
    data = _load_database()
    queue = data.get("queue", [])
    history = data.get("history", [])
    groq_key = os.environ.get("GROQ_API_KEY", "")
    changed = _prune_expired_queue(queue)
    changed |= _refresh_verified_queue(queue)
    changed |= _quarantine_unverified_queue(queue)

    publishable = [item for item in queue if _is_publishable_record(item)]
    counts = {
        media_type: sum(item.get("type") == media_type for item in publishable)
        for media_type in ALLOWED_TYPES
    }
    needed = {
        media_type: max(0, TARGET_PER_TYPE - counts[media_type])
        for media_type in ALLOWED_TYPES
    }
    print(f"Verified queue pool: {counts}; target deficits: {needed}")

    all_identities = _identity_sets(
        queue + history, include_cover_hashes=False
    )
    identities = (
        all_identities[0],
        all_identities[1],
        all_identities[2],
        _identity_sets(queue)[3],
    )
    seen_titles, seen_links, seen_ids, _ = identities

    watchlist = {"podcasts": [], "episodeCandidates": []}
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as handle:
                watchlist = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARNING] Não foi possível ler a watchlist: {exc}")

    podcast_candidates = discover_podcast_candidates(
        watchlist,
        seen_titles,
        seen_ids,
        max(
            TARGET_PER_TYPE * 3,
            len(watchlist.get("podcasts", [])),
        ),
    )
    for candidate in podcast_candidates:
        changed |= _upsert_latest_podcast(
            candidate, queue, history, needed
        )
    changed |= _refresh_podcast_editorial_descriptions(queue, groq_key)
    changed |= _trim_time_sensitive_pool(queue, "podcast")

    cse_key = os.environ.get("GOOGLE_CSE_API_KEY", "")
    cse_id = os.environ.get("GOOGLE_CSE_ID", "")
    all_identities = _identity_sets(
        queue + history, include_cover_hashes=False
    )
    identities = (
        all_identities[0],
        all_identities[1],
        all_identities[2],
        _identity_sets(queue)[3],
    )
    seen_titles, seen_links, seen_ids, _ = identities
    highlight_budget = max(needed["highlight"], 2)
    highlight_candidates = discover_highlight_candidates(
        cse_key, cse_id, seen_links, max(highlight_budget * 4, 8)
    )
    if len(highlight_candidates) < max(highlight_budget * 2, 4):
        rss_candidates = discover_rss_highlight_candidates(
            seen_links
            | {
                candidate.get("link", "")
                for candidate in highlight_candidates
            },
            max(highlight_budget * 4, 8),
        )
        highlight_candidates.extend(rss_candidates)
    highlights_added = 0
    for candidate in highlight_candidates:
        if highlights_added >= highlight_budget:
            break
        added = _add_if_verified(
            candidate,
            queue,
            identities,
            needed,
            allow_when_full=True,
        )
        changed |= added
        highlights_added += int(added)
    changed |= _trim_time_sensitive_pool(queue, "highlight")

    # Curated episode URLs are intentionally distinct from programme/channel
    # watch targets. Only an exact, independently verifiable episode can enter
    # the recommendation queue.
    all_identities = _identity_sets(
        queue + history, include_cover_hashes=False
    )
    identities = (
        all_identities[0],
        all_identities[1],
        all_identities[2],
        _identity_sets(queue)[3],
    )
    for raw in watchlist.get("episodeCandidates", []):
        if not isinstance(raw, dict):
            continue
        media_type = raw.get("type")
        if media_type not in ALLOWED_TYPES:
            continue
        candidate = {
            **raw,
            "id": raw.get("id") or f"watch_{media_type}_{uuid.uuid4().hex[:16]}",
            "category": CATEGORIES.get(media_type, media_type.title()),
            "imageUrl": "",
            "priority": int(raw.get("priority", 3)),
            "expiryDate": raw.get("expiryDate"),
            "createdAt": raw.get("createdAt") or _utc_now(),
            "status": "queue",
            "sourceHint": "curated-episode-watchlist",
        }
        changed |= _add_if_verified(
            candidate, queue, identities, needed
        )

    all_identities = _identity_sets(
        queue + history, include_cover_hashes=False
    )
    identities = (
        all_identities[0],
        all_identities[1],
        all_identities[2],
        _identity_sets(queue)[3],
    )
    seen_titles = identities[0]
    rejected_titles = {
        key.split(":", 1)[1]
        for key in seen_titles
        if key.startswith(("book:", "movie:"))
    }
    if needed["book"] > 0 or needed["movie"] > 0:
        print(
            "  [CATALOGUE] A completar a fila com obras previamente "
            "comprovadas, novamente sujeitas à verificação ao vivo."
        )
        for raw in VERIFIED_CATALOGUE_CANDIDATES:
            if needed["book"] <= 0 and needed["movie"] <= 0:
                break
            candidate = _canonical_candidate(raw)
            if not candidate:
                continue
            normalised_title = _normalise(candidate["title"])
            if normalised_title in rejected_titles:
                continue
            rejected_titles.add(normalised_title)
            changed |= _add_if_verified(
                candidate, queue, identities, needed
            )

    # The model is a discovery fallback only. This keeps autonomous runs fast
    # and quiet while preserving the ability to extend the pool after the
    # verified reserve has genuinely been consumed.
    for attempt in range(1, 4):
        if needed["book"] <= 0 and needed["movie"] <= 0:
            break
        print(
            f"  [AI DISCOVERY] Reserva insuficiente; tentativa {attempt} "
            "de encontrar novas obras verificáveis."
        )
        raw_candidates = _groq_catalogue_candidates(
            groq_key, rejected_titles, attempt
        )
        if not raw_candidates:
            break
        for raw in raw_candidates:
            candidate = _canonical_candidate(raw)
            if not candidate:
                continue
            normalised_title = _normalise(candidate["title"])
            if normalised_title in rejected_titles:
                continue
            rejected_titles.add(normalised_title)
            changed |= _add_if_verified(candidate, queue, identities, needed)

    data["queue"] = queue
    data["history"] = history
    if changed:
        _write_database(data)

    remaining_counts = {
        media_type: sum(
            item.get("type") == media_type and _is_publishable_record(item)
            for item in queue
        )
        for media_type in ALLOWED_TYPES
    }
    print(f"Final verified queue pool: {remaining_counts}")

    missing_for_post = [
        media_type
        for media_type in ("book", "podcast", "highlight")
        if remaining_counts[media_type] < 1
    ]
    if not any(
        remaining_counts[media_type] >= 1
        for media_type in ("movie", "nostalgia", "investigation")
    ):
        missing_for_post.append("movie/nostalgia/investigation")
    if missing_for_post:
        raise RuntimeError(
            "Não foi possível obter sequer um candidato verificado para: "
            + ", ".join(missing_for_post)
            + ". O post não será gerado com conteúdo ambíguo."
        )

    deficits = {
        media_type: TARGET_PER_TYPE - count
        for media_type, count in remaining_counts.items()
        if count < TARGET_PER_TYPE
    }
    if deficits:
        print(
            "[WARNING] O post atual está coberto, mas a reserva ainda está "
            f"abaixo do alvo: {deficits}"
        )


if __name__ == "__main__":
    try:
        auto_populate()
    except Exception as exc:
        print(f"[ERROR] Grounded auto-population failed: {exc}")
        sys.exit(1)
