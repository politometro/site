import json
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import auto_populate_ai
import discord_reviewer


def whole_podcast():
    return {
        "id": "web_podcast_1",
        "type": "podcast",
        "title": "Podcast Exemplo",
        "authorOrMeta": "Podcast Exemplo / Jornalista",
        "description": "Conversas sobre política.",
        "link": "https://podcasts.apple.com/pt/podcast/id12345",
        "imageUrl": "/covers/podcast.jpg",
        "sourceImageUrl": "https://cdn.example.com/podcast.jpg",
        "status": "pending_sent",
        "notificationStatus": "sent",
        "discordMessageId": "discord-message-1",
        "discordNotifiedAt": "2026-08-10T09:00:00+00:00",
        "discordApproval": {
            "required": True,
            "status": "pending",
            "channelId": "discord-channel-1",
            "messageId": "discord-message-1",
            "sentAt": "2026-08-10T09:00:00+00:00",
        },
        "resolutionStatus": "verified",
        "verification": {
            "status": "verified",
            "entityId": "apple:podcast:12345",
            "externalId": "apple:podcast:12345",
            "coverHash": "cover-hash",
        },
    }


class DiscordApplicationTests(unittest.TestCase):
    def setUp(self):
        discord_reviewer._discord_recommendation_limits.clear()

    def test_application_exposes_question_and_recommendation_commands(self):
        names = {command.name for command in discord_reviewer.bot.tree.get_commands()}
        self.assertIn("perguntar", names)
        self.assertIn("recomendar", names)

    def test_post_rejection_menu_has_all_actions_and_free_text(self):
        menu = discord_reviewer.RejectionReasonSelect(
            "123", "draft_123", "abc123"
        )
        values = {option.value for option in menu.options}
        self.assertEqual(
            values,
            {
                "bad_image",
                "wrong_covers",
                "typo_text",
                "typo_image_text",
                "bad_links",
                "bad_recs",
                "custom_feedback",
            },
        )

    def test_review_cover_updates_item_and_identity_manifest_hashes(self):
        item = {
            "resolutionStatus": "verified",
            "verification": {"coverHash": "old-hash"},
        }
        manifest = {
            "entityId": "openlibrary:/works/OL8975462W",
            "canonicalLink": "https://openlibrary.org/works/OL8975462W",
            "coverHash": "old-hash",
        }
        cover = b"normalized-reviewer-jpeg"

        result = discord_reviewer._apply_review_cover_metadata(
            item, manifest, cover, 664, 1000
        )

        expected = hashlib.sha256(cover).hexdigest()
        self.assertEqual(result, expected)
        self.assertEqual(item["verification"]["coverHash"], expected)
        self.assertEqual(manifest["coverHash"], expected)
        self.assertEqual(manifest["width"], 664)
        self.assertEqual(manifest["height"], 1000)
        self.assertEqual(
            item["verification"]["coverOverride"]["source"],
            "discord-review",
        )
        self.assertEqual(manifest["entityId"], "openlibrary:/works/OL8975462W")
        self.assertEqual(
            manifest["canonicalLink"],
            "https://openlibrary.org/works/OL8975462W",
        )

    def test_review_cover_filename_changes_with_image_bytes(self):
        original = "book_the_open_society_f77f3b04df26.jpg"

        first = discord_reviewer._review_cover_name(original, b"first-cover")
        second = discord_reviewer._review_cover_name(first, b"second-cover")

        self.assertNotEqual(first, second)
        self.assertRegex(first, r"_review_[0-9a-f]{12}\.jpg$")
        self.assertNotIn("_review_", second.removesuffix(".jpg").rsplit("_review_", 1)[0])

    def test_free_text_feedback_is_bound_to_exact_draft(self):
        draft = {
            "draft_id": "draft_123",
            "content_hash": "abc123fullhash",
            "approval": {"approved": False},
        }
        written = {}

        def fake_update(path, content, message, sha=None):
            written[path] = json.loads(content.decode("utf-8"))
            return True

        with (
            mock.patch.object(
                discord_reviewer,
                "get_github_file",
                return_value=(
                    json.dumps(draft).encode("utf-8"),
                    "draft-sha",
                ),
            ),
            mock.patch.object(
                discord_reviewer,
                "update_github_file",
                side_effect=fake_update,
            ),
        ):
            result = discord_reviewer._store_review_feedback(
                "draft_123",
                "abc123",
                "Substituir a notícia por um artigo de opinião.",
                SimpleNamespace(id=42, display_name="Revisor"),
            )

        self.assertTrue(result)
        feedback = written["scripts/review_draft.json"]["reviewFeedback"]
        self.assertEqual(len(feedback), 1)
        self.assertIn("artigo de opinião", feedback[0]["text"])
        self.assertEqual(feedback[0]["createdById"], "42")

    def test_post_approval_records_sunday_ten_in_lisbon(self):
        draft = {
            "draft_id": "draft_123",
            "content_hash": "abc123fullhash",
            "created_at": "2026-07-18T20:00:00+00:00",
            "is_test": False,
            "approval": {"approved": False},
        }
        written = {}

        def fake_update(path, content, message, sha=None):
            written[path] = json.loads(content.decode("utf-8"))
            return True

        with (
            mock.patch.object(
                discord_reviewer,
                "get_github_file",
                return_value=(
                    json.dumps(draft).encode("utf-8"),
                    "draft-sha",
                ),
            ),
            mock.patch.object(
                discord_reviewer,
                "update_github_file",
                side_effect=fake_update,
            ),
        ):
            result = discord_reviewer._approve_current_draft(
                "draft_123",
                "abc123",
                SimpleNamespace(id=42, display_name="Revisor"),
            )

        self.assertTrue(result)
        approval = written["scripts/review_draft.json"]["approval"]
        self.assertEqual(
            approval["scheduled_for"],
            "2026-07-19T09:00:00+00:00",
        )
        self.assertEqual(
            approval["scheduled_timezone"],
            "Europe/Lisbon",
        )

    def test_recommendation_command_exposes_all_website_types(self):
        choices = {
            choice.value
            for choice in discord_reviewer.RECOMMENDATION_TYPE_CHOICES
        }
        self.assertEqual(
            choices,
            {
                "book",
                "podcast",
                "movie",
                "nostalgia",
                "investigation",
                "highlight",
                "project",
            },
        )

    def test_public_recommendation_errors_hide_service_internals(self):
        message = discord_reviewer.public_recommendation_error(
            "O servidor recusou a recomendação (HTTP 503)."
        )
        self.assertNotIn("HTTP", message)
        self.assertNotIn("servidor", message.lower())
        self.assertIn("tenta novamente", message.lower())

    def test_expired_recommendation_error_suggests_recent_content(self):
        message = discord_reviewer.public_recommendation_error(
            "A fonte foi identificada, mas o prazo de relevância terminou."
        )
        self.assertIn("mais atual", message.lower())
        self.assertNotIn("fonte foi identificada", message.lower())

    def test_whole_podcast_is_distinguished_from_episode(self):
        show = whole_podcast()
        episode = whole_podcast()
        episode["verification"]["entityId"] = "apple:episode:987"
        episode["verification"]["externalId"] = "apple:episode:987"

        self.assertTrue(discord_reviewer._is_whole_podcast(show))
        self.assertFalse(discord_reviewer._is_whole_podcast(episode))

    def test_watch_approval_adds_watchlist_and_does_not_queue_whole_show(self):
        database = {"queue": [whole_podcast()], "history": []}
        written = {}

        def fake_update(path, content, message, sha=None):
            written[path] = json.loads(content.decode("utf-8"))
            return True

        with (
            mock.patch.object(
                discord_reviewer,
                "get_github_file",
                return_value=(
                    json.dumps(database, ensure_ascii=False).encode("utf-8"),
                    "recommendations-sha",
                ),
            ),
            mock.patch.object(
                discord_reviewer,
                "add_podcast_to_watchlist",
                return_value={
                    "status": "added",
                    "entry": {"appleCollectionId": "12345"},
                },
            ),
            mock.patch.object(
                discord_reviewer,
                "update_github_file",
                side_effect=fake_update,
            ),
        ):
            result = discord_reviewer.approve_recommendation(
                "web_podcast_1",
                SimpleNamespace(id=42),
                "watch",
                "discord-message-1",
                "discord-channel-1",
            )

        self.assertTrue(result["ok"])
        stored = written["website/public/recommendations.json"]["queue"][0]
        self.assertEqual(stored["status"], "watching")
        self.assertEqual(stored["approvalMode"], "watch")
        self.assertEqual(stored["watchlistCollectionId"], "12345")
        self.assertEqual(stored["discordApproval"]["status"], "approved")

    def test_approval_requires_the_delivered_discord_message(self):
        database = {"queue": [whole_podcast()], "history": []}
        with (
            mock.patch.object(
                discord_reviewer,
                "get_github_file",
                return_value=(json.dumps(database).encode(), "sha"),
            ),
            mock.patch.object(
                discord_reviewer, "update_github_file"
            ) as update_mock,
        ):
            result = discord_reviewer.approve_recommendation(
                "web_podcast_1",
                SimpleNamespace(id=42),
                "queue",
                "different-message",
                "discord-channel-1",
            )

        self.assertFalse(result["ok"])
        self.assertIn("não corresponde", result["error"])
        update_mock.assert_not_called()

    def test_unresolved_suggestion_is_approved_but_not_queued(self):
        item = whole_podcast()
        item.update(
            {
                "type": "book",
                "title": "Sugestão sem link",
                "link": "",
                "imageUrl": "",
                "resolutionStatus": "unresolved",
                "verification": {
                    "status": "unresolved",
                    "matchedFields": ["title"],
                },
            }
        )
        database = {"queue": [item], "history": []}
        written = {}

        def fake_update(path, content, message, sha=None):
            written[path] = json.loads(content.decode("utf-8"))
            return True

        with (
            mock.patch.object(
                discord_reviewer,
                "get_github_file",
                return_value=(json.dumps(database).encode(), "sha"),
            ),
            mock.patch.object(
                discord_reviewer,
                "update_github_file",
                side_effect=fake_update,
            ),
        ):
            result = discord_reviewer.approve_recommendation(
                "web_podcast_1",
                SimpleNamespace(id=42),
                "queue",
                "discord-message-1",
                "discord-channel-1",
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["queued"])
        stored = written["website/public/recommendations.json"]["queue"][0]
        self.assertEqual(stored["status"], "approved_pending_enrichment")
        self.assertEqual(stored["discordApproval"]["status"], "approved")

    def test_watchlist_append_is_idempotent_by_apple_collection_id(self):
        item = whole_podcast()
        existing = {
            "podcasts": [
                {
                    "name": "Podcast Exemplo",
                    "author": "Jornalista",
                    "appleCollectionId": "12345",
                }
            ]
        }
        with (
            mock.patch.object(
                discord_reviewer,
                "get_github_file",
                return_value=(
                    json.dumps(existing).encode("utf-8"),
                    "watchlist-sha",
                ),
            ),
            mock.patch.object(
                discord_reviewer,
                "_apple_podcast_metadata",
                return_value={},
            ),
            mock.patch.object(
                discord_reviewer, "update_github_file"
            ) as update_mock,
        ):
            result = discord_reviewer.add_podcast_to_watchlist(item)

        self.assertEqual(result["status"], "already_watched")
        update_mock.assert_not_called()

    def test_auto_population_uses_approved_collection_id_directly(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [
                        {
                            "collectionId": 12345,
                            "collectionName": "Podcast Exemplo",
                            "artistName": "Jornalista",
                            "feedUrl": "https://example.com/feed.xml",
                        }
                    ]
                }

        with mock.patch.object(
            auto_populate_ai.requests, "get", return_value=Response()
        ) as get_mock:
            result = auto_populate_ai._apple_show(
                {
                    "name": "Nome potencialmente ambíguo",
                    "author": "Autor",
                    "appleCollectionId": "12345",
                }
            )

        self.assertEqual(result["collectionId"], 12345)
        self.assertEqual(result["feedUrl"], "https://example.com/feed.xml")
        self.assertEqual(get_mock.call_args.args[0], "https://itunes.apple.com/lookup")

    def test_discord_recommendation_rate_limit_blocks_repeated_submissions(self):
        with mock.patch.object(
            discord_reviewer, "DISCORD_RECOMMENDATION_LIMIT", 2
        ):
            first = discord_reviewer._check_discord_recommendation_rate_limit(
                "42", now=1000
            )
            second = discord_reviewer._check_discord_recommendation_rate_limit(
                "42", now=1001
            )
            blocked = discord_reviewer._check_discord_recommendation_rate_limit(
                "42", now=1002
            )

        self.assertTrue(first["allowed"])
        self.assertTrue(second["allowed"])
        self.assertFalse(blocked["allowed"])
        self.assertGreater(blocked["retry_after_seconds"], 0)
        still_blocked = (
            discord_reviewer._check_discord_recommendation_rate_limit(
                "42",
                now=1000
                + discord_reviewer.DISCORD_RECOMMENDATION_WINDOW_SECONDS
                + 1,
            )
        )
        self.assertFalse(still_blocked["allowed"])

    def test_discord_recommendation_rate_limit_is_per_account(self):
        with mock.patch.object(
            discord_reviewer, "DISCORD_RECOMMENDATION_LIMIT", 1
        ):
            self.assertTrue(
                discord_reviewer._check_discord_recommendation_rate_limit(
                    "first", now=1000
                )["allowed"]
            )
            self.assertTrue(
                discord_reviewer._check_discord_recommendation_rate_limit(
                    "second", now=1001
                )["allowed"]
            )


class PostApprovalButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_schedules_without_triggering_immediate_publication(self):
        view = discord_reviewer.PostReviewView()
        interaction = SimpleNamespace(
            user=SimpleNamespace(
                id=42,
                mention="@revisor",
                display_name="Revisor",
            ),
            response=SimpleNamespace(defer=mock.AsyncMock()),
            followup=SimpleNamespace(send=mock.AsyncMock()),
            message=SimpleNamespace(edit=mock.AsyncMock()),
        )

        with (
            mock.patch.object(
                discord_reviewer,
                "_is_authorized_reviewer",
                return_value=True,
            ),
            mock.patch.object(
                discord_reviewer,
                "_review_identity_from_message",
                return_value=("draft_123", "abc123"),
            ),
            mock.patch.object(
                discord_reviewer,
                "_approve_current_draft",
                return_value=True,
            ),
            mock.patch.object(
                discord_reviewer,
                "trigger_github_workflow",
            ) as trigger,
        ):
            await view.approve_button.callback(interaction)

        trigger.assert_not_called()
        edit_content = interaction.message.edit.await_args.kwargs["content"]
        self.assertIn("agendada", edit_content.lower())
        self.assertIn("domingo às 10:00", edit_content)

    async def test_replace_review_cover_updates_manifest_recommendations_and_draft(self):
        draft = {
            "q1": {
                "id": "rec_test_1",
                "imageUrl": "/covers/book_test_cover.jpg",
                "verification": {},
            }
        }
        manifest = {
            "entityId": "openlibrary:/works/OL12345W",
            "canonicalLink": "https://openlibrary.org/works/OL12345W",
            "coverHash": "old-hash",
        }
        recs = {
            "queue": [
                {
                    "id": "rec_test_1",
                    "imageUrl": "/covers/book_test_cover.jpg",
                    "verification": {},
                }
            ],
            "history": [],
        }
        written = {}

        def fake_get(path):
            if "review_draft" in path:
                return json.dumps(draft).encode("utf-8"), "draft-sha"
            if "book_test_cover.json" in path:
                return json.dumps(manifest).encode("utf-8"), "manifest-sha"
            if "recommendations.json" in path:
                return json.dumps(recs).encode("utf-8"), "rec-sha"
            return None, "not found"

        def fake_update(path, content, message, sha=None):
            written[path] = content
            return True

        from io import BytesIO
        from PIL import Image

        img = Image.new("RGB", (100, 150), color="blue")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        raw_bytes = buf.getvalue()

        with (
            mock.patch.object(
                discord_reviewer, "get_github_file", side_effect=fake_get
            ),
            mock.patch.object(
                discord_reviewer, "update_github_file", side_effect=fake_update
            ),
            mock.patch.object(
                discord_reviewer, "trigger_github_workflow", return_value=True
            ),
        ):
            result = await discord_reviewer.replace_review_cover(
                "123456", "q1", raw_bytes
            )

        self.assertTrue(result)
        self.assertIn("website/public/recommendations.json", written)
        self.assertIn("scripts/review_draft.json", written)
        updated_draft = json.loads(written["scripts/review_draft.json"].decode("utf-8"))
        self.assertTrue(updated_draft["q1"]["imageUrl"].startswith("/covers/book_test_cover_review_"))
        self.assertTrue(updated_draft["q1"]["imageUrl"].endswith(".jpg"))

    async def test_manual_cover_file_attachment_uses_read_method(self):
        fake_attachment = SimpleNamespace(
            url="https://cdn.discordapp.com/attachments/123/456/cover.jpg",
            read=mock.AsyncMock(return_value=b"fake-image-bytes"),
        )
        fake_message = SimpleNamespace(
            author=SimpleNamespace(bot=False, id=42),
            channel=SimpleNamespace(id=discord_reviewer.CHANNEL_ID),
            mentions=[],
            reference=None,
            content="",
            attachments=[fake_attachment],
            reply=mock.AsyncMock(),
        )

        discord_reviewer.waiting_for_image_quadrant = {
            "quadrant": "q1",
            "original_msg_id": "123456",
        }

        with (
            mock.patch.object(
                discord_reviewer,
                "get_github_file",
                return_value=(
                    json.dumps({"q1": {"id": "rec_1"}}).encode("utf-8"),
                    "sha",
                ),
            ),
            mock.patch.object(
                discord_reviewer,
                "replace_review_cover",
                return_value=True,
            ) as mock_replace,
            mock.patch.object(
                discord_reviewer,
                "mark_review_superseded",
                return_value=True,
            ),
            mock.patch.object(
                discord_reviewer.bot,
                "get_context",
                return_value=SimpleNamespace(valid=False),
            ),
        ):
            await discord_reviewer.on_message(fake_message)

        fake_attachment.read.assert_awaited_once()
        mock_replace.assert_awaited_once_with(
            "123456", "q1", b"fake-image-bytes"
        )
        self.assertIsNone(discord_reviewer.waiting_for_image_quadrant)


if __name__ == "__main__":
    unittest.main()

