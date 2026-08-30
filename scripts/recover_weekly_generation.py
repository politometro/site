"""Dispatch or guard a bounded recovery when no review card was delivered."""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request


try:
    from scripts import publication_schedule
except ImportError:
    import publication_schedule


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
        return False, "Outside the bounded recovery window (Saturday)."
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
            "Outside the bounded generation-start window.",
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


def _publication_needed(now):
    """Return (needed, reason), checking if an approved draft is awaiting publication."""
    draft = _load_optional(DRAFT_PATH)
    receipt = _load_optional(PUBLICATION_PATH)
    should_publish, reason, _ = publication_schedule.publication_decision(
        draft, receipt, now=now
    )
    return should_publish, reason


def _dispatch_workflow(token, repository, workflow_file, payload):
    request = urllib.request.Request(
        url=(
            f"https://api.github.com/repos/{repository}/actions/workflows/"
            f"{workflow_file}/dispatches"
        ),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "politometro-weekly-recovery",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Recovery dispatch failed ({exc.code}): {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Recovery dispatch connection failed: {exc.reason}"
        ) from exc
    if status != 204:
        raise RuntimeError(f"Recovery dispatch failed ({status}): {body}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--needs-generation",
        action="store_true",
        help="Guard a queued recovery without dispatching another workflow.",
    )
    args = parser.parse_args()
    now = datetime.datetime.now(datetime.timezone.utc)
    generation_needed, gen_reason = _generation_needed(now)
    print(f"Generation check: {gen_reason}")
    if args.needs_generation:
        # The workflow handles this status inside a shell `if`, so a skipped
        # recovery remains a successful, non-alerting job.
        if generation_needed:
            sys.exit(0)
        if gen_reason.startswith("Outside"):
            sys.exit(4)
        sys.exit(3)

    publication_needed, pub_reason = _publication_needed(now)
    print(f"Publication check: {pub_reason}")

    if not generation_needed and not publication_needed:
        return

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise RuntimeError("GITHUB_TOKEN/GITHUB_REPOSITORY are required.")

    if generation_needed:
        _dispatch_workflow(
            token,
            repository,
            "instagram_generate.yml",
            {
                "ref": "main",
                "inputs": {
                    "recovery_mode": "true",
                    "post_type": "sunday_standard",
                },
            },
        )
        print("One bounded weekly generation recovery was dispatched.")

    if publication_needed:
        _dispatch_workflow(
            token,
            repository,
            "instagram_publish.yml",
            {"ref": "main"},
        )
        print("One bounded weekly publication recovery was dispatched.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
