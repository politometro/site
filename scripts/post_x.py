"""Publica o rascunho semanal aprovado no X (Twitter).

Publica a mesma imagem do post (current_post.jpg) com OAuth 1.0a
(user context) e a mesma legenda adaptada ao limite de 280 caracteres do X:
quando a legenda é maior, é truncada ao fim de uma palavra com reticências
(limitação da plataforma; o texto integral continua no Instagram e no
Facebook). Imagens JPEG até 5 MB são aceites; se o ficheiro for maior, é
recodificado localmente com o Pillow antes do upload.

Idempotente: guarda um recibo em scripts/x_publication.json e não volta a
publicar o mesmo rascunho (draft_id + content_hash).

Segredos necessários (GitHub):
  X_API_KEY               — API Key (consumer key)
  X_API_SECRET            — API Key Secret (consumer secret)
  X_ACCESS_TOKEN          — Access Token da conta que publica
  X_ACCESS_TOKEN_SECRET   — Access Token Secret
"""

import argparse
import datetime
import hashlib
import hmac
import io
import json
import os
import random
import sys
import time
import urllib.parse

import requests

import publication_schedule

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DRAFT_PATH = os.path.join(SCRIPT_DIR, "review_draft.json")
RECEIPT_PATH = os.path.join(SCRIPT_DIR, "x_publication.json")
IMAGE_PATH = os.path.join(ROOT_DIR, "website", "public", "current_post.jpg")
CAPTION_PATH = os.path.join(ROOT_DIR, "website", "public", "current_caption.txt")

API_BASE = "https://api.x.com"
MAX_TWEET_CHARS = 280
MAX_MEDIA_BYTES = 5 * 1024 * 1024


def _load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_receipt(payload):
    tmp = f"{RECEIPT_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, RECEIPT_PATH)


def _approved_context():
    draft = _load_json(DRAFT_PATH)
    approval = publication_schedule.effective_approval(draft) or {}
    if draft.get("is_test") or not approval.get("approved"):
        raise RuntimeError("O rascunho não está aprovado para publicação.")
    if (
        not draft.get("draft_id")
        or not draft.get("content_hash")
        or approval.get("draft_id") != draft.get("draft_id")
        or approval.get("content_hash") != draft.get("content_hash")
    ):
        raise RuntimeError("A aprovação não corresponde ao conteúdo do rascunho.")
    with open(CAPTION_PATH, "r", encoding="utf-8") as handle:
        caption = handle.read().strip()
    if not caption:
        raise RuntimeError("A legenda revista está vazia.")
    if not os.path.exists(IMAGE_PATH):
        raise RuntimeError("A imagem revista está em falta.")
    return draft, caption


def _already_published(draft):
    if not os.path.exists(RECEIPT_PATH):
        return False
    try:
        receipt = _load_json(RECEIPT_PATH)
    except (OSError, ValueError):
        return False
    return (
        receipt.get("draft_id") == draft.get("draft_id")
        and receipt.get("content_hash") == draft.get("content_hash")
        and bool(receipt.get("post_id"))
    )


def _tweet_text(caption):
    """Legenda integral quando cabe; senão truncagem ao fim de palavra."""
    if len(caption) <= MAX_TWEET_CHARS:
        return caption
    cut = caption[: MAX_TWEET_CHARS - 1].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip()
    return f"{cut}…"


def _load_image_bytes():
    with open(IMAGE_PATH, "rb") as handle:
        data = handle.read()
    if len(data) <= MAX_MEDIA_BYTES:
        return data
    # Recodifica localmente para caber no limite do X.
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    for quality in (90, 85, 80, 75, 70):
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True)
        data = buffer.getvalue()
        if len(data) <= MAX_MEDIA_BYTES:
            break
    if len(data) > MAX_MEDIA_BYTES:
        raise RuntimeError("A imagem excede 5 MB mesmo após recodificação.")
    return data


def _percent_encode(value):
    return urllib.parse.quote(str(value), safe="")


def _oauth1_headers(method, url, credentials, json_body=None, multipart_params=None):
    """Assinatura HMAC-SHA1 (RFC 5849) sem dependências externas."""
    oauth_params = {
        "oauth_consumer_key": credentials["api_key"],
        "oauth_nonce": "".join(
            random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(24)
        ),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": credentials["access_token"],
        "oauth_version": "1.0",
    }
    signature_params = dict(oauth_params)
    if json_body is None and multipart_params:
        signature_params.update(multipart_params)
    ordered = sorted(signature_params.items())
    param_string = "&".join(
        f"{_percent_encode(key)}={_percent_encode(value)}" for key, value in ordered
    )
    base_string = "&".join(
        (
            method.upper(),
            _percent_encode(url),
            _percent_encode(param_string),
        )
    )
    signing_key = "&".join(
        (_percent_encode(credentials["api_secret"]), _percent_encode(credentials["access_secret"]))
    )
    signature = hmac.new(
        signing_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    import base64

    oauth_params["oauth_signature"] = base64.b64encode(signature).decode("ascii")
    header = ", ".join(
        f'{_percent_encode(key)}="{_percent_encode(value)}"'
        for key, value in sorted(oauth_params.items())
    )
    return {"Authorization": f"OAuth {header}"}


def _credentials():
    values = {
        "api_key": os.environ.get("X_API_KEY", "").strip(),
        "api_secret": os.environ.get("X_API_SECRET", "").strip(),
        "access_token": os.environ.get("X_ACCESS_TOKEN", "").strip(),
        "access_secret": os.environ.get("X_ACCESS_TOKEN_SECRET", "").strip(),
    }
    if not all(values.values()):
        raise RuntimeError(
            "X_API_KEY/X_API_SECRET/X_ACCESS_TOKEN/X_ACCESS_TOKEN_SECRET não "
            "configurados; a publicação no X foi ignorada."
        )
    return values


def _upload_media(credentials, image_bytes):
    url = f"{API_BASE}/2/media/upload"
    files = {"media": ("post.jpg", image_bytes, "image/jpeg")}
    data = {"media_category": "tweet_image"}
    headers = _oauth1_headers(
        "POST",
        url,
        credentials,
        multipart_params={"media_category": "tweet_image"},
    )
    response = requests.post(
        url,
        headers=headers,
        files=files,
        data=data,
        timeout=120,
    )
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if not response.ok:
        error = payload.get("errors") or payload.get("detail") or f"HTTP {response.status_code}"
        raise RuntimeError(f"O upload de media para o X falhou: {error}")
    media_id = str(payload.get("data", {}).get("id") or "").strip()
    if not media_id:
        raise RuntimeError("O upload de media para o X não devolveu um identificador.")
    return media_id


def _create_tweet(credentials, text, media_id):
    url = f"{API_BASE}/2/tweets"
    body = {
        "text": text,
        "media": {"media_ids": [media_id]},
    }
    headers = _oauth1_headers("POST", url, credentials, json_body=body)
    headers["Content-Type"] = "application/json"
    response = requests.post(url, headers=headers, data=json.dumps(body), timeout=60)
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if not response.ok:
        error = payload.get("errors") or payload.get("detail") or f"HTTP {response.status_code}"
        raise RuntimeError(f"A API do X recusou a publicação: {error}")
    tweet_id = str(payload.get("data", {}).get("id") or "").strip()
    if not tweet_id:
        raise RuntimeError("A API do X não devolveu um identificador de publicação.")
    return tweet_id


def publish_x():
    credentials = _credentials()
    draft, caption = _approved_context()
    if _already_published(draft):
        print("[OK] O rascunho atual já foi publicado no X; nada a fazer.")
        return

    image_bytes = _load_image_bytes()
    media_id = _upload_media(credentials, image_bytes)
    tweet_id = _create_tweet(credentials, _tweet_text(caption), media_id)
    handle = os.environ.get("X_ACCOUNT_HANDLE", "").strip().lstrip("@")
    permalink = f"https://x.com/{handle}/status/{tweet_id}" if handle else ""
    _write_receipt(
        {
            "schema_version": 1,
            "network": "x",
            "draft_id": draft.get("draft_id"),
            "content_hash": draft.get("content_hash"),
            "post_id": tweet_id,
            "permalink": permalink,
            "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    print(f"[OK] Publicado no X: {permalink or tweet_id}")


def check_x():
    credentials = _credentials()
    username = os.environ.get("X_ACCOUNT_HANDLE", "").strip().lstrip("@")
    if not username:
        raise RuntimeError("X_ACCOUNT_HANDLE não configurado (@da conta que publica).")
    url = f"{API_BASE}/2/users/by/username/{username}"
    headers = _oauth1_headers("GET", url, credentials)
    response = requests.get(url, headers=headers, timeout=30)
    if not response.ok:
        raise RuntimeError(
            f"O token do X não acede à conta @{username} (HTTP {response.status_code})."
        )
    print(f"[OK] Acesso ao X confirmado para a conta: @{username}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-access", action="store_true")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publica o rascunho aprovado no X (idempotente por rascunho).",
    )
    parser.add_argument("--retry", type=int, default=2)
    args = parser.parse_args()

    try:
        if args.check_access:
            check_x()
            return
        if args.publish:
            last_error = None
            for attempt in range(args.retry + 1):
                try:
                    publish_x()
                    return
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt < args.retry:
                        time.sleep(5 * (attempt + 1))
            raise RuntimeError(f"Falha de rede ao publicar no X: {last_error}")
        parser.error("indica --check-access ou --publish")
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
