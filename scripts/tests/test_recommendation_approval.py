import copy
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from recommendation_approval import (
    COMMUNITY_SOURCE_KIND,
    community_submission_hash,
    has_valid_discord_delivery,
    has_verified_discord_approval,
    is_post_workflow_eligible,
    requires_discord_approval,
)


def approved_community_item():
    item = {
        "id": "web_book_1",
        "origin": "website",
        "sourceKind": COMMUNITY_SOURCE_KIND,
        "type": "book",
        "title": "Ensaio sobre a Lucidez",
        "link": "https://example.com/livro",
        "createdAt": "2026-08-10T09:00:00+00:00",
        "status": "queue",
        "notificationStatus": "sent",
        "discordMessageId": "100000000000000001",
    }
    item["communitySubmission"] = {
        "id": item["id"],
        "sourceKind": item["sourceKind"],
        "type": item["type"],
        "title": item["title"],
        "link": item["link"],
        "createdAt": item["createdAt"],
    }
    item["submissionHash"] = community_submission_hash(item)
    item["discordApproval"] = {
        "required": True,
        "status": "approved",
        "channelId": "200000000000000002",
        "messageId": item["discordMessageId"],
        "sentAt": "2026-08-10T09:01:00+00:00",
        "payloadHash": item["submissionHash"],
        "approvedAt": "2026-08-10T09:10:00+00:00",
        "approvedBy": "300000000000000003",
    }
    return item


class RecommendationApprovalContractTests(unittest.TestCase):
    def test_automatic_editorial_candidate_does_not_require_discord_proof(self):
        item = {"id": "ai_book_1", "status": "queue"}

        self.assertFalse(requires_discord_approval(item))
        self.assertTrue(is_post_workflow_eligible(item))

    def test_community_item_without_proof_is_not_publishable(self):
        item = {"id": "web_book_1", "origin": "website", "status": "queue"}

        self.assertTrue(requires_discord_approval(item))
        self.assertFalse(has_verified_discord_approval(item))
        self.assertFalse(is_post_workflow_eligible(item))

    def test_matching_snapshot_delivery_and_approval_are_publishable(self):
        item = approved_community_item()

        self.assertTrue(has_valid_discord_delivery(item))
        self.assertTrue(has_verified_discord_approval(item))
        self.assertTrue(is_post_workflow_eligible(item))

    def test_editing_submission_after_delivery_invalidates_proof(self):
        item = approved_community_item()
        item["communitySubmission"]["title"] = "Conteúdo trocado"

        self.assertFalse(has_valid_discord_delivery(item))
        self.assertFalse(is_post_workflow_eligible(item))

    def test_mismatched_payload_hash_is_not_publishable(self):
        item = approved_community_item()
        item["discordApproval"]["payloadHash"] = "0" * 64

        self.assertFalse(has_verified_discord_approval(item))

    def test_configured_channel_and_reviewer_are_enforced(self):
        item = approved_community_item()
        with mock.patch.dict(
            os.environ,
            {
                "DISCORD_REVIEW_CHANNEL_ID": "999999999999999999",
                "DISCORD_APPROVER_USER_IDS": "888888888888888888",
            },
            clear=False,
        ):
            self.assertFalse(has_valid_discord_delivery(item))
            self.assertFalse(has_verified_discord_approval(item))

    def test_enrichment_may_change_display_fields_but_not_snapshot(self):
        item = approved_community_item()
        enriched = copy.deepcopy(item)
        enriched["title"] = "Título canónico enriquecido"
        enriched["link"] = "https://canonical.example/book"

        self.assertEqual(
            community_submission_hash(enriched),
            item["submissionHash"],
        )
        self.assertTrue(has_verified_discord_approval(enriched))


if __name__ == "__main__":
    unittest.main()
