"""Retry the durable Discord outbox for verified website suggestions."""

import datetime
import json
import os
import sys
import time
from urllib.parse import urljoin

import requests


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
REC_FILE = os.path.join(
    ROOT_DIR, "website", "public", "recommendations.json"
)
MAX_NOTIFICATION_ATTEMPTS = 6
DEDUPLICATION_PAGES = 5
MIN_TEMPORAL_VALIDITY_HOURS = 24
DELIVERY_REACTIVATION_DAYS = 7
MAX_ITEMS_PER_RUN = 20
TIME_BUDGET_SECONDS = 180


def _parse_datetime(value):
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


def _atomic_write(value):
    temporary = REC_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, REC_FILE)


def _existing_messages(session, endpoint, headers):
    found = {}
    before = None
    for _ in range(DEDUPLICATION_PAGES):
        params = {"limit": 100}
        if before:
            params["before"] = before
        response = session.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        messages = response.json()
        if not isinstance(messages, list):
            raise RuntimeError("Discord returned an invalid message list.")
        for message in messages:
            embeds = message.get("embeds") or []
            for embed in embeds:
                footer = (embed.get("footer") or {}).get("text", "")
                if footer.startswith("ID: "):
                    item_id = footer[4:].split("|", 1)[0].strip()
                    if item_id and item_id not in found:
                        found[item_id] = str(message.get("id") or "")
        if len(messages) < 100:
            break
        before = str(messages[-1].get("id") or "")
        if not before:
            break
    return found


def _strictly_verified(item, now):
    verification = item.get("verification") or {}
    media_type = item.get("type")
    if (
        item.get("resolutionStatus") != "verified"
        or verification.get("status") != "verified"
        or not str(item.get("title") or "").strip()
        or not str(item.get("description") or "").strip()
    ):
        return False
    if media_type == "project":
        return True
    if (
        not verification.get("entityId")
        or not verification.get("coverHash")
        or not str(item.get("link") or "").startswith(("http://", "https://"))
        or not str(item.get("imageUrl") or "").startswith("/covers/")
    ):
        return False
    if media_type in {"podcast", "highlight"}:
        published = _parse_datetime(item.get("sourcePublishedAt"))
        expiry = _parse_datetime(item.get("expiryDate"))
        return bool(
            published
            and expiry
            and expiry > published
            and expiry
            > now + datetime.timedelta(hours=MIN_TEMPORAL_VALIDITY_HOURS)
        )
    return True


def _retry_delay(response, attempts):
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError):
            payload = {}
        try:
            seconds = float(retry_after or payload.get("retry_after") or 60)
        except (TypeError, ValueError):
            seconds = 60
        return datetime.timedelta(seconds=max(5, min(seconds, 86_400)))
    return datetime.timedelta(hours=min(24, 2**attempts))


def _dead_letter(item, now, error, *, kind="delivery"):
    item["status"] = "notification_failed"
    item["notificationStatus"] = "dead_letter"
    item["notificationFailureKind"] = kind
    item["notificationFailedAt"] = now.isoformat()
    item["lastNotificationError"] = str(error)[:500]
    item.pop("nextNotificationAttemptAt", None)


def _message_payload(item):
    media_type = item.get("type", "project")
    emojis = {
        "book": "📚",
        "podcast": "🎙️",
        "movie": "🎬",
        "highlight": "📰",
        "project": "💡",
    }
    colours = {
        "book": 0x2E86AB,
        "podcast": 0x8338EC,
        "movie": 0xE63946,
        "highlight": 0xF77F00,
        "project": 0x0099FF,
    }
    verification = item.get("verification") or {}
    verification_id = str(
        verification.get("externalId")
        or verification.get("entityId")
        or item.get("externalId")
        or ""
    )
    whole_podcast = (
        media_type == "podcast"
        and verification_id.startswith("apple:podcast:")
    )
    fields = [
        {
            "name": "Tipo",
            "value": str(item.get("category") or media_type)[:1024],
            "inline": True,
        },
        {
            "name": "Autor / Fonte",
            "value": str(item.get("authorOrMeta") or "—")[:1024],
            "inline": True,
        },
        {
            "name": "Verificação",
            "value": str(
                verification.get("source")
                or verification.get("provider")
                or "fonte canónica"
            )[:1024],
            "inline": True,
        },
    ]
    if item.get("link"):
        fields.append(
            {
                "name": "Link canónico",
                "value": str(item["link"])[:1024],
                "inline": False,
            }
        )
    if whole_podcast:
        fields.append(
            {
                "name": "Opções ao aprovar",
                "value": (
                    "Recomendar uma vez · acompanhar episódios recentes "
                    "· ou fazer ambos"
                ),
                "inline": False,
            }
        )
    embed = {
        "title": (
            f"{emojis.get(media_type, '💡')} Sugestão: "
            f"{str(item.get('title') or '')}"
        )[:256],
        "description": str(item.get("description") or "—")[:4096],
        "color": colours.get(media_type, 0x0099FF),
        "fields": fields,
        "footer": {
            "text": (
                f"ID: {item.get('id', 'sem-id')} | "
                "Fonte validada no servidor"
            )
        },
        "timestamp": item.get("createdAt")
        or datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    source_image = str(item.get("sourceImageUrl") or "")
    image_url = source_image if source_image.startswith("https://") else ""
    if not image_url and str(item.get("imageUrl") or "").startswith("/"):
        website_url = os.environ.get("WEBSITE_URL", "").strip()
        if website_url:
            image_url = urljoin(website_url.rstrip("/") + "/", item["imageUrl"])
    if image_url and media_type != "project":
        embed["thumbnail"] = {"url": image_url}
    return {
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 3,
                        "label": (
                            "Escolher aprovação"
                            if whole_podcast
                            else "Aprovar"
                        ),
                        "emoji": {"name": "✅"},
                        "custom_id": "rec_approve",
                    },
                    {
                        "type": 2,
                        "style": 4,
                        "label": "Rejeitar",
                        "emoji": {"name": "❌"},
                        "custom_id": "rec_reject",
                    },
                ],
            }
        ],
    }


def main():
    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_REVIEW_CHANNEL_ID", "").strip()
    if not token or not channel_id:
        raise RuntimeError("Discord outbox credentials are missing.")
    if not os.path.exists(REC_FILE):
        return
    with open(REC_FILE, "r", encoding="utf-8") as handle:
        database = json.load(handle)
    if not isinstance(database, dict):
        raise RuntimeError("recommendations.json não contém um objeto.")

    now = datetime.datetime.now(datetime.timezone.utc)
    changed = False
    for item in database.get("queue", []):
        failed_at = _parse_datetime(item.get("notificationFailedAt"))
        if (
            item.get("status") == "notification_failed"
            and item.get("notificationFailureKind") == "delivery"
            and failed_at
            and failed_at
            <= now - datetime.timedelta(days=DELIVERY_REACTIVATION_DAYS)
        ):
            item["status"] = "pending_approval"
            item["notificationStatus"] = "pending_retry"
            item["notificationAttempts"] = 0
            item["nextNotificationAttemptAt"] = now.isoformat()
            item.pop("notificationFailedAt", None)
            item.pop("notificationFailureKind", None)
            changed = True
    if changed:
        # A repaired Discord configuration can recover old suggestions
        # automatically after a bounded cool-down.
        _atomic_write(database)

    session = requests.Session()
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    endpoint = (
        f"https://discord.com/api/v10/channels/{channel_id}/messages"
    )
    existing = _existing_messages(session, endpoint, headers)
    dead_letters = []
    processed = 0

    for item in database.get("queue", []):
        if processed >= MAX_ITEMS_PER_RUN or time.monotonic() >= deadline:
            print("[INFO] Discord outbox bounded batch/time limit reached.")
            break
        if item.get("status") != "pending_approval":
            continue
        due = _parse_datetime(item.get("nextNotificationAttemptAt"))
        if due and due > now:
            continue
        processed += 1
        expiry = _parse_datetime(item.get("expiryDate"))
        if expiry and expiry <= now:
            item["status"] = "expired"
            item["notificationStatus"] = "expired"
            changed = True
            _atomic_write(database)
            continue
        if not _strictly_verified(item, now):
            _dead_letter(
                item,
                now,
                "Stored suggestion no longer satisfies the strict verification contract.",
                kind="validation",
            )
            dead_letters.append(str(item.get("id") or "sem-id"))
            changed = True
            _atomic_write(database)
            continue
        previous_attempts = int(item.get("notificationAttempts") or 0)
        if previous_attempts >= MAX_NOTIFICATION_ATTEMPTS:
            _dead_letter(
                item,
                now,
                f"Discord delivery exceeded {MAX_NOTIFICATION_ATTEMPTS} attempts.",
            )
            dead_letters.append(str(item.get("id") or "sem-id"))
            changed = True
            _atomic_write(database)
            continue

        message_id = existing.get(str(item.get("id") or ""))
        if not message_id:
            response = session.post(
                endpoint,
                headers=headers,
                json=_message_payload(item),
                timeout=20,
            )
            if response.ok:
                message_id = str(response.json().get("id") or "")
            else:
                attempts = previous_attempts + 1
                item["notificationAttempts"] = attempts
                error = (
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
                if attempts >= MAX_NOTIFICATION_ATTEMPTS:
                    _dead_letter(item, now, error)
                    dead_letters.append(str(item.get("id") or "sem-id"))
                else:
                    item["notificationStatus"] = "pending_retry"
                    item["lastNotificationError"] = error
                    item["nextNotificationAttemptAt"] = (
                        now + _retry_delay(response, attempts)
                    ).isoformat()
                changed = True
                _atomic_write(database)
                continue

        if message_id:
            item["status"] = "pending_sent"
            item["notificationStatus"] = "sent"
            item["notificationAttempts"] = (
                int(item.get("notificationAttempts") or 0) + 1
            )
            item["discordMessageId"] = message_id
            item["discordNotifiedAt"] = now.isoformat()
            item.pop("nextNotificationAttemptAt", None)
            item.pop("lastNotificationError", None)
            changed = True
            # Checkpoint each delivery before contacting the next item. If a
            # later request times out, earlier receipts cannot be lost.
            _atomic_write(database)

    if changed:
        print("[OK] Discord outbox state updated.")
    else:
        print("[OK] No Discord outbox item was due.")
    if dead_letters:
        alert = session.post(
            endpoint,
            headers=headers,
            json={
                "content": (
                    "⚠️ A entrega de sugestões ficou bloqueada após validação "
                    "ou tentativas limitadas. IDs: "
                    + ", ".join(dead_letters[:20])
                    + ". Consulta o workflow de manutenção."
                )
            },
            timeout=20,
        )
        if not alert.ok:
            raise RuntimeError(
                "Discord dead-letter alert failed "
                f"(HTTP {alert.status_code})."
            )


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        requests.RequestException,
    ) as exc:
        print(f"[ERROR] Discord outbox retry failed: {exc}")
        sys.exit(1)
