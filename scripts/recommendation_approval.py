"""Approval invariants for community recommendation records.

Community suggestions are stored before review so delivery can be retried.
They become eligible for post tooling only when an immutable submission
snapshot, the Discord delivery receipt, and the reviewer approval all agree.
Automatically discovered editorial candidates are outside this contract.
"""

from collections.abc import Mapping
import datetime
import hashlib
import hmac
import json
import os


COMMUNITY_SOURCE_KIND = "community_suggestion"
COMMUNITY_ORIGINS = {
    "community",
    "discord",
    "discord-command",
    "website",
}


def _text(value):
    return str(value or "").strip()


def _parse_timestamp(value):
    try:
        parsed = datetime.datetime.fromisoformat(
            _text(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _configured_ids(name):
    return {
        value.strip()
        for value in os.environ.get(name, "").split(",")
        if value.strip().isdigit()
    }


def requires_discord_approval(item):
    """Return whether *item* came from a public/community suggestion path."""
    if not isinstance(item, Mapping):
        return False
    approval = item.get("discordApproval")
    if isinstance(approval, Mapping) and approval.get("required") is True:
        return True
    origin = _text(item.get("origin")).casefold()
    item_id = _text(item.get("id")).casefold()
    return bool(
        item.get("sourceKind") == COMMUNITY_SOURCE_KIND
        or isinstance(item.get("communitySubmission"), Mapping)
        or origin in COMMUNITY_ORIGINS
        or item_id.startswith(("web_", "discord_"))
    )


def community_submission_hash(item):
    """Return the canonical SHA-256 for an immutable community snapshot."""
    if not isinstance(item, Mapping):
        return ""
    snapshot = item.get("communitySubmission")
    if not isinstance(snapshot, Mapping):
        return ""

    item_id = _text(snapshot.get("id"))
    source_kind = _text(snapshot.get("sourceKind"))
    media_type = _text(snapshot.get("type"))
    title = _text(snapshot.get("title"))
    link = _text(snapshot.get("link"))
    created_at = _text(snapshot.get("createdAt"))
    if (
        not item_id
        or source_kind != COMMUNITY_SOURCE_KIND
        or not media_type
        or not title
        or not _parse_timestamp(created_at)
        or item_id != _text(item.get("id"))
        or source_kind != _text(item.get("sourceKind"))
        or media_type != _text(item.get("type"))
        or created_at != _text(item.get("createdAt"))
    ):
        return ""

    encoded = json.dumps(
        [
            "approval-v1",
            item_id,
            source_kind,
            media_type,
            title,
            link,
            created_at,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def has_valid_submission_contract(item):
    """Validate the immutable snapshot and its stored digest."""
    if not requires_discord_approval(item):
        return True
    expected = community_submission_hash(item)
    stored = _text(item.get("submissionHash"))
    return bool(
        expected
        and len(stored) == 64
        and hmac.compare_digest(expected, stored)
    )


def has_valid_discord_delivery(item):
    """Validate that Discord received the exact immutable submission."""
    if not requires_discord_approval(item):
        return True
    if not has_valid_submission_contract(item):
        return False
    approval = item.get("discordApproval")
    if not isinstance(approval, Mapping):
        return False

    message_id = _text(approval.get("messageId"))
    channel_id = _text(approval.get("channelId"))
    payload_hash = _text(approval.get("payloadHash"))
    canonical_channel = _text(os.environ.get("DISCORD_REVIEW_CHANNEL_ID"))
    return bool(
        approval.get("required") is True
        and message_id.isdigit()
        and channel_id.isdigit()
        and message_id == _text(item.get("discordMessageId"))
        and item.get("notificationStatus") == "sent"
        and _parse_timestamp(approval.get("sentAt"))
        and hmac.compare_digest(payload_hash, _text(item.get("submissionHash")))
        and (not canonical_channel or channel_id == canonical_channel)
    )


def has_verified_discord_approval(item):
    """Validate delivery and approval proof for a community item."""
    if not requires_discord_approval(item):
        return True
    if not has_valid_discord_delivery(item):
        return False

    approval = item.get("discordApproval")
    approved_at = _parse_timestamp(approval.get("approvedAt"))
    sent_at = _parse_timestamp(approval.get("sentAt"))
    approved_by = _text(approval.get("approvedBy"))
    configured_reviewers = _configured_ids("DISCORD_APPROVER_USER_IDS")
    configured_roles = _configured_ids("DISCORD_APPROVER_ROLE_IDS")
    approved_role = _text(approval.get("approvedByRole"))
    authorization = _text(approval.get("reviewerAuthorization"))
    configured_authorization_valid = bool(
        approved_by in configured_reviewers
        or approved_role in configured_roles
        or authorization == "server_manager"
    )
    return bool(
        approval.get("status") == "approved"
        and approved_at
        and sent_at
        and approved_at >= sent_at
        and approved_by.isdigit()
        and (
            not configured_reviewers and not configured_roles
            or configured_authorization_valid
        )
    )


def is_post_workflow_eligible(item):
    """Return whether an item may be selected or transformed by post tooling."""
    return bool(
        isinstance(item, Mapping)
        and item.get("status") == "queue"
        and has_verified_discord_approval(item)
    )


def pending_discord_state(item):
    """Return a safe non-publishable state for a community item without proof."""
    if not requires_discord_approval(item) or has_verified_discord_approval(item):
        return None
    if has_valid_discord_delivery(item):
        return {
            "status": "pending_sent",
            "notificationStatus": "sent",
        }
    return {
        "status": "pending_approval",
        "notificationStatus": "pending_retry",
    }
