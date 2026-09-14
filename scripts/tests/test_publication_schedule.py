import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import publication_schedule


def approved_draft(created_at, *, scheduled_for=None):
    draft = {
        "schema_version": 2,
        "draft_id": "draft-schedule",
        "content_hash": "content-hash",
        "created_at": created_at,
        "is_test": False,
        "approval": {
            "approved": True,
            "draft_id": "draft-schedule",
            "content_hash": "content-hash",
            "approved_at": created_at,
        },
    }
    if scheduled_for:
        draft["approval"]["scheduled_for"] = scheduled_for
        draft["approval"]["scheduled_timezone"] = "Europe/Lisbon"
    return draft


class PublicationScheduleTests(unittest.TestCase):
    def test_summer_schedule_uses_ten_in_lisbon(self):
        draft = approved_draft("2026-07-18T20:00:00+00:00")

        at_ten = publication_schedule.publication_decision(
            draft,
            now=datetime.datetime(
                2026, 7, 19, 9, 0, tzinfo=datetime.timezone.utc
            ),
        )
        at_eleven = publication_schedule.publication_decision(
            draft,
            now=datetime.datetime(
                2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc
            ),
        )

        self.assertTrue(at_ten[0])
        self.assertTrue(at_eleven[0])
        self.assertEqual(
            at_ten[2].isoformat(),
            "2026-07-19T09:00:00+00:00",
        )

    def test_winter_schedule_uses_ten_in_lisbon(self):
        draft = approved_draft("2027-01-02T20:00:00+00:00")

        at_nine = publication_schedule.publication_decision(
            draft,
            now=datetime.datetime(
                2027, 1, 3, 9, 0, tzinfo=datetime.timezone.utc
            ),
        )
        at_ten = publication_schedule.publication_decision(
            draft,
            now=datetime.datetime(
                2027, 1, 3, 10, 0, tzinfo=datetime.timezone.utc
            ),
        )

        self.assertFalse(at_nine[0])
        self.assertTrue(at_ten[0])
        self.assertEqual(
            at_ten[2].isoformat(),
            "2027-01-03T10:00:00+00:00",
        )

    def test_window_starts_at_ten_and_accepts_same_sunday_recovery(self):
        draft = approved_draft("2026-07-18T20:00:00+00:00")
        cases = (
            (datetime.datetime(2026, 7, 18, 9, 30, tzinfo=datetime.timezone.utc), False),
            (datetime.datetime(2026, 7, 19, 8, 59, tzinfo=datetime.timezone.utc), False),
            (datetime.datetime(2026, 7, 19, 9, 59, tzinfo=datetime.timezone.utc), True),
            (datetime.datetime(2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc), True),
            (datetime.datetime(2026, 7, 19, 18, 0, tzinfo=datetime.timezone.utc), True),
            (datetime.datetime(2026, 7, 26, 9, 0, tzinfo=datetime.timezone.utc), False),
        )

        for now, expected in cases:
            with self.subTest(now=now.isoformat()):
                self.assertEqual(
                    publication_schedule.publication_decision(
                        draft, now=now
                    )[0],
                    expected,
                )

    def test_invalid_or_already_published_drafts_are_skipped(self):
        now = datetime.datetime(
            2026, 7, 19, 9, 5, tzinfo=datetime.timezone.utc
        )
        draft = approved_draft("2026-07-18T20:00:00+00:00")

        unapproved = json.loads(json.dumps(draft))
        unapproved["approval"]["approved"] = False
        self.assertFalse(
            publication_schedule.publication_decision(
                unapproved, now=now
            )[0]
        )

        mismatched = json.loads(json.dumps(draft))
        mismatched["approval"]["content_hash"] = "different"
        self.assertFalse(
            publication_schedule.publication_decision(
                mismatched, now=now
            )[0]
        )

        test_draft = json.loads(json.dumps(draft))
        test_draft["is_test"] = True
        self.assertFalse(
            publication_schedule.publication_decision(
                test_draft, now=now
            )[0]
        )

        receipt = {
            "draft_id": draft["draft_id"],
            "content_hash": draft["content_hash"],
            "post_id": "instagram-post",
        }
        self.assertFalse(
            publication_schedule.publication_decision(
                draft, receipt, now=now
            )[0]
        )

    def test_wednesday_nostalgia_is_disabled_even_with_manual_override(self):
        draft = approved_draft("2026-08-08T20:00:00+00:00")
        draft["post_type"] = "wednesday_nostalgia"

        should_publish, reason, scheduled_for = (
            publication_schedule.publication_decision(
                draft,
                now=datetime.datetime(
                    2026, 8, 12, 10, 0, tzinfo=datetime.timezone.utc
                ),
                force_now=True,
            )
        )

        self.assertFalse(should_publish)
        self.assertIn("desativada", reason)
        self.assertIsNone(scheduled_for)

    def test_manual_override_can_publish_outside_window_without_bypassing_approval(self):
        draft = approved_draft("2026-07-18T20:00:00+00:00")
        outside_window = datetime.datetime(
            2026, 7, 20, 10, 30, tzinfo=datetime.timezone.utc
        )

        normal = publication_schedule.publication_decision(
            draft, now=outside_window
        )
        forced = publication_schedule.publication_decision(
            draft, now=outside_window, force_now=True
        )

        self.assertFalse(normal[0])
        self.assertTrue(forced[0])

        unapproved = json.loads(json.dumps(draft))
        unapproved["approval"]["approved"] = False
        self.assertFalse(
            publication_schedule.publication_decision(
                unapproved, now=outside_window, force_now=True
            )[0]
        )

    def test_github_output_skips_cleanly_when_there_is_no_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "github-output.txt"
            with (
                mock.patch.object(
                    publication_schedule,
                    "DRAFT_PATH",
                    str(Path(temporary) / "missing-draft.json"),
                ),
                mock.patch.object(
                    publication_schedule,
                    "RECEIPT_PATH",
                    str(Path(temporary) / "missing-receipt.json"),
                ),
                mock.patch.dict(
                    "os.environ",
                    {"GITHUB_OUTPUT": str(output)},
                    clear=False,
                ),
            ):
                should_publish, reason, target = (
                    publication_schedule.current_publication_decision()
                )
                publication_schedule._write_github_output(
                    should_publish, reason, target
                )

            result = output.read_text(encoding="utf-8")
            self.assertIn("should_publish=false", result)

    def test_workflow_uses_lisbon_time_and_checks_guard_before_meta(self):
        workflow_path = (
            Path(__file__).resolve().parents[2]
            / ".github"
            / "workflows"
            / "instagram_publish.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertNotIn("cron: '0 9 * * 3'", workflow)
        self.assertNotIn("cron: '7 9 * * 3'", workflow)
        self.assertNotIn("cron: '17 9 * * 3'", workflow)
        self.assertIn("cron: '0 9 * * 0'", workflow)
        self.assertIn("cron: '7 9 * * 0'", workflow)
        self.assertIn("cron: '17 9 * * 0'", workflow)
        self.assertIn("cron: '0 10 * * 0'", workflow)
        self.assertIn("cron: '7 10 * * 0'", workflow)
        self.assertIn("cron: '17 10 * * 0'", workflow)
        self.assertNotIn("timezone:", workflow)
        self.assertIn("publish_now:", workflow)
        self.assertIn("FORCE_PUBLISH_NOW:", workflow)
        self.assertIn("repository_dispatch:", workflow)
        self.assertIn("types: [weekly-publish]", workflow)
        self.assertIn(
            "github.event.schedule == '17 10 * * 0'",
            workflow,
        )
        self.assertNotIn(
            "github.event.schedule == '17 9 * * 3'",
            workflow,
        )
        self.assertLess(
            workflow.index("Check Approved Publication Window"),
            workflow.index("Validate Meta Access"),
        )
        self.assertLess(
            workflow.index("Validate Meta Access"),
            workflow.index("Prepare Instagram Container"),
        )

        test_workflow = (
            workflow_path.parent / "instagram_test.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", test_workflow)
        self.assertIn("--check-access", test_workflow)
        self.assertIn("--prepare", test_workflow)
        self.assertNotIn("--publish", test_workflow)
        self.assertNotIn("--commit", test_workflow)


if __name__ == "__main__":
    unittest.main()


class UndecidedDraftAutopublishTests(unittest.TestCase):
    """Rascunhos entregues no Discord sem aprovação nem rejeição."""

    def setUp(self):
        self.created_at = "2026-07-18T20:00:00+00:00"
        self.draft = {
            "schema_version": 2,
            "draft_id": "draft-undecided",
            "content_hash": "hash-undecided",
            "created_at": self.created_at,
            "is_test": False,
            "approval": {"approved": False},
            "q1": {"id": "item-1"},
            "q2": {"id": "item-2"},
            "q3": {"id": "item-3"},
            "q4": {"id": "item-4"},
        }
        self.notification = {
            "schema_version": 1,
            "draft_id": "draft-undecided",
            "content_hash": "hash-undecided",
            "review_message_id": "111",
            "caption_message_id": "222",
        }
        self.recommendations = {
            "queue": [
                {"id": "item-1", "status": "queue"},
                {"id": "item-2", "status": "queue"},
                {"id": "item-3", "status": "queue"},
                {"id": "item-4", "status": "queue"},
            ]
        }

    def _patch_files(self):
        return (
            mock.patch.object(
                publication_schedule,
                "REVIEW_NOTIFICATION_PATH",
                "review_notification.json",
            ),
            mock.patch.object(
                publication_schedule,
                "RECOMMENDATIONS_PATH",
                "recommendations.json",
            ),
            mock.patch.object(
                publication_schedule,
                "_load_optional",
                side_effect=lambda path: (
                    self.notification
                    if path == "review_notification.json"
                    else self.recommendations
                ),
            ),
        )

    def test_undecided_delivered_draft_publishes_in_window(self):
        patches = self._patch_files()
        with patches[0], patches[1], patches[2]:
            decision = publication_schedule.publication_decision(
                self.draft,
                now=datetime.datetime(
                    2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc
                ),
            )

        self.assertTrue(decision[0])
        self.assertIn("publicação automática", decision[1])
        self.assertEqual(
            decision[2].isoformat(),
            "2026-07-19T09:00:00+00:00",
        )

    def test_undecided_draft_without_delivery_is_not_published(self):
        self.notification = {}
        patches = self._patch_files()
        with patches[0], patches[1], patches[2]:
            decision = publication_schedule.publication_decision(
                self.draft,
                now=datetime.datetime(
                    2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc
                ),
            )

        self.assertFalse(decision[0])
        self.assertIn("ainda não foi aprovado", decision[1])

    def test_rejected_items_block_autopublish(self):
        self.recommendations["queue"][2]["status"] = "skip"
        patches = self._patch_files()
        with patches[0], patches[1], patches[2]:
            decision = publication_schedule.publication_decision(
                self.draft,
                now=datetime.datetime(
                    2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc
                ),
            )

        self.assertFalse(decision[0])

    def test_reviewer_feedback_blocks_autopublish(self):
        self.draft["reviewFeedback"] = [
            {"text": "trocar o quadrante 3", "createdAt": self.created_at}
        ]
        patches = self._patch_files()
        with patches[0], patches[1], patches[2]:
            decision = publication_schedule.publication_decision(
                self.draft,
                now=datetime.datetime(
                    2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc
                ),
            )

        self.assertFalse(decision[0])

    def test_env_flag_disables_autopublish(self):
        patches = self._patch_files()
        with patches[0], patches[1], patches[2]:
            with mock.patch.dict(
                "os.environ",
                {"AUTO_PUBLISH_UNDECIDED_DRAFTS": "false"},
            ):
                decision = publication_schedule.publication_decision(
                    self.draft,
                    now=datetime.datetime(
                        2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc
                    ),
                )

        self.assertFalse(decision[0])

    def test_test_draft_never_autopublishes(self):
        self.draft["is_test"] = True
        patches = self._patch_files()
        with patches[0], patches[1], patches[2]:
            decision = publication_schedule.publication_decision(
                self.draft,
                now=datetime.datetime(
                    2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc
                ),
            )

        self.assertFalse(decision[0])
