"""Dispatch or guard a bounded recovery when no review card was delivered."""

import argparse
import datetime
import json
import os
import sys

import requests


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DRAFT_PATH = os.path.join(SCRIPT_DIR, "review_draft.json")
NOTIFICATION_PATH = os.path.join(SCRIPT_DIR, "review_notification.json")
PUBLICATION_PATH = os.path.join(SCRIPT_DIR, "instagram_publication.json")
RECOVERY_START_HOUR = 18
RECOVERY_START_MINUTE = 45
LATEST_GENERATION_START_HOUR = 19
LATEST_GENERATION_START_MINUTE = 50


def _load_optional(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _generation_needed(now):
    """Return (needed, reason), rechecking state when a queued job starts."""
    if now.weekday() != 5:
        return False, "Outside the Saturday recovery window."
    cycle_start = now.replace(
        hour=RECOVERY_START_HOUR,
        minute=RECOVERY_START_MINUTE,
        second=0,
        microsecond=0,
    )
    latest_start = now.replace(
        hour=LATEST_GENERATION_START_HOUR,
        minute=LATEST_GENERATION_START_MINUTE,
        second=0,
        microsecond=0,
    )
    if not (cycle_start <= now <= latest_start):
        return (
            False,
            "Outside the bounded 18:45-19:50 UTC generation-start window.",
        )

    publication = _load_optional(PUBLICATION_PATH)
    if (_timestamp(publication.get("published_at")) or datetime.datetime.min.replace(
        tzinfo=datetime.timezone.utc
    )) >= cycle_start:
        return False, "This weekly cycle is already published."

    draft = _load_optional(DRAFT_PATH)
    draft_created = _timestamp(draft.get("created_at"))
    current_draft = bool(draft_created and draft_created >= cycle_start)
    production_draft = current_draft and not bool(draft.get("is_test"))
    notification = _load_optional(NOTIFICATION_PATH)
    delivered = bool(
        production_draft
        and notification.get("draft_id") == draft.get("draft_id")
        and notification.get("content_hash") == draft.get("content_hash")
        and notification.get("review_message_id")
        and notification.get("caption_message_id")
    )
    if delivered:
        return False, "The current review card is already on Discord."
    if production_draft and (draft.get("approval") or {}).get("approved"):
        return (
            False,
            "The current draft is approved; generation will not overwrite it.",
        )
    return True, "No delivered or approved proposal exists for this cycle."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--needs-generation",
        action="store_true",
        help="Guard a queued recovery without dispatching another workflow.",
    )
    args = parser.parse_args()
    now = datetime.datetime.now(datetime.timezone.utc)
    needed, reason = _generation_needed(now)
    print(reason)
    if args.needs_generation:
        # The workflow handles this status inside a shell `if`, so a skipped
        # recovery remains a successful, non-alerting job.
        if needed:
            sys.exit(0)
        if reason.startswith("Outside"):
            sys.exit(4)
        sys.exit(3)
    if not needed:
        return

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise RuntimeError("GITHUB_TOKEN/GITHUB_REPOSITORY are required.")
    response = requests.post(
        (
            f"https://api.github.com/repos/{repository}/actions/workflows/"
            "instagram_generate.yml/dispatches"
        ),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": "main", "inputs": {"recovery_mode": "true"}},
        timeout=20,
    )
    if response.status_code != 204:
        raise RuntimeError(
            f"Recovery dispatch failed ({response.status_code}): {response.text}"
        )
    print("One bounded weekly generation recovery was dispatched.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, requests.RequestException) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
