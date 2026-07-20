from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import recommendation_resolver as resolver


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def iso_days_ago(days: int) -> str:
    return (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    ).isoformat().replace("+00:00", "Z")


def jpeg_bytes(width: int = 320, height: int = 480) -> bytes:
    image = Image.new("RGB", (width, height), (33, 72, 105))
    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


class RecommendationResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.cache_patch = patch.object(
            resolver, "CACHE_DIR", self.temporary.name
        )
        self.cache_patch.start()
        self.cover_data = jpeg_bytes()
        self.cover = resolver.NormalizedCover(
            data=self.cover_data,
            sha256=hashlib.sha256(self.cover_data).hexdigest(),
            width=320,
            height=480,
            source_url="https://cdn.example.org/cover.jpg",
            source_mime="image/jpeg",
        )

    def tearDown(self) -> None:
        self.cache_patch.stop()
        self.temporary.cleanup()

    def entity(
        self,
        *,
        media_type: str = "book",
        title: str = "Título Canónico",
        author: str = "Autor Real",
        description: str = "",
        published_at: str = "",
    ) -> resolver.EntityResolution:
        prefix = {
            "book": "isbn:9789720000001",
            "podcast": "apple-episode:12345",
            "movie": "imdb:tt0120626",
            "highlight": "article:url:abc123",
        }[media_type]
        return resolver.EntityResolution(
            link="https://example.org/content",
            image_url="https://cdn.example.org/cover.jpg",
            external_id=prefix,
            source=f"test:{media_type}",
            score=0.97,
            resolved_title=title,
            resolved_author=author,
            description=description,
            published_at=published_at,
        )

    def resolve_with(
        self, item: dict, entity: resolver.EntityResolution
    ) -> dict:
        with (
            patch.object(resolver, "_resolve_entity", return_value=entity),
            patch.object(resolver, "_assert_safe_url"),
            patch.object(
                resolver,
                "_download_and_normalize_image",
                return_value=self.cover,
            ),
        ):
            return resolver.resolve_recommendation(item)

    def test_legacy_cache_key_remains_compatible_and_identity_is_unique(self):
        legacy = resolver._cache_key("Capitães de Abril", "movie")
        self.assertEqual(legacy, "movie_capit_es_de_abril_2790f2af")
        first = resolver._cache_key(
            "Obra Homónima", "book", "isbn:1111111111"
        )
        second = resolver._cache_key(
            "Obra Homónima", "book", "isbn:2222222222"
        )
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith(resolver._cache_key("Obra Homónima", "book")))

    def test_atomic_resolution_keeps_content_copy_when_source_has_no_synopsis(self):
        item = {
            "id": "candidate-1",
            "type": "book",
            "title": "Título Inventado pela IA",
            "authorOrMeta": "Autor Inventado",
            "description": (
                "Uma leitura sobre propaganda, vigilância e controlo político "
                "numa sociedade autoritária."
            ),
            "link": "",
            "imageUrl": "",
        }
        result = self.resolve_with(
            item,
            self.entity(
                title="Título Canónico",
                author="Autor Real",
                description="",
            ),
        )
        self.assertEqual(result["resolutionStatus"], "verified")
        self.assertEqual(result["title"], "Título Canónico")
        self.assertEqual(result["authorOrMeta"], "Autor Real")
        self.assertIn("propaganda", result["description"])
        self.assertIn("controlo", result["description"])
        self.assertTrue(result["imageUrl"].startswith("/covers/"))
        self.assertTrue(resolver.validate_cached_cover(result))
        self.assertEqual(
            result["verification"]["sourceDescription"],
            result["description"],
        )

    def test_grounded_source_description_replaces_ai_description(self):
        item = {
            "type": "movie",
            "title": "Nome aproximado",
            "authorOrMeta": "Realizador alegado",
            "description": "Texto Groq.",
            "link": "",
            "imageUrl": "",
        }
        result = self.resolve_with(
            item,
            self.entity(
                media_type="movie",
                title="Nome Oficial",
                author="Realizador Oficial",
                description="Sinopse factual fornecida pela fonte verificada.",
            ),
        )
        self.assertEqual(
            result["description"],
            "Sinopse factual fornecida pela fonte verificada.",
        )
        self.assertNotIn("Groq", result["description"])

    def test_cached_podcast_preserves_editorial_copy_and_source_evidence(self):
        now = dt.datetime.now(dt.timezone.utc)
        source_description = (
            "As notas finais chegaram às escolas, mas muitas ainda não foram "
            "afixadas e vários alunos continuam sem prazo definido."
        )
        editorial_description = (
            "O episódio analisa falhas na divulgação das notas dos exames "
            "nacionais e a incerteza ainda vivida pelos alunos."
        )
        item = {
            "type": "podcast",
            "title": "Exames nacionais e notas em atraso",
            "authorOrMeta": "Expresso da Meia-Noite",
            "description": source_description,
            "link": "https://example.org/podcast/episode",
            "imageUrl": "/covers/podcast.jpg",
            "sourcePublishedAt": (
                now - dt.timedelta(hours=4)
            ).isoformat(),
            "expiryDate": (now + dt.timedelta(days=3)).isoformat(),
            "verification": {
                "status": "verified",
                "entityId": "episode:123",
                "coverHash": "cover-hash",
                "verifiedAt": iso_now(),
                "resolvedTitle": "Exames nacionais e notas em atraso",
                "resolvedAuthor": "Expresso da Meia-Noite",
                "sourceDescription": source_description,
                "editorialDescription": editorial_description,
            },
        }

        with (
            patch.object(resolver, "validate_cached_cover", return_value=True),
            patch.object(resolver, "probe_verified_source", return_value=True),
        ):
            cached = resolver._already_verified(item)

        self.assertIsNotNone(cached)
        self.assertEqual(cached["description"], editorial_description)
        self.assertEqual(
            cached["verification"]["sourceDescription"],
            source_description,
        )

    def test_unmanifested_legacy_file_never_skips_entity_resolution(self):
        item = {
            "type": "book",
            "title": "Título Antigo",
            "authorOrMeta": "Autor",
            "description": "",
            "link": "",
            "imageUrl": "",
        }
        legacy_path = Path(self.temporary.name) / (
            resolver._cache_key(item["title"], item["type"]) + ".jpg"
        )
        legacy_path.write_bytes(self.cover_data)
        entity = self.entity()
        with (
            patch.object(
                resolver, "_resolve_entity", return_value=entity
            ) as resolve_mock,
            patch.object(resolver, "_assert_safe_url"),
            patch.object(
                resolver,
                "_download_and_normalize_image",
                return_value=self.cover,
            ),
        ):
            result = resolver.resolve_recommendation(item)
        resolve_mock.assert_called_once()
        self.assertTrue(resolver.validate_cached_cover(result))

    def test_fresh_verified_cache_requires_link_probe_and_does_not_mutate_files(self):
        item = {
            "type": "book",
            "title": "Candidato",
            "authorOrMeta": "Autor",
            "description": "",
            "link": "",
            "imageUrl": "",
        }
        result = self.resolve_with(item, self.entity())
        image_path = Path(self.temporary.name) / result["imageUrl"].split("/")[-1]
        manifest_path = image_path.with_suffix(".json")
        before = (
            image_path.read_bytes(),
            manifest_path.read_bytes(),
            image_path.stat().st_mtime_ns,
            manifest_path.stat().st_mtime_ns,
        )
        with (
            patch.object(
                resolver, "_probe_link_state", return_value="available"
            ),
            patch.object(
                resolver,
                "_resolve_entity",
                side_effect=AssertionError("should not resolve"),
            ),
        ):
            cached = resolver.resolve_recommendation(result)
        after = (
            image_path.read_bytes(),
            manifest_path.read_bytes(),
            image_path.stat().st_mtime_ns,
            manifest_path.stat().st_mtime_ns,
        )
        self.assertEqual(before, after)
        self.assertEqual(cached["title"], "Título Canónico")

    def test_unavailable_verified_link_is_not_reused(self):
        item = {
            "type": "book",
            "title": "Candidato",
            "authorOrMeta": "Autor",
            "description": "",
            "link": "",
            "imageUrl": "",
        }
        result = self.resolve_with(item, self.entity())
        with (
            patch.object(
                resolver, "_probe_link_state", return_value="missing"
            ),
            patch.object(
                resolver,
                "_resolve_entity",
                side_effect=resolver.ResolutionError(
                    "LINK_UNAVAILABLE", "404"
                ),
            ) as resolve_mock,
        ):
            with self.assertRaisesRegex(
                resolver.ResolutionError, "LINK_UNAVAILABLE"
            ):
                resolver.resolve_recommendation(result)
        resolve_mock.assert_called_once()

    def test_stale_verified_cache_is_not_reused(self):
        item = {
            "type": "book",
            "title": "Candidato",
            "authorOrMeta": "Autor",
            "description": "",
            "link": "",
            "imageUrl": "",
        }
        result = self.resolve_with(item, self.entity())
        stale = copy.deepcopy(result)
        stale["verification"]["verifiedAt"] = iso_days_ago(3)
        with (
            patch.object(
                resolver, "_probe_link_state", return_value="available"
            ),
            patch.object(
                resolver,
                "_resolve_entity",
                side_effect=resolver.ResolutionError(
                    "REVALIDATION_REQUIRED", "stale"
                ),
            ) as resolve_mock,
        ):
            with self.assertRaises(resolver.ResolutionError):
                resolver.resolve_recommendation(stale)
        resolve_mock.assert_called_once()

    def test_podcast_expiry_uses_daily_and_weekly_cadence(self):
        published = iso_now()
        daily = resolver._expiry_for(
            "podcast", published, {"frequency": "diário"}
        )
        weekly = resolver._expiry_for(
            "podcast", published, {"frequency": "semanal"}
        )
        published_dt = dt.datetime.fromisoformat(
            published.replace("Z", "+00:00")
        )
        daily_dt = dt.datetime.fromisoformat(daily.replace("Z", "+00:00"))
        weekly_dt = dt.datetime.fromisoformat(weekly.replace("Z", "+00:00"))
        self.assertAlmostEqual((daily_dt - published_dt).days, 3)
        self.assertAlmostEqual((weekly_dt - published_dt).days, 10)

    def test_podcast_and_highlight_require_date_and_nonexpired_expiry(self):
        base = {
            "title": "Conteúdo",
            "authorOrMeta": "Fonte",
            "description": "IA",
            "link": "",
            "imageUrl": "",
        }
        with patch.object(
            resolver,
            "_resolve_entity",
            return_value=self.entity(
                media_type="podcast", published_at=""
            ),
        ), patch.object(resolver, "_assert_safe_url"):
            with self.assertRaisesRegex(
                resolver.ResolutionError, "SOURCE_DATE_UNVERIFIED"
            ):
                resolver.resolve_recommendation({**base, "type": "podcast"})

        expired = self.entity(
            media_type="highlight", published_at=iso_days_ago(90)
        )
        with patch.object(
            resolver, "_resolve_entity", return_value=expired
        ), patch.object(resolver, "_assert_safe_url"):
            with self.assertRaisesRegex(
                resolver.ResolutionError, "CONTENT_EXPIRED"
            ):
                resolver.resolve_recommendation({**base, "type": "highlight"})

    def test_existing_valid_expiry_is_preserved(self):
        future = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)
        ).isoformat().replace("+00:00", "Z")
        item = {
            "type": "podcast",
            "title": "Episódio candidato",
            "authorOrMeta": "Programa / Autor",
            "description": "IA",
            "link": "",
            "imageUrl": "",
            "expiryDate": future,
        }
        result = self.resolve_with(
            item,
            self.entity(
                media_type="podcast",
                title="Episódio oficial",
                author="Programa",
                published_at=iso_days_ago(1),
            ),
        )
        self.assertEqual(result["expiryDate"], future)
        self.assertTrue(result["sourcePublishedAt"])

    def test_time_sensitive_content_requires_review_margin(self):
        near_expiry = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)
        ).isoformat().replace("+00:00", "Z")
        item = {
            "type": "podcast",
            "title": "Episódio candidato",
            "authorOrMeta": "Programa / Autor",
            "description": "IA",
            "link": "",
            "imageUrl": "",
            "expiryDate": near_expiry,
        }
        with self.assertRaisesRegex(
            resolver.ResolutionError, "CONTENT_TOO_CLOSE_TO_EXPIRY"
        ):
            self.resolve_with(
                item,
                self.entity(
                    media_type="podcast",
                    title="Episódio oficial",
                    author="Programa / Autor",
                    published_at=iso_days_ago(1),
                ),
            )

    def test_investigation_expiry_when_time_sensitive(self):
        published = iso_now()
        evergreen = resolver._expiry_for("investigation", published, {})
        self.assertIsNone(evergreen)

        time_sensitive = resolver._expiry_for(
            "investigation", published, {"is_time_sensitive": True}
        )
        self.assertIsNotNone(time_sensitive)
        published_dt = dt.datetime.fromisoformat(
            published.replace("Z", "+00:00")
        )
        ts_dt = dt.datetime.fromisoformat(time_sensitive.replace("Z", "+00:00"))
        self.assertAlmostEqual((ts_dt - published_dt).days, 60)

        # Confirm resolution fails if time-sensitive investigation is expired
        base = {
            "type": "investigation",
            "title": "Investigação Datada",
            "authorOrMeta": "NOW Canal",
            "description": "Reportagem",
            "link": "",
            "imageUrl": "",
            "is_time_sensitive": True,
        }
        expired_entity = self.entity(
            media_type="highlight", published_at=iso_days_ago(120)
        )
        with patch.object(
            resolver, "_resolve_entity", return_value=expired_entity
        ), patch.object(resolver, "_assert_safe_url"):
            with self.assertRaisesRegex(
                resolver.ResolutionError, "CONTENT_EXPIRED"
            ):
                resolver.resolve_recommendation(base)

    def test_bataguas_categorized_as_podcast(self):
        item = {
            "type": "podcast",
            "title": "Conteúdo do Batáguas EP02",
            "authorOrMeta": "Diogo Bataguas",
            "description": "Sátira e humor sobre a atualidade política.",
            "link": "https://www.youtube.com/watch?v=7A0mUSB-sgw",
            "imageUrl": "",
        }
        bat_entity = resolver.EntityResolution(
            link="https://www.youtube.com/watch?v=7A0mUSB-sgw",
            image_url="https://cdn.example.org/cover.jpg",
            external_id="youtube:7A0mUSB-sgw",
            source="youtube:Diogo Bataguas",
            score=1.0,
            resolved_title="Conteúdo do Batáguas EP02",
            resolved_author="Diogo Bataguas",
            description="Sátira e humor sobre a atualidade política.",
            published_at=iso_days_ago(5),
        )
        result = self.resolve_with(item, bat_entity)
        self.assertEqual(result["type"], "podcast")
        self.assertIsNotNone(result["expiryDate"])

    def test_series_recency_restriction_prevents_recommendation_within_4_weeks(self):
        recent_pub = iso_days_ago(10)
        old_pub = iso_days_ago(40)
        history = [
            {
                "type": "podcast",
                "title": "Isto É Gozar Com Quem Trabalha EP01",
                "authorOrMeta": "SIC",
                "sourceSeriesTitle": "Isto É Gozar Com Quem Trabalha",
                "publishedAt": recent_pub,
            },
            {
                "type": "podcast",
                "title": "Linhas Vermelhas EP05",
                "authorOrMeta": "SIC Notícias",
                "sourceSeriesTitle": "Linhas Vermelhas",
                "publishedAt": old_pub,
            },
        ]
        # Candidate from same podcast within 4 weeks -> blocked
        new_ep_same_podcast = {
            "type": "podcast",
            "title": "Isto É Gozar Com Quem Trabalha EP02",
            "authorOrMeta": "SIC",
            "sourceSeriesTitle": "Isto É Gozar Com Quem Trabalha",
        }
        self.assertTrue(
            resolver.is_series_recency_restricted(new_ep_same_podcast, history)
        )

        # Candidate from podcast published > 4 weeks ago -> allowed
        old_podcast_ep = {
            "type": "podcast",
            "title": "Linhas Vermelhas EP06",
            "authorOrMeta": "SIC Notícias",
            "sourceSeriesTitle": "Linhas Vermelhas",
        }
        self.assertFalse(
            resolver.is_series_recency_restricted(old_podcast_ep, history)
        )

    def test_high_importance_overrides_series_recency_restriction(self):
        history = [
            {
                "type": "podcast",
                "title": "Conteúdo do Batáguas EP01",
                "authorOrMeta": "Diogo Bataguas",
                "publishedAt": iso_days_ago(7),
            }
        ]
        candidate_normal = {
            "type": "podcast",
            "title": "Conteúdo do Batáguas EP02",
            "authorOrMeta": "Diogo Bataguas",
        }
        candidate_important = {
            "type": "podcast",
            "title": "Conteúdo do Batáguas EP02 — Especial Eleições",
            "authorOrMeta": "Diogo Bataguas",
            "high_importance": True,
        }
        self.assertTrue(
            resolver.is_series_recency_restricted(candidate_normal, history)
        )
        self.assertFalse(
            resolver.is_series_recency_restricted(candidate_important, history)
        )

    def test_investigation_is_exempt_from_series_recency_restriction(self):
        history = [
            {
                "type": "investigation",
                "title": "Repórter Sábado: Parte 1",
                "authorOrMeta": "NOW Canal",
                "sourceSeriesTitle": "Repórter Sábado",
                "publishedAt": iso_days_ago(5),
            }
        ]
        part2 = {
            "type": "investigation",
            "title": "Repórter Sábado: Parte 2",
            "authorOrMeta": "NOW Canal",
            "sourceSeriesTitle": "Repórter Sábado",
        }
        self.assertFalse(
            resolver.is_series_recency_restricted(part2, history)
        )

    def test_spoofed_imdb_and_untrusted_catalog_links_are_rejected_before_network(self):
        with self.assertRaisesRegex(
            resolver.ResolutionError, "MOVIE_LINK_NOT_IMDB"
        ):
            resolver._resolve_movie(
                {
                    "type": "movie",
                    "title": "Filme",
                    "authorOrMeta": "Realizador",
                    "link": "https://attacker.example/title/tt0120626/",
                }
            )
        with self.assertRaisesRegex(
            resolver.ResolutionError, "BOOK_DOMAIN_NOT_ALLOWED"
        ):
            resolver._validate_book_page(
                {
                    "type": "book",
                    "title": "Livro",
                    "authorOrMeta": "Autor",
                },
                "https://attacker.example/book",
            )
        with self.assertRaisesRegex(
            resolver.ResolutionError, "PODCAST_DOMAIN_NOT_ALLOWED"
        ):
            resolver._validate_podcast_page(
                {
                    "type": "podcast",
                    "title": "Episódio",
                    "authorOrMeta": "Programa / Autor",
                },
                "https://attacker.example/episode",
            )

    def test_openlibrary_work_page_is_verified_through_json_catalogue(self):
        payload = {
            "docs": [
                {
                    "key": "/works/OL30827457W",
                    "title": "1984",
                    "author_name": ["George Orwell"],
                    "isbn": ["9780451524935"],
                    "cover_i": 7222246,
                }
            ]
        }
        item = {
            "type": "book",
            "title": "1984",
            "authorOrMeta": "George Orwell",
            "link": "https://openlibrary.org/works/OL30827457W/1984",
        }

        with (
            patch.object(
                resolver, "_get_json", return_value=("search", payload)
            ) as get_json,
            patch.object(
                resolver,
                "_page_metadata",
                side_effect=AssertionError("Open Library HTML must not be used"),
            ),
        ):
            result = resolver._validate_book_page(item, item["link"])

        self.assertEqual(result.link, "https://openlibrary.org/works/OL30827457W")
        self.assertEqual(result.external_id, "isbn:9780451524935")
        self.assertEqual(result.source, "openlibrary")
        self.assertIn("search.json", get_json.call_args.args[0])

    def test_openlibrary_source_probe_uses_work_json_not_html(self):
        item = {
            "type": "book",
            "title": "1984",
            "link": "https://openlibrary.org/works/OL30827457W/1984",
        }
        with (
            patch.object(
                resolver,
                "_get_json",
                return_value=(
                    "https://openlibrary.org/works/OL30827457W.json",
                    {"key": "/works/OL30827457W", "title": "1984"},
                ),
            ) as get_json,
            patch.object(
                resolver,
                "_probe_link_available",
                side_effect=AssertionError("HTML probe must not be used"),
            ),
        ):
            self.assertTrue(resolver.probe_verified_source(item))

        self.assertEqual(
            get_json.call_args.args[0],
            "https://openlibrary.org/works/OL30827457W.json",
        )

    def test_recent_openlibrary_proof_survives_temporary_503(self):
        item = {
            "type": "book",
            "title": "1984",
            "link": "https://openlibrary.org/works/OL30827457W",
            "verification": {
                "status": "verified",
                "entityId": "openlibrary:/works/OL30827457W",
                "coverHash": "verified-cover",
                "verifiedAt": iso_now(),
            },
        }
        with (
            patch.object(
                resolver,
                "_get_json",
                side_effect=resolver.ResolutionError(
                    "HTTP_ERROR", "HTTP 503 ao obter o catálogo."
                ),
            ),
            patch.object(
                resolver, "validate_cached_cover", return_value=True
            ),
        ):
            self.assertTrue(resolver.probe_verified_source(item))

    def test_imdb_source_probe_uses_wikidata_instead_of_imdb_html(self):
        item = {
            "type": "movie",
            "title": "The Great Dictator",
            "authorOrMeta": "Charlie Chaplin",
            "link": "https://www.imdb.com/title/tt0032553/",
            "externalId": "imdb:tt0032553",
        }
        confirmation = (
            "The Great Dictator",
            ["Charlie Chaplin"],
            1.0,
            {"claims": {}},
        )
        with (
            patch.object(
                resolver,
                "_wikidata_confirms_imdb",
                return_value=confirmation,
            ) as wikidata,
            patch.object(
                resolver,
                "_probe_link_available",
                side_effect=AssertionError("IMDb HTML must not be probed"),
            ),
        ):
            self.assertTrue(resolver.probe_verified_source(item))

        wikidata.assert_called_once_with(
            "The Great Dictator", "tt0032553", "Charlie Chaplin"
        )

    def test_supplied_imdb_movie_survives_http_202_with_wikidata_poster(self):
        entity = {
            "claims": {
                "P18": [
                    {
                        "mainsnak": {
                            "datavalue": {
                                "value": "The Great Dictator poster.jpg"
                            }
                        }
                    }
                ]
            }
        }
        confirmation = (
            "The Great Dictator",
            ["Charlie Chaplin"],
            1.0,
            entity,
        )
        item = {
            "type": "movie",
            "title": "The Great Dictator",
            "authorOrMeta": "Charlie Chaplin",
            "link": "https://www.imdb.com/title/tt0032553/",
        }

        with (
            patch.object(
                resolver,
                "_wikidata_confirms_imdb",
                return_value=confirmation,
            ),
            patch.object(
                resolver,
                "_validate_imdb_page",
                side_effect=resolver.ResolutionError(
                    "HTTP_ERROR", "HTTP 202 ao obter IMDb."
                ),
            ),
        ):
            result = resolver._resolve_movie(item)

        self.assertEqual(result.external_id, "imdb:tt0032553")
        self.assertEqual(
            result.link, "https://www.imdb.com/title/tt0032553/"
        )
        self.assertIn("commons.wikimedia.org", result.image_url)
        self.assertEqual(result.resolved_author, "Charlie Chaplin")

    def test_http_202_is_classified_as_temporary_source_response(self):
        error = resolver.ResolutionError(
            "HTTP_ERROR", "HTTP 202 ao obter https://www.imdb.com/title/x/."
        )
        self.assertTrue(resolver._is_transient_source_error(error))

    def test_http_get_retries_timeout_and_503_before_accepting_json(self):
        temporary_failure = Mock()
        temporary_failure.status_code = 503
        temporary_failure.headers = {}
        temporary_failure.close = Mock()

        success = Mock()
        success.status_code = 200
        success.headers = {"Content-Type": "application/json"}
        success.url = "https://openlibrary.org/works/OL30827457W.json"
        success.iter_content.return_value = [b'{"key":"/works/OL30827457W"}']
        success.close = Mock()

        with (
            patch.object(resolver, "_assert_safe_url"),
            patch.object(
                resolver.requests,
                "get",
                side_effect=[
                    resolver.requests.Timeout("timed out"),
                    temporary_failure,
                    success,
                ],
            ) as request,
            patch.object(resolver.time, "sleep"),
        ):
            final_url, mime, body = resolver._http_get(
                "https://openlibrary.org/works/OL30827457W.json",
                accept="application/json",
                max_bytes=4096,
                allowed_mimes=resolver.JSON_MIME_TYPES,
            )

        self.assertEqual(request.call_count, 3)
        self.assertEqual(final_url, success.url)
        self.assertEqual(mime, "application/json")
        self.assertIn(b"OL30827457W", body)

    def test_apple_match_checks_show_and_creator_separately(self):
        result = {
            "trackName": "O episódio exato",
            "collectionName": "Programa Certo",
            "artistName": "Autor Errado",
            "trackViewUrl": "https://podcasts.apple.com/pt/x?i=123",
            "artworkUrl600": "https://is1-ssl.mzstatic.com/cover.jpg",
            "trackId": 123,
            "releaseDate": iso_days_ago(1),
        }
        item = {
            "title": "O episódio exato",
            "authorOrMeta": "Programa Certo / Autor Certo",
        }
        self.assertIsNone(resolver._apple_episode_from_result(item, result))
        result["artistName"] = "Autor Certo"
        self.assertIsNotNone(
            resolver._apple_episode_from_result(item, result)
        )

    def test_wikidata_uses_localised_entity_label_not_search_label(self):
        entity = {
            "labels": {
                "pt": {"value": "Capitães de Abril"},
                "en": {"value": "April Captains"},
            },
            "aliases": {},
        }
        label, combined, sequence = resolver._wikidata_matching_title(
            entity, "Capitães de Abril"
        )
        self.assertEqual(label, "Capitães de Abril")
        self.assertGreaterEqual(combined, 0.99)
        self.assertGreaterEqual(sequence, 0.99)

    def test_feed_parser_extracts_editorial_image_from_description(self):
        feed = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item>
          <title>Reportagem politica</title>
          <link>https://www.rtp.pt/noticias/pais/reportagem_n123</link>
          <guid>reportagem-123</guid>
          <pubDate>Sat, 18 Jul 2026 10:00:00 +0100</pubDate>
          <description><![CDATA[
            <img src="https://cdn-images.rtp.pt/noticias/reportagem.jpg"/>
            Descricao editorial.
          ]]></description>
        </item></channel></rss>"""
        entries = resolver._parse_feed(
            feed, "https://www.rtp.pt/noticias/rss"
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0]["image"],
            "https://cdn-images.rtp.pt/noticias/reportagem.jpg",
        )

    def test_highlight_policy_rejects_news_and_accepts_editorial_work(self):
        self.assertFalse(
            resolver.is_eligible_highlight(
                title=(
                    'Líder do PAN diz que oposições internas têm de '
                    'respeitar "quem o coletivo elege"'
                ),
                description=(
                    "A porta-voz comentou hoje as divergências internas."
                ),
                link="https://www.rtp.pt/noticias/politica/lider-do-pan_n123",
            )
        )
        self.assertFalse(
            resolver.is_eligible_highlight(
                title="Ministério Público abre investigação ao contrato",
                description="O inquérito foi anunciado esta manhã.",
                link=(
                    "https://www.rtp.pt/noticias/pais/"
                    "ministerio-publico-abre-investigacao_n456"
                ),
            )
        )
        self.assertTrue(
            resolver.is_eligible_highlight(
                title="O futuro da democracia portuguesa",
                description="Reflexão do autor sobre as instituições.",
                link=(
                    "https://www.publico.pt/2026/07/18/"
                    "opiniao/futuro-democracia"
                ),
            )
        )
        self.assertTrue(
            resolver.is_eligible_highlight(
                title="Os contratos que ficaram por explicar",
                description="Jornalismo de investigação aprofundado.",
                link="https://expresso.pt/politica/contratos",
            )
        )

    def test_highlight_page_rejects_ordinary_news_after_metadata_check(self):
        item = {
            "type": "highlight",
            "title": "Líder partidário apresenta candidatura",
            "authorOrMeta": "RTP",
        }
        metadata = {
            "finalUrl": "https://www.rtp.pt/noticias/politica/candidatura_n1",
            "canonical": "https://www.rtp.pt/noticias/politica/candidatura_n1",
            "title": "Líder partidário apresenta candidatura",
            "image": "https://cdn.example.org/news.jpg",
            "description": "O anúncio foi feito esta manhã.",
            "publishedAt": iso_now(),
            "authors": ["RTP"],
            "isbns": [],
            "meta": {"article:section": "Política"},
        }
        with patch.object(
            resolver, "_page_metadata", return_value=metadata
        ):
            with self.assertRaisesRegex(
                resolver.ResolutionError, "NEWS_NOT_ALLOWED"
            ):
                resolver._validate_highlight_page(
                    item, metadata["canonical"]
                )

    def test_svg_unsplash_known_placeholder_and_private_ip_are_blocked(self):
        with self.assertRaisesRegex(
            resolver.ResolutionError, "NON_RASTER_IMAGE"
        ):
            resolver._decode_and_normalize_image(
                b"<svg>" + (b"x" * 2048),
                "image/jpeg",
                "https://cdn.example.org/a.jpg",
            )
        with self.assertRaisesRegex(
            resolver.ResolutionError, "PLACEHOLDER_IMAGE"
        ):
            resolver._assert_safe_url(
                "https://images.unsplash.com/photo.jpg", purpose="image"
            )
        with self.assertRaisesRegex(
            resolver.ResolutionError, "SSRF_BLOCKED"
        ):
            resolver._assert_safe_url("http://127.0.0.1/private")

    def test_availability_probe_blocks_private_redirect_and_non_success_statuses(self):
        class FakeResponse:
            def __init__(self, status_code, headers=None, url="https://example.com/"):
                self.status_code = status_code
                self.headers = headers or {}
                self.url = url

            def close(self):
                return None

        public_dns = [
            (
                2,
                1,
                6,
                "",
                ("93.184.216.34", 443),
            )
        ]
        private_redirect = FakeResponse(
            302,
            {"Location": "http://127.0.0.1/private"},
            "https://example.com/start",
        )
        with (
            patch.object(resolver.socket, "getaddrinfo", return_value=public_dns),
            patch.object(
                resolver.requests, "get", return_value=private_redirect
            ) as get_mock,
        ):
            self.assertFalse(
                resolver._probe_link_available("https://example.com/start")
            )
            get_mock.assert_called_once()

        for status in (403, 429):
            with self.subTest(status=status), patch.object(
                resolver.socket, "getaddrinfo", return_value=public_dns
            ), patch.object(
                resolver.requests,
                "get",
                return_value=FakeResponse(status),
            ):
                self.assertFalse(
                    resolver._probe_link_available("https://example.com/source")
                )

    def test_generic_probe_distinguishes_protection_from_deleted_link(self):
        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code
                self.headers = {}
                self.url = "https://example.com/article"

            def close(self):
                return None

        with (
            patch.object(resolver, "_assert_safe_url"),
            patch.object(
                resolver.requests, "get", return_value=FakeResponse(403)
            ),
        ):
            self.assertEqual(
                resolver._probe_link_state("https://example.com/article"),
                "transient",
            )
        with (
            patch.object(resolver, "_assert_safe_url"),
            patch.object(
                resolver.requests, "get", return_value=FakeResponse(404)
            ),
        ):
            self.assertEqual(
                resolver._probe_link_state("https://example.com/article"),
                "missing",
            )

    def test_recent_generic_proof_survives_protection_but_not_404(self):
        item = {
            "type": "highlight",
            "title": "Investigação",
            "link": "https://example.com/article",
            "verification": {
                "status": "verified",
                "entityId": "article:123",
                "coverHash": "verified-cover",
                "verifiedAt": iso_now(),
            },
        }
        with (
            patch.object(
                resolver, "_probe_link_state", return_value="transient"
            ),
            patch.object(
                resolver, "validate_cached_cover", return_value=True
            ),
        ):
            self.assertTrue(resolver.probe_verified_source(item))
        with patch.object(
            resolver, "_probe_link_state", return_value="missing"
        ):
            self.assertFalse(resolver.probe_verified_source(item))

    def test_raster_normalisation_outputs_verified_jpeg(self):
        png = BytesIO()
        Image.new("RGBA", (400, 400), (10, 20, 30, 128)).save(
            png, format="PNG"
        )
        cover = resolver._decode_and_normalize_image(
            png.getvalue(),
            "image/png",
            "https://cdn.example.org/cover.png",
        )
        self.assertEqual(
            hashlib.sha256(cover.data).hexdigest(), cover.sha256
        )
        reopened = Image.open(BytesIO(cover.data))
        self.assertEqual(reopened.format, "JPEG")
        self.assertEqual(reopened.size, (400, 400))

    def test_resolution_error_accepts_legacy_single_argument(self):
        error = resolver.ResolutionError("mensagem antiga")
        self.assertEqual(error.code, "RESOLUTION_FAILED")
        self.assertIn("mensagem antiga", str(error))


if __name__ == "__main__":
    unittest.main()
