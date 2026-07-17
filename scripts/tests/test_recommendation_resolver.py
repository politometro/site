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
from unittest.mock import patch

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

    def test_atomic_resolution_replaces_ai_copy_with_canonical_facts(self):
        item = {
            "id": "candidate-1",
            "type": "book",
            "title": "Título Inventado pela IA",
            "authorOrMeta": "Autor Inventado",
            "description": "Lead promocional inventado.",
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
        self.assertEqual(
            result["description"], "Livro “Título Canónico”, de Autor Real."
        )
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
            patch.object(resolver, "_probe_link_available", return_value=True),
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
            patch.object(resolver, "_probe_link_available", return_value=False),
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
            patch.object(resolver, "_probe_link_available", return_value=True),
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
