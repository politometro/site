"""Twitch user-token validation and refresh for the IRC bot."""

import json
import os
import tempfile
import threading
import time

import requests


VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
REQUIRED_SCOPES = {"chat:read", "chat:edit"}
VALIDATION_INTERVAL_SECONDS = 3600


def _default_state_file():
    configured = os.environ.get("TWITCH_TOKEN_STATE_FILE", "").strip()
    if configured:
        return configured
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "/data/politometro_twitch_tokens.json"
    return os.path.join(
        tempfile.gettempdir(), "politometro_twitch_tokens.json"
    )


class TwitchTokenManager:
    def __init__(self, session=None, state_file=None):
        self.session = session or requests.Session()
        self.state_file = state_file or _default_state_file()
        self.access_token = (
            os.environ.get("TWITCH_OAUTH_TOKEN", "")
            .strip()
            .removeprefix("oauth:")
        )
        self.refresh_token = os.environ.get(
            "TWITCH_REFRESH_TOKEN", ""
        ).strip()
        self.client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get(
            "TWITCH_CLIENT_SECRET", ""
        ).strip()
        self.expected_login = os.environ.get(
            "TWITCH_BOT_USERNAME", ""
        ).strip().lower()
        self.last_validated = 0.0
        self._lock = threading.Lock()
        self._load_state()

    @property
    def refresh_configured(self):
        return bool(
            self.refresh_token and self.client_id and self.client_secret
        )

    def _load_state(self):
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            access_token = str(state.get("access_token") or "").strip()
            refresh_token = str(state.get("refresh_token") or "").strip()
            if access_token and refresh_token:
                self.access_token = access_token
                self.refresh_token = refresh_token
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Environment secrets remain the safe fallback.
            return

    def _save_state(self):
        if not self.state_file:
            return
        directory = os.path.dirname(self.state_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = self.state_file + ".tmp"
        payload = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "updated_at": time.time(),
        }
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.state_file)

    def _validate(self):
        if not self.access_token:
            return False
        response = self.session.get(
            VALIDATE_URL,
            headers={"Authorization": f"OAuth {self.access_token}"},
            timeout=20,
        )
        if response.status_code == 401:
            return False
        response.raise_for_status()
        payload = response.json()
        login = str(payload.get("login") or "").lower()
        scopes = set(payload.get("scopes") or [])
        if self.expected_login and login != self.expected_login:
            raise RuntimeError(
                "O token Twitch pertence a uma conta diferente de "
                "TWITCH_BOT_USERNAME."
            )
        missing = REQUIRED_SCOPES - scopes
        if missing:
            raise RuntimeError(
                "O token Twitch não contém as permissões necessárias: "
                + ", ".join(sorted(missing))
            )
        self.last_validated = time.monotonic()
        return True

    def _refresh(self):
        if not self.refresh_configured:
            raise RuntimeError(
                "O token Twitch expirou e a renovação automática não está "
                "completa. Configura TWITCH_REFRESH_TOKEN, TWITCH_CLIENT_ID "
                "e TWITCH_CLIENT_SECRET."
            )
        response = self.session.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if not access_token or not refresh_token:
            raise RuntimeError(
                "A Twitch não devolveu o novo par de tokens."
            )
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._save_state()
        if not self._validate():
            raise RuntimeError(
                "O novo token Twitch não passou na validação."
            )
        print("Token Twitch renovado e validado automaticamente.")
        return self.access_token

    def get_access_token(self, force_validate=False):
        with self._lock:
            validation_due = (
                force_validate
                or not self.last_validated
                or time.monotonic() - self.last_validated
                >= VALIDATION_INTERVAL_SECONDS
            )
            if not validation_due:
                return self.access_token
            if self._validate():
                return self.access_token
            return self._refresh()


token_manager = TwitchTokenManager()
