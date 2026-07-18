import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import notify_rejected_approvals as notifier


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.posts = []

    def get(self, *args, **kwargs):
        return FakeResponse(self.existing)

    def post(self, *args, **kwargs):
        self.posts.append(kwargs["json"])
        return FakeResponse({"id": "discord-message-1"})


class RejectedApprovalNotificationTests(unittest.TestCase):
    def test_public_reason_does_not_expose_internal_error(self):
        self.assertEqual(
            notifier.public_reason(
                "BOOK_NOT_FOUND [Livro]: internal catalogue details"
            ),
            "A fonte já não confirma com segurança esta recomendação.",
        )

    def test_approved_rejection_is_sent_and_persisted_once(self):
        data = {
            "queue": [
                {
                    "id": "approved-1",
                    "type": "book",
                    "category": "Livro",
                    "title": "Livro aprovado",
                    "status": "invalid",
                    "resolutionStatus": "rejected",
                    "validationError": "BOOK_NOT_FOUND: details",
                    "approvedAt": "2026-07-18T10:00:00Z",
                    "approvedBy": "123456",
                }
            ],
            "history": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            rec_file = Path(temporary) / "recommendations.json"
            rec_file.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            session = FakeSession()
            with (
                mock.patch.object(notifier, "REC_FILE", str(rec_file)),
                mock.patch.dict(
                    os.environ,
                    {
                        "DISCORD_BOT_TOKEN": "token",
                        "DISCORD_REVIEW_CHANNEL_ID": "channel",
                    },
                    clear=False,
                ),
            ):
                self.assertEqual(
                    notifier.notify_rejected_approvals(session), 1
                )
                self.assertEqual(len(session.posts), 1)
                stored = json.loads(rec_file.read_text(encoding="utf-8"))
                marker = stored["queue"][0][
                    "workflowRejectionNotification"
                ]
                self.assertEqual(marker["status"], "sent")
                self.assertEqual(marker["messageId"], "discord-message-1")

                self.assertEqual(
                    notifier.notify_rejected_approvals(session), 0
                )
                self.assertEqual(len(session.posts), 1)

    def test_existing_discord_alert_is_reconciled_without_duplicate(self):
        item_id = "approved-existing"
        data = {
            "queue": [
                {
                    "id": item_id,
                    "type": "movie",
                    "title": "Filme aprovado",
                    "status": "invalid",
                    "resolutionStatus": "rejected",
                    "validationError": "HTTP_ERROR: 404",
                    "approvedAt": "2026-07-18T10:00:00Z",
                    "approvedBy": "654321",
                }
            ],
            "history": [],
        }
        existing = [
            {
                "id": "existing-message",
                "embeds": [
                    {
                        "footer": {
                            "text": (
                                f"Recommendation ID: {item_id} | "
                                "workflow-rejected"
                            )
                        }
                    }
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            rec_file = Path(temporary) / "recommendations.json"
            rec_file.write_text(json.dumps(data), encoding="utf-8")
            session = FakeSession(existing)
            with (
                mock.patch.object(notifier, "REC_FILE", str(rec_file)),
                mock.patch.dict(
                    os.environ,
                    {
                        "DISCORD_BOT_TOKEN": "token",
                        "DISCORD_REVIEW_CHANNEL_ID": "channel",
                    },
                    clear=False,
                ),
            ):
                self.assertEqual(
                    notifier.notify_rejected_approvals(session), 0
                )
                self.assertEqual(session.posts, [])
                stored = json.loads(rec_file.read_text(encoding="utf-8"))
                self.assertEqual(
                    stored["queue"][0]["workflowRejectionNotification"][
                        "messageId"
                    ],
                    "existing-message",
                )


if __name__ == "__main__":
    unittest.main()
