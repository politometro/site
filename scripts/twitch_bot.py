import os
import re
import socket
import ssl
import threading
import time

from politometro_chat import query_politometro_chat, split_text_chunks


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
TWITCH_RESPONSE_LIMIT = max(120, int(os.environ.get("TWITCH_RESPONSE_LIMIT", "450")))
TWITCH_USER_COOLDOWN_SECONDS = max(0, int(os.environ.get("TWITCH_USER_COOLDOWN_SECONDS", "30")))
TWITCH_GLOBAL_COOLDOWN_SECONDS = max(0, int(os.environ.get("TWITCH_GLOBAL_COOLDOWN_SECONDS", "5")))

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697

_last_user_response = {}
_last_global_response = 0.0
_cooldown_lock = threading.Lock()
_send_lock = threading.Lock()


def twitch_configured():
    return bool(TWITCH_BOT_USERNAME and TWITCH_OAUTH_TOKEN and TWITCH_CHANNELS)


def _oauth_token():
    token = TWITCH_OAUTH_TOKEN
    return token if token.startswith("oauth:") else f"oauth:{token}"


def _send(sock, line):
    with _send_lock:
        sock.sendall(f"{line}\r\n".encode("utf-8"))


def _send_message(sock, channel, text):
    clean_text = re.sub(r"[\r\n]+", " ", str(text or "")).strip()
    if not clean_text:
        return
    _send(sock, f"PRIVMSG #{channel} :{clean_text}")


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

    response = query_politometro_chat(question, source="twitch-bot", user_id=user)
    for index, chunk in enumerate(split_text_chunks(response, TWITCH_RESPONSE_LIMIT)):
        prefix = f"@{display_name} " if index == 0 else ""
        _send_message(sock, channel, f"{prefix}{chunk}")
        time.sleep(1.2)


def run_twitch_bot_forever():
    if not twitch_configured():
        print("Twitch bot nao configurado: define TWITCH_BOT_USERNAME, TWITCH_OAUTH_TOKEN e TWITCH_CHANNELS.")
        return

    backoff = 5
    while True:
        try:
            print(f"A iniciar bot de Twitch em: {', '.join(TWITCH_CHANNELS)}")
            context = ssl.create_default_context()
            with socket.create_connection((IRC_HOST, IRC_PORT), timeout=30) as raw_sock:
                with context.wrap_socket(raw_sock, server_hostname=IRC_HOST) as sock:
                    sock.settimeout(300)
                    _send(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
                    _send(sock, f"PASS {_oauth_token()}")
                    _send(sock, f"NICK {TWITCH_BOT_USERNAME}")
                    for channel in TWITCH_CHANNELS:
                        _send(sock, f"JOIN #{channel}")

                    backoff = 5
                    buffer = ""
                    while True:
                        data = sock.recv(4096)
                        if not data:
                            raise ConnectionError("Ligacao Twitch encerrada.")
                        buffer += data.decode("utf-8", errors="replace")
                        while "\r\n" in buffer:
                            line, buffer = buffer.split("\r\n", 1)
                            if not line:
                                continue
                            if line.startswith("PING"):
                                _send(sock, line.replace("PING", "PONG", 1))
                                continue

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
            print(f"Twitch bot desligou-se: {exc}. Nova tentativa em {backoff}s.")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
