"""Notify Discord when an approved recommendation later fails verification."""

import datetime
import json
import os
import re
import sys

import requests


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
REC_FILE = os.path.join(
    ROOT_DIR, "website", "public", "recommendations.json"
)
DISCORD_API = "https://discord.com/api/v10"


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _failure_code(value):
    match = re.match(r"^\s*([A-Z][A-Z0-9_]+)", str(value or ""))
    return match.group(1) if match else "VERIFICATION_FAILED"


def public_reason(value):
    code = _failure_code(value)
    if code in {
        "HTTP_ERROR",
        "NETWORK_ERROR",
        "LINK_UNAVAILABLE",
        "BAD_REDIRECT",
        "TOO_MANY_REDIRECTS",
    }:
        return "A fonte original deixou de estar disponível."
    if code in {
        "CONTENT_EXPIRED",
        "CONTENT_TOO_CLOSE_TO_EXPIRY",
        "EXPIRY_UNVERIFIED",
    }:
        return "O conteúdo deixou de estar suficientemente atual."
    if code in {
        "COVER_NOT_FOUND",
        "BAD_IMAGE_SIZE",
        "BAD_ASPECT_RATIO",
        "PLACEHOLDER_IMAGE",
    }:
        return "Já não foi possível confirmar uma imagem adequada."
    if any(
        marker in code
        for marker in ("MISMATCH", "NOT_FOUND", "UNVERIFIED")
    ):
        return "A fonte já não confirma com segurança esta recomendação."
    return "A recomendação deixou de cumprir os critérios de verificação."


def _eligible(item):
    return bool(
        item.get("approvedAt")
        and item.get("approvedBy")
        and item.get("status") == "invalid"
        and item.get("resolutionStatus") == "rejected"
        and item.get("validationError")
    )


def _footer(item):
    return f"Recommendation ID: {item.get('id')} | workflow-rejected"


def _existing_alerts(session, channel_id, headers):
    response = session.get(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=headers,
        params={"limit": 100},
        timeout=20,
    )
    response.raise_for_status()
    found = {}
    for message in response.json():
        for embed in message.get("embeds") or []:
            footer = str((embed.get("footer") or {}).get("text") or "")
            match = re.match(
                r"^Recommendation ID: (.+?) \| workflow-rejected$",
                footer,
            )
            if match:
                found[match.group(1)] = str(message.get("id") or "")
    return found


def _payload(item):
    category = str(item.get("category") or item.get("type") or "Conteúdo")
    title = str(item.get("title") or "Recomendação sem título")
    approver = str(item.get("approvedBy") or "")
    reason = public_reason(item.get("validationError"))
    return {
        "content": f"<@{approver}>" if approver else "",
        "allowed_mentions": {
            "users": [approver] if approver else [],
            "parse": [],
        },
        "embeds": [
            {
                "title": "⚠️ Recomendação aprovada retirada da fila",
                "description": (
                    f"**{title}** ({category}) tinha sido aprovada, mas uma "
                    "nova verificação não a conseguiu confirmar. Não será "
                    "utilizada num post."
                ),
                "color": 0xE67E22,
                "fields": [
                    {
                        "name": "Motivo",
                        "value": reason,
                        "inline": False,
                    }
                ],
                "footer": {"text": _footer(item)},
            }
        ],
    }


def notify_rejected_approvals(session=None):
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_REVIEW_CHANNEL_ID", "").strip()
    if not token or not channel_id:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN e DISCORD_REVIEW_CHANNEL_ID são necessários."
        )
    with open(REC_FILE, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    queue = data.get("queue", [])
    pending = [item for item in queue if _eligible(item)]
    if not pending:
        print("[OK] No rejected approved recommendation needs an alert.")
        return 0

    client = session or requests.Session()
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    try:
        existing = _existing_alerts(client, channel_id, headers)
    except requests.RequestException as exc:
        print(f"[WARNING] Could not check previous Discord alerts: {exc}")
        existing = {}

    changed = False
    notified = 0
    now = _utc_now()
    for item in pending:
        item_id = str(item.get("id") or "")
        marker = item.get("workflowRejectionNotification") or {}
        code = _failure_code(item.get("validationError"))
        if marker.get("status") == "sent" and marker.get("failureCode") == code:
            continue
        message_id = existing.get(item_id)
        if not message_id:
            response = client.post(
                f"{DISCORD_API}/channels/{channel_id}/messages",
                headers=headers,
                json=_payload(item),
                timeout=20,
            )
            response.raise_for_status()
            message_id = str(response.json().get("id") or "")
            notified += 1
        item["workflowRejectionNotification"] = {
            "status": "sent",
            "failureCode": code,
            "messageId": message_id,
            "sentAt": now,
        }
        changed = True

    if changed:
        temporary = REC_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, REC_FILE)
    print(f"[OK] Sent {notified} rejected-approval alert(s).")
    return notified


if __name__ == "__main__":
    try:
        notify_rejected_approvals()
    except Exception as exc:
        print(f"[ERROR] Rejected approval alert failed: {exc}")
        sys.exit(1)
