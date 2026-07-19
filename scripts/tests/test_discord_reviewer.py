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

    def test_recommendation_command_exposes_same_five_types_as_website(self):
        choices = {
            choice.value
            for choice in discord_reviewer.RECOMMENDATION_TYPE_CHOICES
        }
        self.assertEqual(
            choices,
            {"book", "podcast", "movie", "highlight", "project"},
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
            )

        self.assertTrue(result["ok"])
        stored = written["website/public/recommendations.json"]["queue"][0]
        self.assertEqual(stored["status"], "watching")
        self.assertEqual(stored["approvalMode"], "watch")
        self.assertEqual(stored["watchlistCollectionId"], "12345")

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


if __name__ == "__main__":
    unittest.main()
