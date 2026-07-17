"""Publish an approved Instagram draft as a durable, idempotent transaction.

The workflow deliberately invokes the three phases separately:

1. ``--prepare`` creates one Meta container and persists its ``creation_id``.
2. ``--mark-publishing`` persists the intent to call ``media_publish``.
3. ``--publish`` reconciles recent media, publishes that same container when
   needed, and persists the confirmed ``post_id``.

Keeping the first two receipts in git before the next phase means a runner
crash or an ambiguous HTTP timeout can never cause a fresh container to be
created for the same draft.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time

import requests


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DRAFT_PATH = os.path.join(SCRIPT_DIR, "review_draft.json")
RECEIPT_PATH = os.path.join(SCRIPT_DIR, "instagram_publication.json")
CAPTION_PATH = os.path.join(
    ROOT_DIR, "website", "public", "current_caption.txt"
)

CONTAINER_READY_TIMEOUT_SECONDS = 60
CONTAINER_POLL_SECONDS = 5
RECONCILE_ATTEMPTS = 4
RECONCILE_DELAY_SECONDS = 3
RECONCILE_CLOCK_SKEW_SECONDS = 300


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso_now():
    return _utc_now().isoformat()


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json_atomic(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def _graph_version():
    version = os.environ.get("META_GRAPH_API_VERSION", "v25.0").strip()
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise RuntimeError("META_GRAPH_API_VERSION tem um formato inválido.")
    return version


def _approved_draft():
    if not os.path.exists(DRAFT_PATH):
        raise RuntimeError("O rascunho aprovado não existe.")
    draft = _load_json(DRAFT_PATH)
    approval = draft.get("approval") or {}
    if draft.get("is_test") or not approval.get("approved"):
        raise RuntimeError("O rascunho não está aprovado para publicação.")
    if (
        not draft.get("draft_id")
        or not draft.get("content_hash")
        or approval.get("draft_id") != draft.get("draft_id")
        or approval.get("content_hash") != draft.get("content_hash")
    ):
        raise RuntimeError("A aprovação não corresponde ao conteúdo do rascunho.")
    return draft


def _caption_and_hash():
    if not os.path.exists(CAPTION_PATH):
        raise RuntimeError("A legenda revista está em falta.")
    with open(CAPTION_PATH, "r", encoding="utf-8") as handle:
        caption = handle.read()
    if not caption.strip():
        raise RuntimeError("A legenda revista está vazia.")
    digest = hashlib.sha256(caption.encode("utf-8")).hexdigest()
    return caption, digest


def _environment():
    instagram_id = os.environ.get(
        "INSTAGRAM_BUSINESS_ACCOUNT_ID", ""
    ).strip()
    access_token = os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    commit_sha = os.environ.get("GITHUB_SHA", "main").strip()
    if not instagram_id or not access_token:
        raise RuntimeError(
            "INSTAGRAM_BUSINESS_ACCOUNT_ID/FACEBOOK_ACCESS_TOKEN não configurados."
        )
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY não está configurado.")
    return {
        "instagram_id": instagram_id,
        "access_token": access_token,
        "repository": repository,
        "commit_sha": commit_sha,
        "graph_api_version": _graph_version(),
        "base_url": f"https://graph.facebook.com/{_graph_version()}",
    }


def _context():
    draft = _approved_draft()
    caption, caption_sha256 = _caption_and_hash()
    expected_caption_hash = draft.get("caption_sha256")
    if expected_caption_hash and expected_caption_hash != caption_sha256:
        raise RuntimeError("A legenda já não corresponde ao rascunho aprovado.")
    environment = _environment()
    image_url = (
        f"https://raw.githubusercontent.com/{environment['repository']}/"
        f"{environment['commit_sha']}/website/public/current_post.jpg"
    )
    return {
        **environment,
        "draft": draft,
        "caption": caption,
        "caption_sha256": caption_sha256,
        "image_url": image_url,
    }


def _load_receipt():
    if not os.path.exists(RECEIPT_PATH):
        return None
    receipt = _load_json(RECEIPT_PATH)
    if not isinstance(receipt, dict):
        raise RuntimeError("O recibo do Instagram está corrompido.")
    return receipt


def _same_draft(receipt, draft):
    return (
        receipt.get("draft_id") == draft.get("draft_id")
        and receipt.get("content_hash") == draft.get("content_hash")
    )


def _confirmed(receipt):
    return bool(receipt and receipt.get("post_id"))


def _receipt_for_current_draft(context, *, require=True):
    receipt = _load_receipt()
    if receipt is None:
        if require:
            raise RuntimeError(
                "O contentor ainda não foi preparado e persistido."
            )
        return None
    if _same_draft(receipt, context["draft"]):
        stored_hash = receipt.get("caption_sha256")
        if stored_hash and stored_hash != context["caption_sha256"]:
            raise RuntimeError(
                "O recibo pendente pertence a outra versão da legenda."
            )
        return receipt
    if not _confirmed(receipt):
        raise RuntimeError(
            "Existe uma publicação pendente de outro rascunho; "
            "não será criado um segundo contentor."
        )
    if require:
        raise RuntimeError("O recibo persistido pertence a outro rascunho.")
    return None


def _response_json(response):
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("A Meta devolveu uma resposta que não é JSON.") from exc
    return payload if isinstance(payload, dict) else {}


def prepare_publication(session=None):
    """Create and persist exactly one container for the approved draft."""
    context = _context()
    receipt = _receipt_for_current_draft(context, require=False)
    if receipt is not None:
        if _confirmed(receipt):
            print(
                "[OK] Este rascunho já foi publicado; "
                f"recibo {receipt['post_id']} reutilizado."
            )
            return receipt
        if not receipt.get("creation_id"):
            raise RuntimeError(
                "Existe um recibo pendente sem creation_id; "
                "a criação automática foi bloqueada."
            )
        print(
            "[OK] Contentor pendente reutilizado sem criar outro: "
            f"{receipt['creation_id']}."
        )
        return receipt

    client = session or requests.Session()
    create_response = client.post(
        f"{context['base_url']}/{context['instagram_id']}/media",
        data={
            "image_url": context["image_url"],
            "caption": context["caption"],
            "access_token": context["access_token"],
        },
        timeout=25,
    )
    create_payload = _response_json(create_response)
    if not create_response.ok or not create_payload.get("id"):
        raise RuntimeError(
            f"Falha ao criar o contentor do Instagram: {create_payload}"
        )

    receipt = {
        "schema_version": 2,
        "state": "prepared",
        "draft_id": context["draft"]["draft_id"],
        "content_hash": context["draft"]["content_hash"],
        "creation_id": str(create_payload["id"]),
        "caption_sha256": context["caption_sha256"],
        # The exact caption is required for safe reconciliation after an
        # ambiguous media_publish timeout.
        "caption": context["caption"],
        "prepared_at": _iso_now(),
        "graph_api_version": context["graph_api_version"],
        "image_url": context["image_url"],
    }
    _write_json_atomic(RECEIPT_PATH, receipt)
    print(
        f"[OK] Contentor {receipt['creation_id']} preparado; "
        "o recibo deve ser persistido antes da publicação."
    )
    return receipt


def mark_publishing():
    """Persist the publish intent before the first media_publish request."""
    context = _context()
    receipt = _receipt_for_current_draft(context)
    if _confirmed(receipt):
        print(f"[OK] Publicação já confirmada: {receipt['post_id']}.")
        return receipt
    if not receipt.get("creation_id"):
        raise RuntimeError("O recibo pendente não contém creation_id.")
    state = receipt.get("state") or "prepared"
    if state == "publishing":
        print(
            "[OK] Marcador de publicação já persistido; "
            f"contentor {receipt['creation_id']} será reutilizado."
        )
        return receipt
    if state != "prepared":
        raise RuntimeError(f"Estado de publicação inesperado: {state}.")

    receipt["state"] = "publishing"
    receipt["publishing_started_at"] = _iso_now()
    receipt["publish_attempts"] = int(receipt.get("publish_attempts") or 0) + 1
    _write_json_atomic(RECEIPT_PATH, receipt)
    print(
        f"[OK] Intenção de publicar {receipt['creation_id']} persistida."
    )
    return receipt


def _wait_until_container_ready(
    session, base_url, creation_id, token
):
    """Poll for a bounded time and return the final container state."""
    deadline = time.monotonic() + CONTAINER_READY_TIMEOUT_SECONDS
    last_status = "IN_PROGRESS"
    while time.monotonic() < deadline:
        response = session.get(
            f"{base_url}/{creation_id}",
            params={
                "fields": "status_code,status",
                "access_token": token,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = _response_json(response)
        last_status = str(payload.get("status_code") or "").upper()
        if last_status == "FINISHED":
            return last_status
        if last_status in {"PUBLISHED"}:
            return last_status
        if last_status in {"ERROR", "EXPIRED"}:
            raise RuntimeError(
                f"A Meta rejeitou o contentor: {payload.get('status') or payload}"
            )
        time.sleep(CONTAINER_POLL_SECONDS)
    raise RuntimeError(
        "O contentor não ficou pronto no limite configurado "
        f"(estado: {last_status})."
    )


def _reconcile_not_before(receipt):
    marker = (
        receipt.get("publishing_started_at")
        or receipt.get("prepared_at")
    )
    parsed = _parse_datetime(marker)
    if parsed is None:
        raise RuntimeError(
            "O recibo não contém um timestamp válido para reconciliação."
        )
    return parsed - datetime.timedelta(
        seconds=RECONCILE_CLOCK_SKEW_SECONDS
    )


def _find_matching_recent_media(session, context, receipt):
    """Return a published post matching exact caption and transaction window."""
    if receipt.get("state") != "publishing":
        return None
    response = session.get(
        f"{context['base_url']}/{context['instagram_id']}/media",
        params={
            "fields": "id,caption,timestamp,media_type",
            "limit": 25,
            "access_token": context["access_token"],
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = _response_json(response)
    not_before = _reconcile_not_before(receipt)
    not_after = _utc_now() + datetime.timedelta(
        seconds=RECONCILE_CLOCK_SKEW_SECONDS
    )
    matches = []
    expected_caption = str(receipt.get("caption") or "").replace(
        "\r\n", "\n"
    ).strip()
    for media in payload.get("data") or []:
        if not isinstance(media, dict):
            continue
        media_type = str(media.get("media_type") or "").upper()
        if media_type and media_type != "IMAGE":
            continue
        actual_caption = str(media.get("caption") or "").replace(
            "\r\n", "\n"
        ).strip()
        if actual_caption != expected_caption:
            continue
        timestamp = _parse_datetime(media.get("timestamp"))
        if timestamp is None or timestamp < not_before or timestamp > not_after:
            continue
        if media.get("id"):
            matches.append((timestamp, str(media["id"])))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def _confirm_receipt(receipt, post_id, *, reconciled):
    receipt["state"] = "confirmed"
    receipt["post_id"] = str(post_id)
    receipt["published_at"] = _iso_now()
    receipt["confirmation_source"] = (
        "recent_media_reconciliation" if reconciled else "media_publish_response"
    )
    receipt.pop("last_error", None)
    _write_json_atomic(RECEIPT_PATH, receipt)
    return receipt


def _reconcile_with_retries(
    session, context, receipt, *, attempts=1
):
    last_error = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            post_id = _find_matching_recent_media(
                session, context, receipt
            )
            if post_id:
                confirmed = _confirm_receipt(
                    receipt, post_id, reconciled=True
                )
                print(
                    "[OK] Publicação reconciliada sem repetir o contentor: "
                    f"{post_id}."
                )
                return confirmed
        except (RuntimeError, requests.RequestException) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(RECONCILE_DELAY_SECONDS)
    if last_error and attempts == 1:
        raise RuntimeError(
            f"Não foi possível reconciliar publicações recentes: {last_error}"
        ) from last_error
    return None


def publish_or_reconcile(session=None):
    """Publish the persisted container, reconciling every ambiguous outcome."""
    context = _context()
    receipt = _receipt_for_current_draft(context)
    if _confirmed(receipt):
        print(
            "[OK] Este rascunho já foi publicado no Instagram; "
            f"recibo {receipt['post_id']} reutilizado."
        )
        return receipt
    if receipt.get("state") != "publishing":
        raise RuntimeError(
            "Falta persistir o marcador publishing antes de media_publish."
        )
    if not receipt.get("creation_id"):
        raise RuntimeError("O recibo pendente não contém creation_id.")

    client = session or requests.Session()

    # A persisted "publishing" state may mean a prior runner lost the HTTP
    # response. Reconcile first, before making any repeat request.
    reconciled = _reconcile_with_retries(
        client, context, receipt, attempts=1
    )
    if reconciled:
        return reconciled

    container_state = _wait_until_container_ready(
        client,
        context["base_url"],
        receipt["creation_id"],
        context["access_token"],
    )
    if container_state == "PUBLISHED":
        reconciled = _reconcile_with_retries(
            client,
            context,
            receipt,
            attempts=RECONCILE_ATTEMPTS,
        )
        if reconciled:
            return reconciled
        raise RuntimeError(
            "O contentor já consta como publicado, mas o post não pôde "
            "ser reconciliado; media_publish não foi repetido."
        )

    receipt["last_publish_request_at"] = _iso_now()
    _write_json_atomic(RECEIPT_PATH, receipt)
    try:
        publish_response = client.post(
            f"{context['base_url']}/{context['instagram_id']}/media_publish",
            data={
                "creation_id": receipt["creation_id"],
                "access_token": context["access_token"],
            },
            timeout=25,
        )
        publish_payload = _response_json(publish_response)
        if not publish_response.ok or not publish_payload.get("id"):
            raise RuntimeError(
                "Falha ao publicar o contentor no Instagram: "
                f"{publish_payload}"
            )
    except (RuntimeError, requests.RequestException) as publish_error:
        receipt["last_error"] = str(publish_error)
        _write_json_atomic(RECEIPT_PATH, receipt)
        reconciled = _reconcile_with_retries(
            client,
            context,
            receipt,
            attempts=RECONCILE_ATTEMPTS,
        )
        if reconciled:
            return reconciled
        raise RuntimeError(
            "Resultado de media_publish ambíguo ou falhado; o mesmo "
            "creation_id ficou pendente e nenhum contentor novo será criado. "
            f"Detalhe: {publish_error}"
        ) from publish_error

    confirmed = _confirm_receipt(
        receipt, publish_payload["id"], reconciled=False
    )
    print(
        f"[OK] Publicação confirmada pelo Instagram: {confirmed['post_id']}"
    )
    return confirmed


def post_to_instagram():
    """Backward-compatible local entry point; the workflow uses split phases."""
    prepare_publication()
    mark_publishing()
    return publish_or_reconcile()


def _main():
    parser = argparse.ArgumentParser()
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--prepare", action="store_true")
    phase.add_argument("--mark-publishing", action="store_true")
    phase.add_argument("--publish", action="store_true")
    arguments = parser.parse_args()
    if arguments.prepare:
        prepare_publication()
    elif arguments.mark_publishing:
        mark_publishing()
    elif arguments.publish:
        publish_or_reconcile()
    else:
        post_to_instagram()


if __name__ == "__main__":
    try:
        _main()
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        requests.RequestException,
    ) as exc:
        print(f"[ERROR] Instagram publish failed: {exc}")
        sys.exit(1)
