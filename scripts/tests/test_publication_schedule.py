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
        self.assertFalse(at_eleven[0])
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

    def test_window_is_bounded_to_the_target_sunday_ten_o_clock_hour(self):
        draft = approved_draft("2026-07-18T20:00:00+00:00")
        cases = (
            (datetime.datetime(2026, 7, 18, 9, 30, tzinfo=datetime.timezone.utc), False),
            (datetime.datetime(2026, 7, 19, 8, 59, tzinfo=datetime.timezone.utc), False),
            (datetime.datetime(2026, 7, 19, 9, 59, tzinfo=datetime.timezone.utc), True),
            (datetime.datetime(2026, 7, 19, 10, 0, tzinfo=datetime.timezone.utc), False),
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

        self.assertIn("cron: '0 10 * * 0'", workflow)
        self.assertIn("cron: '7 10 * * 0'", workflow)
        self.assertIn("cron: '17 10 * * 0'", workflow)
        self.assertIn("timezone: 'Europe/Lisbon'", workflow)
        self.assertIn(
            "github.event.schedule == '17 10 * * 0'",
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
