import os
import re
import socket
import ssl
import threading
import time
import datetime
from collections import deque
from queue import Queue

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
TWITCH_USER_COOLDOWN_SECONDS = max(0, int(os.environ.get("TWITCH_USER_COOLDOWN_SECONDS", "15")))
TWITCH_GLOBAL_COOLDOWN_SECONDS = max(0, int(os.environ.get("TWITCH_GLOBAL_COOLDOWN_SECONDS", "1")))
TWITCH_MAX_QUEUED_QUESTIONS = min(
    3,
    max(
        1,
        int(os.environ.get("TWITCH_MAX_QUEUED_QUESTIONS", "3")),
    ),
)
TWITCH_MIN_SEND_INTERVAL_SECONDS = max(
    1.0,
    float(os.environ.get("TWITCH_MIN_SEND_INTERVAL_SECONDS", "1")),
)
TWITCH_MAX_MESSAGES_PER_30_SECONDS = min(
    19,
    max(
        1,
        int(
            os.environ.get(
                "TWITCH_MAX_MESSAGES_PER_30_SECONDS", "19"
            )
        ),
    ),
)

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697

_last_user_response = {}
_last_channel_response = {}
_cooldown_lock = threading.Lock()
_send_lock = threading.Lock()
_chat_rate_lock = threading.Lock()
_recent_chat_sends = deque()
_send_schedule_lock = threading.Lock()
_channel_send_locks = {}
_channel_slow_seconds = {}
_channel_last_sent = {}
_channel_last_attempt = {}
_channel_waiting_for_send = set()
_channel_pending_questions = {}
_slow_retry_pending = set()
_busy_notice_pending = set()
_queue_full_notice_pending = set()
_user_notice_last = {}
_channel_question_queues = {}
_channel_question_workers = set()
_seen_source_messages = {}
_status_lock = threading.Lock()
_twitch_status = {
    "state": "inactive",
    "detail": "A configuração ainda não foi verificada.",
    "channels": list(TWITCH_CHANNELS),
    "joined_channels": [],
    "channel_states": {},
    "last_error": "",
    "updated_at": "",
    "mentions_received": 0,
    "responses_sent": 0,
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


def _set_channel_status(channel, *, joined=None, slow_seconds=None):
    channel_key = str(channel or "").strip().lstrip("#").lower()
    if not channel_key:
        return
    with _status_lock:
        channel_states = dict(_twitch_status.get("channel_states") or {})
        previous = dict(channel_states.get(channel_key) or {})
        if joined is not None:
            previous["joined"] = bool(joined)
        if slow_seconds is not None:
            previous["slow_seconds"] = max(0, int(slow_seconds))
        channel_states[channel_key] = previous
        _twitch_status["channel_states"] = channel_states
        _twitch_status["joined_channels"] = sorted(
            name
            for name, state in channel_states.items()
            if state.get("joined")
        )
        _twitch_status["updated_at"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z")


def _reset_channel_statuses():
    with _status_lock:
        _twitch_status["channels"] = list(TWITCH_CHANNELS)
        _twitch_status["joined_channels"] = []
        _twitch_status["channel_states"] = {
            channel: {"joined": False, "slow_seconds": 0}
            for channel in TWITCH_CHANNELS
        }
        _twitch_status["updated_at"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z")


def get_twitch_status():
    with _status_lock:
        return dict(_twitch_status)


def _increment_status(field):
    with _status_lock:
        _twitch_status[field] = int(_twitch_status.get(field, 0)) + 1
        _twitch_status["updated_at"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z")


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
    joined_channels = ", ".join(
        f"#{channel}" for channel in status.get("joined_channels", [])
    ) or "nenhum confirmado ainda"
    lines = [
        "### Estado da Twitch",
        f"**{labels.get(status['state'], status['state'])}**",
        f"Canais configurados: `{channels}`",
        f"Canais ligados: `{joined_channels}`",
        (
            "Menções recebidas desde o arranque: "
            f"`{status.get('mentions_received', 0)}`"
        ),
        (
            "Respostas enviadas desde o arranque: "
            f"`{status.get('responses_sent', 0)}`"
        ),
    ]
    channel_states = status.get("channel_states") or {}
    for channel in status["channels"]:
        channel_state = channel_states.get(channel) or {}
        joined = channel_state.get("joined")
        slow_seconds = int(channel_state.get("slow_seconds") or 0)
        if joined:
            detail = (
                f"Modo lento de {slow_seconds}s."
                if slow_seconds
                else "Sem modo lento."
            )
            lines.append(f"`#{channel}`: {detail}")
        else:
            lines.append(f"`#{channel}`: A aguardar confirmaÃ§Ã£o da ligaÃ§Ã£o.")
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


def _channel_send_lock(channel):
    channel_key = str(channel or "").lower()
    with _send_schedule_lock:
        return _channel_send_locks.setdefault(
            channel_key, threading.Lock()
        )


def _wait_for_chat_rate_capacity():
    while True:
        with _chat_rate_lock:
            now = time.monotonic()
            while (
                _recent_chat_sends
                and now - _recent_chat_sends[0] >= 30
            ):
                _recent_chat_sends.popleft()
            if (
                len(_recent_chat_sends)
                < TWITCH_MAX_MESSAGES_PER_30_SECONDS
            ):
                _recent_chat_sends.append(now)
                return
            wait_seconds = max(
                0.05, 30 - (now - _recent_chat_sends[0])
            )
        time.sleep(wait_seconds)


def _send_message(sock, channel, text):
    clean_text = re.sub(r"[\r\n]+", " ", str(text or "")).strip()
    if not clean_text:
        return
    channel_key = str(channel or "").lower()
    channel_lock = _channel_send_lock(channel_key)
    with channel_lock:
        with _send_schedule_lock:
            interval = max(
                TWITCH_MIN_SEND_INTERVAL_SECONDS,
                float(_channel_slow_seconds.get(channel_key, 0)),
            )
            wait_seconds = max(
                0.0,
                float(_channel_last_sent.get(channel_key, 0.0))
                + interval
                - time.monotonic(),
            )
        if wait_seconds > 0:
            with _send_schedule_lock:
                _channel_waiting_for_send.add(channel_key)
        try:
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            _wait_for_chat_rate_capacity()
            _send(sock, f"PRIVMSG #{channel_key} :{clean_text}")
            sent_at = time.monotonic()
            with _send_schedule_lock:
                _channel_last_sent[channel_key] = sent_at
                _channel_last_attempt[channel_key] = {
                    "text": clean_text,
                    "sent_at": sent_at,
                }
        finally:
            with _send_schedule_lock:
                _channel_waiting_for_send.discard(channel_key)


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
        "message_id": tags.get("id", ""),
        "source_id": tags.get("source-id") or tags.get("id", ""),
        "room_id": tags.get("room-id", ""),
        "source_room_id": (
            tags.get("source-room-id") or tags.get("room-id", "")
        ),
    }


def _claim_source_message(message):
    message_id = str(message.get("source_id") or "").strip()
    if not message_id:
        return True
    now = time.monotonic()
    with _cooldown_lock:
        expired = [
            key
            for key, seen_at in _seen_source_messages.items()
            if now - seen_at >= 300
        ]
        for key in expired:
            _seen_source_messages.pop(key, None)
        if message_id in _seen_source_messages:
            return False
        _seen_source_messages[message_id] = now
        return True


def _parse_notice(raw_line):
    line = str(raw_line or "")
    tags = {}
    if line.startswith("@"):
        tag_blob, _, line = line.partition(" ")
        for item in tag_blob[1:].split(";"):
            key, _, value = item.partition("=")
            tags[key] = value
    match = re.match(
        r"^:tmi\.twitch\.tv NOTICE #?([^ ]+) :(.*)$", line
    )
    if not match:
        return None
    return {
        "id": tags.get("msg-id", ""),
        "channel": match.group(1).lower(),
        "text": match.group(2).strip(),
    }


def _parse_roomstate(raw_line):
    line = str(raw_line or "")
    if not line.startswith("@"):
        return None
    tag_blob, _, command = line.partition(" ")
    match = re.match(
        r"^:tmi\.twitch\.tv ROOMSTATE #([^ ]+)$", command
    )
    if not match:
        return None
    tags = {}
    for item in tag_blob[1:].split(";"):
        key, _, value = item.partition("=")
        tags[key] = value
    if "slow" not in tags:
        return None
    try:
        slow_seconds = max(0, int(tags["slow"]))
    except (TypeError, ValueError):
        return None
    return {
        "channel": match.group(1).lower(),
        "slow_seconds": slow_seconds,
    }


def _slowmode_wait_from_notice(notice):
    numbers = re.findall(r"\d+", str(notice.get("text") or ""))
    return max([int(value) for value in numbers], default=1)


def _retry_after_slowmode(sock, channel, text, wait_seconds):
    channel_key = str(channel or "").lower()
    try:
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        if not text:
            return
        _send_message(sock, channel_key, text)
        _increment_status("responses_sent")
        _set_twitch_status(
            "connected",
            "A resposta aguardou pelo modo lento e foi enviada.",
        )
    except Exception as exc:
        _set_twitch_status(
            "error",
            "Não foi possível reenviar uma resposta após o modo lento.",
            error=exc,
        )
    finally:
        with _send_schedule_lock:
            _slow_retry_pending.discard(channel_key)


def _wait_for_slowmode_retry(channel):
    channel_key = str(channel or "").lower()
    while True:
        with _send_schedule_lock:
            retry_pending = channel_key in _slow_retry_pending
        if not retry_pending:
            return
        time.sleep(0.1)


def _extract_question(message):
    text = str(message or "").strip()
    if TWITCH_MENTION_ENABLED and TWITCH_BOT_USERNAME:
        mention = f"@{TWITCH_BOT_USERNAME}"
        if text.lower().startswith(mention.lower()):
            return text[len(mention) :].strip()
    return ""


def _cooldown_status(user, channel):
    now = time.monotonic()
    channel_key = str(channel or "").lower()
    user_key = (channel_key, str(user or "").lower())
    with _cooldown_lock:
        user_remaining = (
            _last_user_response.get(user_key, 0.0)
            + TWITCH_USER_COOLDOWN_SECONDS
            - now
        )
        channel_remaining = (
            _last_channel_response.get(channel_key, 0.0)
            + TWITCH_GLOBAL_COOLDOWN_SECONDS
            - now
        )
        if user_remaining > 0:
            return "user", user_remaining
        with _send_schedule_lock:
            waiting_for_slowmode = (
                channel_key in _channel_waiting_for_send
                and _channel_slow_seconds.get(channel_key, 0) > 0
            )
        if channel_remaining > 0 and not waiting_for_slowmode:
            return "busy", channel_remaining
        _last_user_response[user_key] = now
        _last_channel_response[channel_key] = now
        return "accepted", 0.0


def _cooldown_remaining(user, channel=""):
    _, remaining = _cooldown_status(user, channel)
    return remaining


def _reserve_question_slot(channel):
    channel_key = str(channel or "").lower()
    with _cooldown_lock:
        pending = int(_channel_pending_questions.get(channel_key, 0))
        if pending >= TWITCH_MAX_QUEUED_QUESTIONS + 1:
            return False
        _channel_pending_questions[channel_key] = pending + 1
        return True


def _release_question_slot(channel):
    channel_key = str(channel or "").lower()
    with _cooldown_lock:
        remaining = max(
            0, int(_channel_pending_questions.get(channel_key, 0)) - 1
        )
        if remaining:
            _channel_pending_questions[channel_key] = remaining
        else:
            _channel_pending_questions.pop(channel_key, None)


def _send_queue_full_notice(sock, channel, display_name):
    channel_key = str(channel or "").lower()
    with _cooldown_lock:
        if channel_key in _queue_full_notice_pending:
            return
        _queue_full_notice_pending.add(channel_key)
    try:
        _send_message(
            sock,
            channel_key,
            (
                f"@{display_name} estão a chegar várias perguntas ao mesmo "
                "tempo. Por favor, envia novamente dentro de instantes."
            ),
        )
        _increment_status("responses_sent")
    finally:
        with _cooldown_lock:
            _queue_full_notice_pending.discard(channel_key)


def _send_busy_notice(sock, channel, display_name):
    channel_key = str(channel or "").lower()
    with _cooldown_lock:
        if channel_key in _busy_notice_pending:
            return
        _busy_notice_pending.add(channel_key)
    try:
        _send_message(
            sock,
            channel_key,
            (
                f"@{display_name} estão a chegar várias perguntas ao mesmo "
                "tempo. Por favor, envia novamente dentro de instantes."
            ),
        )
        _increment_status("responses_sent")
    finally:
        with _cooldown_lock:
            _busy_notice_pending.discard(channel_key)


def _send_user_cooldown_notice(
    sock, channel, display_name, user, remaining
):
    notice_key = (
        str(channel or "").lower(),
        str(user or "").lower(),
    )
    now = time.monotonic()
    with _cooldown_lock:
        if (
            _user_notice_last.get(notice_key, 0.0)
            + TWITCH_USER_COOLDOWN_SECONDS
            > now
        ):
            return
        _user_notice_last[notice_key] = now
    _send_message(
        sock,
        channel,
        (
            f"@{display_name} aguarda mais {int(remaining) + 1}s "
            "antes de enviar outra pergunta."
        ),
    )
    _increment_status("responses_sent")


def _answer_question(sock, channel, display_name, user, question):
    try:
        if not question:
            _send_message(
                sock,
                channel,
                f"@{display_name} menciona @{TWITCH_BOT_USERNAME} e escreve a tua pergunta.",
            )
            _increment_status("responses_sent")
            return

        response = query_politometro_chat(
            question, source="twitch-bot", user_id=user
        )
        _wait_for_slowmode_retry(channel)
        _send_message(
            sock,
            channel,
            format_twitch_response(response, display_name),
        )
        _increment_status("responses_sent")
        _set_twitch_status(
            "connected",
            "A última pergunta recebida foi respondida.",
        )
    except Exception as exc:
        _set_twitch_status(
            "error",
            "Foi recebida uma pergunta, mas não foi possível enviar a resposta.",
            error=exc,
        )
        print(f"Falha ao responder no chat da Twitch: {exc}")
    finally:
        _release_question_slot(channel)


def _question_worker(channel):
    channel_key = str(channel or "").lower()
    while True:
        with _cooldown_lock:
            question_queue = _channel_question_queues[channel_key]
        task = question_queue.get()
        try:
            _answer_question(*task)
        finally:
            question_queue.task_done()


def _enqueue_question(sock, message, question):
    channel_key = message["channel"]
    with _cooldown_lock:
        question_queue = _channel_question_queues.setdefault(
            channel_key, Queue()
        )
        if channel_key not in _channel_question_workers:
            _channel_question_workers.add(channel_key)
            threading.Thread(
                target=_question_worker,
                args=(channel_key,),
                daemon=True,
            ).start()
    question_queue.put(
        (
            sock,
            channel_key,
            message["display_name"],
            message["user"],
            question,
        )
    )


def _handle_incoming_question(sock, message, question):
    channel = message["channel"]
    cooldown_state, remaining = _cooldown_status(
        message["user"], channel
    )
    if cooldown_state == "busy":
        threading.Thread(
            target=_send_busy_notice,
            args=(sock, channel, message["display_name"]),
            daemon=True,
        ).start()
        return
    if cooldown_state == "user":
        threading.Thread(
            target=_send_user_cooldown_notice,
            args=(
                sock,
                channel,
                message["display_name"],
                message["user"],
                remaining,
            ),
            daemon=True,
        ).start()
        return
    if not _reserve_question_slot(channel):
        threading.Thread(
            target=_send_queue_full_notice,
            args=(sock, channel, message["display_name"]),
            daemon=True,
        ).start()
        return
    _enqueue_question(sock, message, question)


def run_twitch_bot_forever():
    if not twitch_configured():
        _set_twitch_status(
            "inactive",
            "Configura o nome do bot, token e pelo menos um canal.",
        )
        print("Twitch bot nao configurado: define TWITCH_BOT_USERNAME, TWITCH_OAUTH_TOKEN e TWITCH_CHANNELS.")
        return

    backoff = 5
    _reset_channel_statuses()
    _set_twitch_status("starting", "A preparar a ligação segura ao chat.")
    while True:
        try:
            print(f"A iniciar bot de Twitch em: {', '.join(TWITCH_CHANNELS)}")
            _reset_channel_statuses()
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
                            roomstate = _parse_roomstate(line)
                            if roomstate:
                                with _send_schedule_lock:
                                    _channel_slow_seconds[
                                        roomstate["channel"]
                                    ] = roomstate["slow_seconds"]
                                _set_channel_status(
                                    roomstate["channel"],
                                    joined=True,
                                    slow_seconds=roomstate["slow_seconds"],
                                )
                                joined_count = len(
                                    get_twitch_status().get(
                                        "joined_channels", []
                                    )
                                )
                                slow_detail = (
                                    f"Modo lento de "
                                    f"{roomstate['slow_seconds']}s detetado."
                                    if roomstate["slow_seconds"]
                                    else "Sem modo lento."
                                )
                                _set_twitch_status(
                                    "connected",
                                    (
                                        f"Ligado a {joined_count}/"
                                        f"{len(TWITCH_CHANNELS)} canais. "
                                        f"#{roomstate['channel']}: "
                                        f"{slow_detail}"
                                    ),
                                )
                                continue
                            notice = _parse_notice(line)
                            if notice:
                                if notice["id"] == "msg_slowmode":
                                    wait_seconds = (
                                        _slowmode_wait_from_notice(notice)
                                    )
                                    channel_key = notice["channel"]
                                    with _send_schedule_lock:
                                        _channel_slow_seconds[channel_key] = (
                                            max(
                                                _channel_slow_seconds.get(
                                                    channel_key, 0
                                                ),
                                                wait_seconds,
                                            )
                                        )
                                        attempt = dict(
                                            _channel_last_attempt.get(
                                                channel_key
                                            )
                                            or {}
                                        )
                                        should_retry = bool(
                                            attempt.get("text")
                                            and channel_key
                                            not in _slow_retry_pending
                                        )
                                        if should_retry:
                                            _slow_retry_pending.add(
                                                channel_key
                                            )
                                    if should_retry:
                                        _set_twitch_status(
                                            "connected",
                                            (
                                                "A resposta está a aguardar "
                                                "pelo fim do modo lento."
                                            ),
                                        )
                                        threading.Thread(
                                            target=_retry_after_slowmode,
                                            args=(
                                                sock,
                                                channel_key,
                                                attempt["text"],
                                                wait_seconds,
                                            ),
                                            daemon=True,
                                        ).start()
                                    continue
                                _set_twitch_status(
                                    "error",
                                    (
                                        "A Twitch recusou uma ação do bot no "
                                        "chat. Consulta o motivo abaixo."
                                    ),
                                    error=notice["text"],
                                )
                                print(
                                    "Aviso do chat Twitch "
                                    f"({notice['id'] or 'sem código'}): "
                                    f"{notice['text']}"
                                )
                                continue
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
                                if not _claim_source_message(message):
                                    continue
                                _increment_status("mentions_received")
                                _set_twitch_status(
                                    "connected",
                                    (
                                        "Foi recebida uma menção e a resposta "
                                        "está a ser preparada."
                                    ),
                                )
                                _handle_incoming_question(
                                    sock, message, question
                                )
        except Exception as exc:
            _set_twitch_status(
                "reconnecting",
                f"Nova tentativa automática em {backoff} segundos.",
                error=exc,
            )
            print(f"Twitch bot desligou-se: {exc}. Nova tentativa em {backoff}s.")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
