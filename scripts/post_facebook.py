"""Publica o rascunho semanal aprovado na página do Facebook.

Publica exatamente o mesmo post do Instagram: a mesma imagem
(website/public/current_post.jpg) e a mesma legenda (current_caption.txt).
O Facebook aceita a imagem 1080×1350 sem alterações.

Idempotente: guarda um recibo em scripts/facebook_publication.json e não
republish se o mesmo rascunho (draft_id + content_hash) já foi publicado.
A decisão de janela/aprovação é a mesma do Instagram (publication_schedule),
incluindo a publicação automática de rascunhos sem decisão.

Segredos necessários (GitHub):
  FACEBOOK_PAGE_ID       — ID da página do Facebook
  FACEBOOK_ACCESS_TOKEN  — token de Página com pages_manage_posts
                           (pode ser o mesmo token já usado para o Instagram)
  GITHUB_REPOSITORY      — fornecido pelo Actions
"""

import argparse
import datetime
import json
import os
import sys
import time

import requests

import publication_schedule

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DRAFT_PATH = os.path.join(SCRIPT_DIR, "review_draft.json")
RECEIPT_PATH = os.path.join(SCRIPT_DIR, "facebook_publication.json")
IMAGE_PATH = os.path.join(ROOT_DIR, "website", "public", "current_post.jpg")
CAPTION_PATH = os.path.join(ROOT_DIR, "website", "public", "current_caption.txt")

DEFAULT_GRAPH_VERSION = "v25.0"
MAX_CAPTION_CHARS = 6_300  # limite de legendas de fotos no Facebook


def _graph_version():
    version = os.environ.get("META_GRAPH_API_VERSION", DEFAULT_GRAPH_VERSION).strip()
    return version or DEFAULT_GRAPH_VERSION


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


def _image_url():
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    commit_sha = os.environ.get("GITHUB_SHA", "main").strip() or "main"
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY não está configurado.")
    return (
        f"https://raw.githubusercontent.com/{repository}/"
        f"{commit_sha}/website/public/current_post.jpg"
    )


def publish_facebook():
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "").strip()
    token = os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip()
    if not page_id or not token:
        raise RuntimeError(
            "FACEBOOK_PAGE_ID/FACEBOOK_ACCESS_TOKEN não configurados; "
            "a publicação no Facebook foi ignorada."
        )

    draft, caption = _approved_context()
    if _already_published(draft):
        print(
            "[OK] O rascunho atual já foi publicado no Facebook; nada a fazer."
        )
        return

    if len(caption) > MAX_CAPTION_CHARS:
        caption = caption[: MAX_CAPTION_CHARS - 1].rstrip() + "…"

    graph = f"https://graph.facebook.com/{_graph_version()}"
    response = requests.post(
        f"{graph}/{page_id}/photos",
        data={
            "url": _image_url(),
            "caption": caption,
            "access_token": token,
        },
        timeout=60,
    )
    payload = {}
    if response.headers.get("content-type", "").startswith("application/json"):
        payload = response.json()
    if not response.ok:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        message = error.get("message") or f"HTTP {response.status_code}"
        raise RuntimeError(f"A API do Facebook recusou a publicação: {message}")

    photo_id = str(payload.get("post_id") or payload.get("id") or "").strip()
    if not photo_id:
        raise RuntimeError("A API do Facebook não devolveu um identificador de publicação.")

    permalink = ""
    try:
        lookup = requests.get(
            f"{graph}/{photo_id}",
            params={
                "fields": "permalink_url",
                "access_token": token,
            },
            timeout=30,
        )
        if lookup.ok:
            permalink = str(lookup.json().get("permalink_url") or "")
    except requests.RequestException:
        permalink = ""

    _write_receipt(
        {
            "schema_version": 1,
            "network": "facebook",
            "draft_id": draft.get("draft_id"),
            "content_hash": draft.get("content_hash"),
            "post_id": photo_id,
            "permalink": permalink,
            "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    print(f"[OK] Publicado no Facebook: {photo_id} {permalink}".rstrip())


def check_facebook():
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "").strip()
    token = os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip()
    if not page_id or not token:
        raise RuntimeError(
            "FACEBOOK_PAGE_ID/FACEBOOK_ACCESS_TOKEN não configurados."
        )
    graph = f"https://graph.facebook.com/{_graph_version()}"
    response = requests.get(
        f"{graph}/{page_id}",
        params={"fields": "id,name", "access_token": token},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"O token do Facebook não acede à página {page_id} (HTTP {response.status_code})."
        )
    name = response.json().get("name", page_id)
    print(f"[OK] Acesso ao Facebook confirmado para a página: {name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-access", action="store_true")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publica o rascunho aprovado no Facebook (idempotente por rascunho).",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=2,
        help="Tentativas extras para erros transitórios (5xx/rede).",
    )
    args = parser.parse_args()

    try:
        if args.check_access:
            check_facebook()
            return
        if args.publish:
            last_error = None
            for attempt in range(args.retry + 1):
                try:
                    publish_facebook()
                    return
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt < args.retry:
                        time.sleep(5 * (attempt + 1))
            raise RuntimeError(f"Falha de rede ao publicar no Facebook: {last_error}")
        parser.error("indica --check-access ou --publish")
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
