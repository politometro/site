"""Schedule guard for the approved weekly Instagram post.

Discord approval only records consent.  This module decides whether the
approved draft belongs to the current Sunday 10:00 publication window in
Europe/Lisbon.  Keeping the decision in Python makes manual workflow runs and
delayed GitHub runners obey the same local-time rule.
"""

import argparse
import datetime
import json
import os
from zoneinfo import ZoneInfo


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DRAFT_PATH = os.path.join(SCRIPT_DIR, "review_draft.json")
RECEIPT_PATH = os.path.join(SCRIPT_DIR, "instagram_publication.json")
PUBLICATION_TIMEZONE_NAME = "Europe/Lisbon"
PUBLICATION_TIMEZONE = ZoneInfo(PUBLICATION_TIMEZONE_NAME)
SUNDAY_WEEKDAY = 6  # Sunday
SUNDAY_HOUR = 10
WEDNESDAY_WEEKDAY = 2  # Wednesday
WEDNESDAY_HOUR = 9


def _get_draft_edition(draft):
    if isinstance(draft, dict):
        return draft.get("post_type") or draft.get("edition") or "sunday_standard"
    return "sunday_standard"


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _load_optional(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def scheduled_for_draft(draft):
    """Return the draft's target publication window as an UTC ISO string."""
    approval = draft.get("approval") or {}
    reference = _parse_datetime(draft.get("created_at"))
    if reference is None:
        reference = _parse_datetime(approval.get("approved_at"))
    if reference is None:
        return None

    edition = _get_draft_edition(draft)
    target_weekday = WEDNESDAY_WEEKDAY if edition == "wednesday_nostalgia" else SUNDAY_WEEKDAY
    target_hour = WEDNESDAY_HOUR if edition == "wednesday_nostalgia" else SUNDAY_HOUR

    local_reference = reference.astimezone(PUBLICATION_TIMEZONE)
    days_until_target = (target_weekday - local_reference.weekday()) % 7
    target_date = local_reference.date() + datetime.timedelta(days=days_until_target)
    target_local = datetime.datetime.combine(
        target_date,
        datetime.time(target_hour, 0),
        tzinfo=PUBLICATION_TIMEZONE,
    )
    return target_local.astimezone(datetime.timezone.utc).isoformat()


def publication_decision(draft, receipt=None, *, now=None, force_now=False):
    """Return ``(should_publish, reason, scheduled_for)`` for one run."""
    if not isinstance(draft, dict) or not draft:
        return False, "Não existe um rascunho semanal para publicar.", None
    if draft.get("is_test"):
        return False, "O rascunho atual é apenas de teste.", None

    draft_id = str(draft.get("draft_id") or "")
    content_hash = str(draft.get("content_hash") or "")
    approval = draft.get("approval") or {}
    if not approval.get("approved"):
        return False, "O rascunho ainda não foi aprovado.", None
    if (
        not draft_id
        or not content_hash
        or approval.get("draft_id") != draft_id
        or approval.get("content_hash") != content_hash
    ):
        return False, "A aprovação não corresponde ao rascunho atual.", None

    scheduled_for = (
        _parse_datetime(approval.get("scheduled_for"))
        or _parse_datetime(scheduled_for_draft(draft))
    )
    if scheduled_for is None:
        return False, "Não foi possível determinar o agendamento deste rascunho.", None

    edition = _get_draft_edition(draft)
    expected_weekday = WEDNESDAY_WEEKDAY if edition == "wednesday_nostalgia" else SUNDAY_WEEKDAY
    expected_hour = WEDNESDAY_HOUR if edition == "wednesday_nostalgia" else SUNDAY_HOUR

    target_local = scheduled_for.astimezone(PUBLICATION_TIMEZONE)
    if (
        target_local.weekday() != expected_weekday
        or target_local.hour != expected_hour
        or target_local.minute != 0
    ):
        return False, "O horário guardado para o rascunho é inválido.", scheduled_for

    current = now or datetime.datetime.now(datetime.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=datetime.timezone.utc)
    current_local = current.astimezone(PUBLICATION_TIMEZONE)
    if force_now:
        if receipt:
            same_draft = (
                receipt.get("draft_id") == draft_id
                and receipt.get("content_hash") == content_hash
            )
            if same_draft and receipt.get("post_id"):
                return False, "Este rascunho já foi publicado.", scheduled_for
            if not same_draft and not receipt.get("post_id"):
                return (
                    False,
                    "Existe outra publicação ainda pendente.",
                    scheduled_for,
                )
        return (
            True,
            "Override manual autorizado para publicar agora.",
            scheduled_for,
        )
    if current_local.date() != target_local.date():
        return (
            False,
            "Este rascunho não pertence à janela de publicação de hoje.",
            scheduled_for,
        )
    if (
        current_local.weekday() != expected_weekday
        or current_local.hour != expected_hour
    ):
        day_label = "quarta-feira" if expected_weekday == 2 else "domingo"
        return (
            False,
            f"A publicação só pode começar na {day_label} entre as {expected_hour:02d}:00 e as {expected_hour:02d}:59.",
        )

    receipt = receipt if isinstance(receipt, dict) else {}
    if receipt:
        same_draft = (
            receipt.get("draft_id") == draft_id
            and receipt.get("content_hash") == content_hash
        )
        if same_draft and receipt.get("post_id"):
            return False, "Este rascunho já foi publicado.", scheduled_for
        if not same_draft and not receipt.get("post_id"):
            return (
                False,
                "Existe outra publicação ainda pendente.",
                scheduled_for,
            )

    return True, "Rascunho aprovado e dentro da janela das 10:00.", scheduled_for


def current_publication_decision(*, now=None, force_now=False):
    return publication_decision(
        _load_optional(DRAFT_PATH),
        _load_optional(RECEIPT_PATH),
        now=now,
        force_now=force_now,
    )


def _write_github_output(should_publish, reason, scheduled_for):
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT não está configurado.")
    safe_reason = " ".join(str(reason).splitlines())
    scheduled_text = scheduled_for.isoformat() if scheduled_for else ""
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"should_publish={str(should_publish).lower()}\n")
        handle.write(f"reason={safe_reason}\n")
        handle.write(f"scheduled_for={scheduled_text}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="Write a non-failing should_publish decision for GitHub Actions.",
    )
    args = parser.parse_args()

    force_now = os.environ.get("FORCE_PUBLISH_NOW", "").strip().lower() == "true"
    should_publish, reason, scheduled_for = current_publication_decision(
        force_now=force_now
    )
    target = scheduled_for.isoformat() if scheduled_for else "indisponível"
    print(f"{reason} Horário previsto: {target}.")
    if args.github_output:
        _write_github_output(should_publish, reason, scheduled_for)


if __name__ == "__main__":
    main()
