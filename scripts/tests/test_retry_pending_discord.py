import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import retry_pending_discord as outbox


def verified_book(item_id):
    return {
        "id": item_id,
        "type": "book",
        "category": "Livro",
        "title": f"Livro {item_id}",
        "authorOrMeta": "Autor",
        "description": "Descrição confirmada.",
        "link": f"https://example.com/{item_id}",
        "imageUrl": f"/covers/{item_id}.jpg",
        "status": "pending_approval",
        "resolutionStatus": "verified",
        "verification": {
            "status": "verified",
            "entityId": f"isbn:{item_id}",
            "coverHash": f"hash-{item_id}",
            "source": "test",
        },
    }


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class DiscordOutboxTests(unittest.TestCase):
    def test_each_success_is_checkpointed_before_later_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec_path = Path(tmp) / "recommendations.json"
            rec_path.write_text(
                json.dumps(
                    {
                        "queue": [
                            verified_book("first"),
                            verified_book("second"),
                        ],
                        "history": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            class Session:
                def __init__(self):
                    self.posts = 0

                def get(self, *args, **kwargs):
                    return FakeResponse(payload=[])

                def post(self, *args, **kwargs):
                    self.posts += 1
                    if self.posts == 1:
                        return FakeResponse(payload={"id": "discord-first"})
                    raise requests.Timeout("ambiguous timeout")

            with (
                mock.patch.object(outbox, "REC_FILE", str(rec_path)),
                mock.patch.object(outbox.requests, "Session", return_value=Session()),
                mock.patch.dict(
                    os.environ,
                    {
                        "DISCORD_BOT_TOKEN": "token",
                        "DISCORD_REVIEW_CHANNEL_ID": "channel",
                    },
                    clear=False,
                ),
                self.assertRaises(requests.Timeout),
            ):
                outbox.main()

            stored = json.loads(rec_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["queue"][0]["status"], "pending_sent")
            self.assertEqual(
                stored["queue"][0]["discordMessageId"], "discord-first"
            )
            self.assertEqual(
                stored["queue"][1]["status"], "pending_approval"
            )

    def test_delivery_dead_letter_reactivates_after_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec_path = Path(tmp) / "recommendations.json"
            item = verified_book("recover")
            item.update(
                {
                    "status": "notification_failed",
                    "notificationStatus": "dead_letter",
                    "notificationFailureKind": "delivery",
                    "notificationAttempts": outbox.MAX_NOTIFICATION_ATTEMPTS,
                    "notificationFailedAt": (
                        datetime.datetime.now(datetime.timezone.utc)
                        - datetime.timedelta(
                            days=outbox.DELIVERY_REACTIVATION_DAYS + 1
                        )
                    ).isoformat(),
                }
            )
            rec_path.write_text(
                json.dumps({"queue": [item], "history": []}, ensure_ascii=False),
                encoding="utf-8",
            )

            class Session:
                def get(self, *args, **kwargs):
                    return FakeResponse(payload=[])

                def post(self, *args, **kwargs):
                    return FakeResponse(payload={"id": "discord-recovered"})

            with (
                mock.patch.object(outbox, "REC_FILE", str(rec_path)),
                mock.patch.object(outbox.requests, "Session", return_value=Session()),
                mock.patch.dict(
                    os.environ,
                    {
                        "DISCORD_BOT_TOKEN": "token",
                        "DISCORD_REVIEW_CHANNEL_ID": "channel",
                    },
                    clear=False,
                ),
            ):
                outbox.main()

            stored = json.loads(rec_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["queue"][0]["status"], "pending_sent")
            self.assertEqual(
                stored["queue"][0]["discordMessageId"], "discord-recovered"
            )

    def test_rate_limit_uses_retry_after(self):
        response = FakeResponse(
            status_code=429,
            payload={"retry_after": 37},
        )
        self.assertEqual(
            outbox._retry_delay(response, attempts=1),
            datetime.timedelta(seconds=37),
        )


if __name__ == "__main__":
    unittest.main()
