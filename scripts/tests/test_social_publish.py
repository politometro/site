"""Testes da publicação no Facebook/X e das sugestões rápidas no Discord."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import post_facebook, post_x
from scripts import discord_reviewer


def _write_json(path, payload):
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


class SocialPublishFixture(unittest.TestCase):
    """Rascunho aprovado + artefactos de imagem/legenda num diretório temporário."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        scripts_dir = Path(self.tmp.name)
        self.draft_path = scripts_dir / "review_draft.json"
        self.facebook_receipt = scripts_dir / "facebook_publication.json"
        self.x_receipt = scripts_dir / "x_publication.json"
        self.image_path = scripts_dir / "current_post.jpg"
        self.caption_path = scripts_dir / "current_caption.txt"

        draft = {
            "schema_version": 2,
            "draft_id": "draft_social",
            "content_hash": "hash_social",
            "created_at": "2026-07-18T20:00:00+00:00",
            "is_test": False,
            "approval": {
                "approved": True,
                "draft_id": "draft_social",
                "content_hash": "hash_social",
            },
        }
        _write_json(self.draft_path, draft)
        self.caption_path.write_text(
            "Livro, podcast e proposta da semana. #Politometro", encoding="utf-8"
        )
        # JPEG mínimo válido (1×1 branco).
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1080, 1350), "white").save(buffer, "JPEG", quality=85)
        self.image_path.write_bytes(buffer.getvalue())

        patches = (
            mock.patch.object(post_facebook, "DRAFT_PATH", str(self.draft_path)),
            mock.patch.object(post_facebook, "RECEIPT_PATH", str(self.facebook_receipt)),
            mock.patch.object(post_facebook, "IMAGE_PATH", str(self.image_path)),
            mock.patch.object(post_facebook, "CAPTION_PATH", str(self.caption_path)),
            mock.patch.object(post_x, "DRAFT_PATH", str(self.draft_path)),
            mock.patch.object(post_x, "RECEIPT_PATH", str(self.x_receipt)),
            mock.patch.object(post_x, "IMAGE_PATH", str(self.image_path)),
            mock.patch.object(post_x, "CAPTION_PATH", str(self.caption_path)),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()


class FacebookPublishTests(SocialPublishFixture):
    def test_publishes_same_image_and_caption(self):
        captured = {}

        def fake_post(url, data=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            response = mock.Mock()
            response.ok = True
            response.headers = {"content-type": "application/json"}
            response.json.return_value = {"post_id": "fb_post_1"}
            return response

        with mock.patch.dict(
            "os.environ",
            {
                "FACEBOOK_PAGE_ID": "123456",
                "FACEBOOK_ACCESS_TOKEN": "token",
                "GITHUB_REPOSITORY": "org/repo",
                "GITHUB_SHA": "abc123",
            },
        ), mock.patch.object(post_facebook.requests, "post", side_effect=fake_post):
            post_facebook.publish_facebook()

        self.assertIn("/123456/photos", captured["url"])
        self.assertEqual(
            captured["data"]["url"],
            "https://raw.githubusercontent.com/org/repo/abc123/website/public/current_post.jpg",
        )
        self.assertEqual(
            captured["data"]["caption"],
            "Livro, podcast e proposta da semana. #Politometro",
        )
        receipt = json.loads(self.facebook_receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["post_id"], "fb_post_1")
        self.assertEqual(receipt["draft_id"], "draft_social")

    def test_is_idempotent_per_draft(self):
        _write_json(
            self.facebook_receipt,
            {
                "draft_id": "draft_social",
                "content_hash": "hash_social",
                "post_id": "already",
            },
        )
        with mock.patch.dict(
            "os.environ",
            {
                "FACEBOOK_PAGE_ID": "123456",
                "FACEBOOK_ACCESS_TOKEN": "token",
                "GITHUB_REPOSITORY": "org/repo",
            },
        ), mock.patch.object(
            post_facebook.requests, "post"
        ) as post_spy:
            post_facebook.publish_facebook()
        post_spy.assert_not_called()

    def test_undecided_draft_cannot_publish(self):
        draft = json.loads(self.draft_path.read_text(encoding="utf-8"))
        draft["approval"] = {"approved": False}
        _write_json(self.draft_path, draft)
        with mock.patch.dict(
            "os.environ",
            {
                "FACEBOOK_PAGE_ID": "123456",
                "FACEBOOK_ACCESS_TOKEN": "token",
                "GITHUB_REPOSITORY": "org/repo",
            },
        ):
            with self.assertRaises(RuntimeError):
                post_facebook.publish_facebook()


class XPublishTests(SocialPublishFixture):
    def setUp(self):
        super().setUp()
        self.credentials = {
            "api_key": "key",
            "api_secret": "secret",
            "access_token": "token",
            "access_secret": "token-secret",
        }

    def test_tweet_text_truncates_at_word_boundary(self):
        long_caption = "palavra " * 80
        text = post_x._tweet_text(long_caption)
        self.assertLessEqual(len(text), 280)
        self.assertTrue(text.endswith("…"))
        self.assertFalse(text.endswith(" …"))

    def test_tweet_text_keeps_short_caption(self):
        self.assertEqual(
            post_x._tweet_text("Legenda curta"), "Legenda curta"
        )

    def test_oauth1_header_shape(self):
        headers = post_x._oauth1_headers(
            "POST",
            "https://api.x.com/2/tweets",
            self.credentials,
            json_body={"text": "olá"},
        )
        auth = headers["Authorization"]
        self.assertTrue(auth.startswith("OAuth "))
        for field in (
            'oauth_consumer_key="key"',
            'oauth_signature_method="HMAC-SHA1"',
            'oauth_version="1.0"',
        ):
            self.assertIn(field, auth)

    def test_publishes_media_and_tweet(self):
        calls = []

        def fake_post(url, headers=None, files=None, data=None, timeout=None, json=None):
            calls.append(url)
            response = mock.Mock()
            response.ok = True
            response.headers = {"content-type": "application/json"}
            if url.endswith("/2/media/upload"):
                response.json.return_value = {"data": {"id": "media_9"}}
            else:
                response.json.return_value = {"data": {"id": "tweet_9"}}
            return response

        env = {
            "X_API_KEY": "key",
            "X_API_SECRET": "secret",
            "X_ACCESS_TOKEN": "token",
            "X_ACCESS_TOKEN_SECRET": "token-secret",
            "X_ACCOUNT_HANDLE": "@politometro_pt",
        }
        with mock.patch.dict("os.environ", env), mock.patch.object(
            post_x.requests, "post", side_effect=fake_post
        ):
            post_x.publish_x()

        self.assertEqual(
            calls, ["https://api.x.com/2/media/upload", "https://api.x.com/2/tweets"]
        )
        receipt = json.loads(self.x_receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["post_id"], "tweet_9")
        self.assertEqual(
            receipt["permalink"], "https://x.com/politometro_pt/status/tweet_9"
        )

    def test_is_idempotent_per_draft(self):
        _write_json(
            self.x_receipt,
            {
                "draft_id": "draft_social",
                "content_hash": "hash_social",
                "post_id": "already",
            },
        )
        with mock.patch.dict(
            "os.environ",
            {
                "X_API_KEY": "key",
                "X_API_SECRET": "secret",
                "X_ACCESS_TOKEN": "token",
                "X_ACCESS_TOKEN_SECRET": "token-secret",
                "X_ACCOUNT_HANDLE": "@politometro_pt",
            },
        ), mock.patch.object(post_x.requests, "post") as post_spy:
            post_x.publish_x()
        post_spy.assert_not_called()


class DiscordSuggestionsTests(unittest.TestCase):
    def test_format_quick_suggestions_numbered(self):
        text = discord_reviewer.format_quick_suggestions(
            ["Primeira?", "Segunda?", "Terceira?"]
        )
        self.assertIn("Perguntas rápidas", text)
        self.assertIn("1. Primeira?", text)
        self.assertIn("3. Terceira?", text)

    def test_format_quick_suggestions_empty_is_empty(self):
        self.assertEqual(discord_reviewer.format_quick_suggestions([]), "")

    def test_suggestions_use_dedicated_client_id(self):
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            response = mock.Mock()
            response.status_code = 200
            response.json.return_value = {
                "suggestions": ["Sugestão A?", "Sugestão B?", "Sugestão C?"]
            }
            return response

        with mock.patch.object(
            discord_reviewer.requests, "post", side_effect=fake_post
        ):
            result = discord_reviewer.query_chat_suggestions(
                "pergunta", "resposta", user_id="42"
            )

        self.assertEqual(result, ["Sugestão A?", "Sugestão B?", "Sugestão C?"])
        self.assertTrue(
            captured["url"].endswith("/api/chat/suggestions"),
            captured["url"],
        )
        self.assertEqual(
            captured["headers"]["x-client-id"], "discord-suggestions:42"
        )

    def test_suggestions_fall_back_to_starters_on_error(self):
        with mock.patch.object(
            discord_reviewer.requests,
            "post",
            side_effect=Exception("rede em baixo"),
        ):
            result = discord_reviewer.query_chat_suggestions(
                "pergunta", "resposta", user_id="42"
            )
        self.assertEqual(result, discord_reviewer.STARTER_SUGGESTIONS)
