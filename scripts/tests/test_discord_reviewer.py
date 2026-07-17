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
    def test_application_exposes_question_and_recommendation_commands(self):
        names = {command.name for command in discord_reviewer.bot.tree.get_commands()}
        self.assertIn("perguntar", names)
        self.assertIn("recomendar", names)

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


if __name__ == "__main__":
    unittest.main()
