import os
import re
import socket
import ssl
import threading
import time
import datetime

from politometro_chat import query_politometro_chat
from twitch_auth import token_manager


TWITCH_BOT_USERNAME = os.environ.get("TWITCH_BOT_USERNAME", "").strip().lower()
TWITCH_OAUTH_TOKEN = os.environ.get("TWITCH_OAUTH_TOKEN", "").strip()
TWITCH_CHANNELS = [
    channel.strip().lstrip("#").lower()
    for channel in os.environ.get("TWITCH_CHANNELS", os.environ.get("TWITCH_CHANNEL", "")).split(",")
    if channel.strip()
]
TWITCH_MENTION_ENABLED = os.environ.get("TWITCH_MENTION_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
TWITCH_RESPONSE_LIMIT = min(
    430,
    max(180, int(os.environ.get("TWITCH_RESPONSE_LIMIT", "430"))),
)
TWITCH_USER_COOLDOWN_SECONDS = max(0, int(os.environ.get("TWITCH_USER_COOLDOWN_SECONDS", "30")))
TWITCH_GLOBAL_COOLDOWN_SECONDS = max(0, int(os.environ.get("TWITCH_GLOBAL_COOLDOWN_SECONDS", "5")))

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697

_last_user_response = {}
_last_global_response = 0.0
_cooldown_lock = threading.Lock()
_send_lock = threading.Lock()
_status_lock = threading.Lock()
_twitch_status = {
    "state": "inactive",
    "detail": "A configuração ainda não foi verificada.",
    "channels": list(TWITCH_CHANNELS),
    "last_error": "",
    "updated_at": "",
}


def _set_twitch_status(state, detail="", error=""):
    secrets = (
        TWITCH_OAUTH_TOKEN,
        os.environ.get("TWITCH_REFRESH_TOKEN", ""),
        os.environ.get("TWITCH_CLIENT_SECRET", ""),
    )
    safe_error = str(error or "")
    for secret in secrets:
        if secret:
            safe_error = safe_error.replace(secret, "***")
    with _status_lock:
        _twitch_status.update(
            {
                "state": state,
                "detail": str(detail or ""),
                "last_error": safe_error[:500],
                "updated_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat().replace("+00:00", "Z"),
            }
        )


def get_twitch_status():
    with _status_lock:
        return dict(_twitch_status)


def twitch_status_markdown():
    status = get_twitch_status()
    labels = {
        "inactive": "⚪ Inativo",
        "starting": "🟡 A iniciar",
        "connecting": "🟡 A ligar",
        "authenticating": "🟡 A autenticar",
        "connected": "🟢 Ligado",
        "reconnecting": "🟠 A voltar a ligar",
        "error": "🔴 Erro",
    }
    channels = ", ".join(status["channels"]) or "nenhum"
    lines = [
        "### Estado da Twitch",
        f"**{labels.get(status['state'], status['state'])}**",
        f"Canais configurados: `{channels}`",
    ]
    if status.get("detail"):
        lines.append(status["detail"])
    if status.get("last_error"):
        lines.append(f"Último erro: `{status['last_error']}`")
    if status.get("updated_at"):
        lines.append(f"Verificado em: `{status['updated_at']}`")
    return "\n\n".join(lines)


def twitch_configured():
    return bool(TWITCH_BOT_USERNAME and TWITCH_OAUTH_TOKEN and TWITCH_CHANNELS)


def twitch_refresh_configured():
    return token_manager.refresh_configured


def _oauth_token():
    token = token_manager.get_access_token()
    return token if token.startswith("oauth:") else f"oauth:{token}"


def _send(sock, line):
    with _send_lock:
        sock.sendall(f"{line}\r\n".encode("utf-8"))


def _send_message(sock, channel, text):
    clean_text = re.sub(r"[\r\n]+", " ", str(text or "")).strip()
    if not clean_text:
        return
    _send(sock, f"PRIVMSG #{channel} :{clean_text}")


def _utf8_prefix(value, max_bytes):
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(value or "")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def format_twitch_response(response, display_name):
    """Build exactly one IRC-safe answer, including the user mention."""
    prefix = f"@{display_name} "
    text = re.sub(r"\s+", " ", str(response or "")).strip()
    text = re.sub(r"(?<!\w)[#*_`]+|[#*_`]+(?!\w)", "", text).strip()
    available = max(
        1,
        TWITCH_RESPONSE_LIMIT - len(prefix.encode("utf-8")),
    )
    if len(text.encode("utf-8")) <= available:
        return prefix + text

    suffix = "…"
    content_budget = max(1, available - len(suffix.encode("utf-8")))
    shortened = _utf8_prefix(text, content_budget).rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return prefix + shortened + suffix


def _parse_privmsg(raw_line):
    # IRC lines can include tags before the user prefix.
    line = raw_line
    tags = {}
    if line.startswith("@"):
        tag_blob, _, line = line.partition(" ")
        for item in tag_blob[1:].split(";"):
            key, _, value = item.partition("=")
            tags[key] = value

    match = re.match(r"^:([^!]+)![^ ]+ PRIVMSG #([^ ]+) :(.*)$", line)
    if not match:
        return None
    return {
        "user": match.group(1).lower(),
        "display_name": tags.get("display-name") or match.group(1),
        "channel": match.group(2).lower(),
        "text": match.group(3).strip(),
    }


def _extract_question(message):
    text = str(message or "").strip()
    if TWITCH_MENTION_ENABLED and TWITCH_BOT_USERNAME:
        mention = f"@{TWITCH_BOT_USERNAME}"
        if text.lower().startswith(mention.lower()):
            return text[len(mention) :].strip()
    return ""


def _cooldown_remaining(user):
    global _last_global_response
    now = time.monotonic()
    with _cooldown_lock:
        user_remaining = (
            _last_user_response.get(user, 0.0) + TWITCH_USER_COOLDOWN_SECONDS - now
        )
        global_remaining = _last_global_response + TWITCH_GLOBAL_COOLDOWN_SECONDS - now
        remaining = max(user_remaining, global_remaining, 0.0)
        if remaining <= 0:
            _last_user_response[user] = now
            _last_global_response = now
        return remaining


def _answer_question(sock, channel, display_name, user, question):
    remaining = _cooldown_remaining(user)
    if remaining > 0:
        _send_message(
            sock,
            channel,
            f"@{display_name} espera mais {int(remaining) + 1}s antes de fazer nova pergunta.",
        )
        return

    if not question:
        _send_message(
            sock,
            channel,
            f"@{display_name} menciona @{TWITCH_BOT_USERNAME} e escreve a tua pergunta.",
        )
        return

    response = query_politometro_chat(
        question, source="twitch-bot", user_id=user
    )
    _send_message(
        sock,
        channel,
        format_twitch_response(response, display_name),
    )


def run_twitch_bot_forever():
    if not twitch_configured():
        _set_twitch_status(
            "inactive",
            "Configura o nome do bot, token e pelo menos um canal.",
        )
        print("Twitch bot nao configurado: define TWITCH_BOT_USERNAME, TWITCH_OAUTH_TOKEN e TWITCH_CHANNELS.")
        return

    backoff = 5
    _set_twitch_status("starting", "A preparar a ligação segura ao chat.")
    while True:
        try:
            print(f"A iniciar bot de Twitch em: {', '.join(TWITCH_CHANNELS)}")
            _set_twitch_status(
                "connecting", "A estabelecer ligação com a Twitch."
            )
            context = ssl.create_default_context()
            with socket.create_connection((IRC_HOST, IRC_PORT), timeout=30) as raw_sock:
                with context.wrap_socket(raw_sock, server_hostname=IRC_HOST) as sock:
                    sock.settimeout(300)
                    _send(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
                    connection_token = _oauth_token()
                    _send(sock, f"PASS {connection_token}")
                    _send(sock, f"NICK {TWITCH_BOT_USERNAME}")
                    for channel in TWITCH_CHANNELS:
                        _send(sock, f"JOIN #{channel}")
                    _set_twitch_status(
                        "authenticating",
                        "Token enviado; a aguardar confirmação da Twitch.",
                    )

                    backoff = 5
                    buffer = ""
                    while True:
                        data = sock.recv(4096)
                        if not data:
                            raise ConnectionError("Ligacao Twitch encerrada.")
                        buffer += data.decode("utf-8", errors="replace")
                        if (
                            time.monotonic() - token_manager.last_validated
                            >= 3600
                        ):
                            current_token = token_manager.get_access_token(
                                force_validate=True
                            )
                            if f"oauth:{current_token}" != connection_token:
                                raise ConnectionError(
                                    "Token Twitch renovado; a restabelecer "
                                    "a ligação autenticada."
                                )
                        while "\r\n" in buffer:
                            line, buffer = buffer.split("\r\n", 1)
                            if not line:
                                continue
                            if line.startswith("PING"):
                                _send(sock, line.replace("PING", "PONG", 1))
                                continue
                            if (
                                "NOTICE * :Login authentication failed"
                                in line
                            ):
                                raise RuntimeError(
                                    "A Twitch recusou o token da conta do bot."
                                )
                            if re.search(
                                rf":tmi\.twitch\.tv 001 "
                                rf"{re.escape(TWITCH_BOT_USERNAME)}\b",
                                line,
                                flags=re.IGNORECASE,
                            ):
                                _set_twitch_status(
                                    "connected",
                                    "Conta autenticada e canais solicitados.",
                                )
                                print(
                                    "Bot Twitch autenticado como "
                                    f"{TWITCH_BOT_USERNAME}."
                                )

                            message = _parse_privmsg(line)
                            if not message or message["user"] == TWITCH_BOT_USERNAME:
                                continue

                            question = _extract_question(message["text"])
                            if question:
                                threading.Thread(
                                    target=_answer_question,
                                    args=(
                                        sock,
                                        message["channel"],
                                        message["display_name"],
                                        message["user"],
                                        question,
                                    ),
                                    daemon=True,
                                ).start()
        except Exception as exc:
            _set_twitch_status(
                "reconnecting",
                f"Nova tentativa automática em {backoff} segundos.",
                error=exc,
            )
            print(f"Twitch bot desligou-se: {exc}. Nova tentativa em {backoff}s.")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
