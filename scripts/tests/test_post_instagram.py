"""Idempotency tests for the durable Instagram publication transaction."""

import datetime
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from scripts import post_instagram


class FakeResponse:
    def __init__(self, payload, *, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeMetaSession:
    def __init__(self, publish_outcome="success"):
        self.publish_outcome = publish_outcome
        self.create_calls = 0
        self.publish_calls = 0
        self.recent_media = []
        self.creation_ids = []

    def post(self, url, data, timeout):
        if url.endswith("/media_publish"):
            self.publish_calls += 1
            if self.publish_outcome == "timeout_after_success":
                self.recent_media.append(
                    {
                        "id": "post-after-timeout",
                        "caption": TEST_CAPTION,
                        "timestamp": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                        "media_type": "IMAGE",
                    }
                )
                raise requests.Timeout("response lost")
            if self.publish_outcome == "error":
                return FakeResponse(
                    {"error": {"message": "temporary failure"}},
                    ok=False,
                    status_code=500,
                )
            self.recent_media.append(
                {
                    "id": "post-success",
                    "caption": TEST_CAPTION,
                    "timestamp": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    "media_type": "IMAGE",
                }
            )
            return FakeResponse({"id": "post-success"})

        if url.endswith("/media"):
            self.create_calls += 1
            creation_id = f"container-{self.create_calls}"
            self.creation_ids.append(creation_id)
            return FakeResponse({"id": creation_id})
        raise AssertionError(f"Unexpected POST {url}")

    def get(self, url, params, timeout):
        if url.endswith("/account-123/media"):
            return FakeResponse({"data": list(self.recent_media)})
        if url.endswith("/account-123"):
            return FakeResponse(
                {"id": "account-123", "username": "politometro"}
            )
        if "/container-" in url:
            return FakeResponse(
                {"status_code": "FINISHED", "status": "Ready"}
            )
        raise AssertionError(f"Unexpected GET {url}")


TEST_CAPTION = "Legenda revista e exata.\n#politometro\n"


class InstagramIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.draft_path = root / "review_draft.json"
        self.receipt_path = root / "instagram_publication.json"
        self.caption_path = root / "current_caption.txt"
        self.caption_path.write_text(TEST_CAPTION, encoding="utf-8")
        caption_hash = hashlib.sha256(
            TEST_CAPTION.encode("utf-8")
        ).hexdigest()
        self.draft = {
            "schema_version": 2,
            "draft_id": "draft-123",
            "content_hash": "content-hash-123",
            "caption_sha256": caption_hash,
            "is_test": False,
            "approval": {
                "approved": True,
                "draft_id": "draft-123",
                "content_hash": "content-hash-123",
            },
        }
        self.draft_path.write_text(
            json.dumps(self.draft), encoding="utf-8"
        )
        self.paths = mock.patch.multiple(
            post_instagram,
            DRAFT_PATH=str(self.draft_path),
            RECEIPT_PATH=str(self.receipt_path),
            CAPTION_PATH=str(self.caption_path),
            RECONCILE_ATTEMPTS=2,
            RECONCILE_DELAY_SECONDS=0,
            CONTAINER_POLL_SECONDS=0,
        )
        self.paths.start()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_BUSINESS_ACCOUNT_ID": "account-123",
                "FACEBOOK_ACCESS_TOKEN": "secret-token",
                "GITHUB_REPOSITORY": "owner/repository",
                "GITHUB_SHA": "abc123",
                "META_GRAPH_API_VERSION": "v25.0",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.paths.stop()
        self.temporary.cleanup()

    def receipt(self):
        return json.loads(self.receipt_path.read_text(encoding="utf-8"))

    def test_timeout_after_remote_success_is_reconciled_without_duplicate(self):
        session = FakeMetaSession("timeout_after_success")

        prepared = post_instagram.prepare_publication(session)
        self.assertEqual(prepared["state"], "prepared")
        self.assertEqual(prepared["creation_id"], "container-1")
        self.assertFalse(prepared.get("post_id"))

        marked = post_instagram.mark_publishing()
        self.assertEqual(marked["state"], "publishing")

        confirmed = post_instagram.publish_or_reconcile(session)
        self.assertEqual(confirmed["state"], "confirmed")
        self.assertEqual(confirmed["post_id"], "post-after-timeout")
        self.assertEqual(
            confirmed["confirmation_source"],
            "recent_media_reconciliation",
        )
        self.assertEqual(session.create_calls, 1)
        self.assertEqual(session.publish_calls, 1)

        # A workflow rerun reuses the confirmed receipt and does not contact
        # either container-creation or media_publish again.
        post_instagram.prepare_publication(session)
        post_instagram.mark_publishing()
        rerun = post_instagram.publish_or_reconcile(session)
        self.assertEqual(rerun["post_id"], "post-after-timeout")
        self.assertEqual(session.create_calls, 1)
        self.assertEqual(session.publish_calls, 1)

    def test_prepare_reuses_pending_receipt_and_never_creates_new_container(self):
        session = FakeMetaSession()
        first = post_instagram.prepare_publication(session)
        second = post_instagram.prepare_publication(session)

        self.assertEqual(first["creation_id"], "container-1")
        self.assertEqual(second["creation_id"], "container-1")
        self.assertEqual(session.create_calls, 1)
        self.assertEqual(self.receipt()["state"], "prepared")

    def test_meta_access_is_checked_without_creating_a_container(self):
        session = FakeMetaSession()

        result = post_instagram.validate_meta_access(session)

        self.assertEqual(result["id"], "account-123")
        self.assertEqual(result["username"], "politometro")
        self.assertEqual(session.create_calls, 0)
        self.assertEqual(session.publish_calls, 0)

    def test_oauth_200_error_explains_the_required_account_authorization(self):
        message = post_instagram._meta_failure_message(
            {
                "error": {
                    "message": (
                        "Cannot call API for app 1640113147677812 on behalf "
                        "of user 122094188331404556"
                    ),
                    "type": "OAuthException",
                    "code": 200,
                }
            },
            "criar o contentor do Instagram",
        )

        self.assertIn("FACEBOOK_ACCESS_TOKEN", message)
        self.assertIn("token válido da Página", message)
        self.assertIn("instagram_content_publish", message)
        self.assertNotIn("1640113147677812", message)
        self.assertNotIn("122094188331404556", message)

    def test_failed_publish_rerun_reuses_same_pending_creation_id(self):
        failed_session = FakeMetaSession("error")
        post_instagram.prepare_publication(failed_session)
        post_instagram.mark_publishing()

        with self.assertRaisesRegex(
            RuntimeError, "creation_id ficou pendente"
        ):
            post_instagram.publish_or_reconcile(failed_session)
        pending = self.receipt()
        self.assertEqual(pending["state"], "publishing")
        self.assertEqual(pending["creation_id"], "container-1")

        successful_session = FakeMetaSession("success")
        reused = post_instagram.prepare_publication(successful_session)
        self.assertEqual(reused["creation_id"], "container-1")
        self.assertEqual(successful_session.create_calls, 0)
        post_instagram.mark_publishing()
        confirmed = post_instagram.publish_or_reconcile(
            successful_session
        )
        self.assertEqual(confirmed["post_id"], "post-success")
        self.assertEqual(successful_session.create_calls, 0)
        self.assertEqual(successful_session.publish_calls, 1)

    def test_reconciliation_requires_exact_caption_and_recent_timestamp(self):
        session = FakeMetaSession()
        post_instagram.prepare_publication(session)
        post_instagram.mark_publishing()
        session.recent_media.extend(
            [
                {
                    "id": "wrong-caption",
                    "caption": TEST_CAPTION + "alterada",
                    "timestamp": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                },
                {
                    "id": "too-old",
                    "caption": TEST_CAPTION,
                    "timestamp": (
                        datetime.datetime.now(datetime.timezone.utc)
                        - datetime.timedelta(days=2)
                    ).isoformat(),
                },
            ]
        )

        confirmed = post_instagram.publish_or_reconcile(session)
        self.assertEqual(confirmed["post_id"], "post-success")
        self.assertEqual(session.publish_calls, 1)


if __name__ == "__main__":
    unittest.main()
