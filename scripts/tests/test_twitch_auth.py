import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import twitch_auth


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, validations, refresh=None):
        self.validations = list(validations)
        self.refresh = refresh
        self.posts = []

    def get(self, *args, **kwargs):
        return self.validations.pop(0)

    def post(self, *args, **kwargs):
        self.posts.append(kwargs)
        return self.refresh


class TwitchTokenManagerTests(unittest.TestCase):
    def environment(self):
        return mock.patch.dict(
            os.environ,
            {
                "TWITCH_OAUTH_TOKEN": "expired-access",
                "TWITCH_REFRESH_TOKEN": "initial-refresh",
                "TWITCH_CLIENT_ID": "client-id",
                "TWITCH_CLIENT_SECRET": "client-secret",
                "TWITCH_BOT_USERNAME": "politometro_bot",
            },
            clear=False,
        )

    def test_valid_token_is_reused(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "login": "politometro_bot",
                        "scopes": ["chat:read", "chat:edit"],
                    },
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temporary, self.environment():
            manager = twitch_auth.TwitchTokenManager(
                session=session,
                state_file=str(Path(temporary) / "tokens.json"),
            )
            self.assertEqual(manager.get_access_token(), "expired-access")
            self.assertEqual(session.posts, [])

    def test_expired_token_is_refreshed_and_rotated_pair_is_saved(self):
        session = FakeSession(
            [
                FakeResponse(401, {"status": 401}),
                FakeResponse(
                    200,
                    {
                        "login": "politometro_bot",
                        "scopes": ["chat:read", "chat:edit"],
                    },
                ),
            ],
            refresh=FakeResponse(
                200,
                {
                    "access_token": "new-access",
                    "refresh_token": "rotated-refresh",
                },
            ),
        )
        with tempfile.TemporaryDirectory() as temporary, self.environment():
            state_file = Path(temporary) / "tokens.json"
            manager = twitch_auth.TwitchTokenManager(
                session=session, state_file=str(state_file)
            )
            self.assertEqual(manager.get_access_token(), "new-access")
            request = session.posts[0]["data"]
            self.assertEqual(request["refresh_token"], "initial-refresh")
            self.assertEqual(request["client_id"], "client-id")
            stored = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(stored["access_token"], "new-access")
            self.assertEqual(
                stored["refresh_token"], "rotated-refresh"
            )

    def test_wrong_account_is_rejected(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "login": "outra_conta",
                        "scopes": ["chat:read", "chat:edit"],
                    },
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temporary, self.environment():
            manager = twitch_auth.TwitchTokenManager(
                session=session,
                state_file=str(Path(temporary) / "tokens.json"),
            )
            with self.assertRaisesRegex(
                RuntimeError, "conta diferente"
            ):
                manager.get_access_token()


if __name__ == "__main__":
    unittest.main()
