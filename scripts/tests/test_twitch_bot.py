import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import twitch_bot


class TwitchBotTests(unittest.TestCase):
    def test_parse_privmsg_with_tags(self):
        line = (
            "@badge-info=;badges=;display-name=Luis;id=abc "
            ":luis!luis@luis.tmi.twitch.tv PRIVMSG #politometro :@politometro teste"
        )

        message = twitch_bot._parse_privmsg(line)

        self.assertEqual(message["user"], "luis")
        self.assertEqual(message["display_name"], "Luis")
        self.assertEqual(message["channel"], "politometro")
        self.assertEqual(message["text"], "@politometro teste")

    def test_command_does_not_trigger_question(self):
        self.assertEqual(
            twitch_bot._extract_question("!perguntar Qual e a proposta do PS?"),
            "",
        )

    def test_extract_question_from_mention(self):
        with mock.patch.object(twitch_bot, "TWITCH_BOT_USERNAME", "politometro"):
            with mock.patch.object(twitch_bot, "TWITCH_MENTION_ENABLED", True):
                self.assertEqual(
                    twitch_bot._extract_question("@politometro compara PSD e PS"),
                    "compara PSD e PS",
                )

    def test_ignores_unaddressed_message(self):
        self.assertEqual(twitch_bot._extract_question("boa noite chat"), "")

    def test_status_page_never_exposes_tokens(self):
        with (
            mock.patch.object(
                twitch_bot, "TWITCH_OAUTH_TOKEN", "access-secret"
            ),
            mock.patch.dict(
                "os.environ",
                {
                    "TWITCH_REFRESH_TOKEN": "refresh-secret",
                    "TWITCH_CLIENT_SECRET": "client-secret",
                },
                clear=False,
            ),
        ):
            twitch_bot._set_twitch_status(
                "error",
                "Falha de teste.",
                (
                    "access-secret refresh-secret client-secret "
                    "não foram aceites"
                ),
            )
        rendered = twitch_bot.twitch_status_markdown()
        self.assertNotIn("access-secret", rendered)
        self.assertNotIn("refresh-secret", rendered)
        self.assertNotIn("client-secret", rendered)
        self.assertIn("***", rendered)

    def test_long_utf8_answer_becomes_one_message_within_byte_limit(self):
        answer = (
            "A política de saúde pública prevê ação próxima e coordenação. "
            * 20
        )
        rendered = twitch_bot.format_twitch_response(answer, "Luís")
        self.assertLessEqual(
            len(rendered.encode("utf-8")),
            twitch_bot.TWITCH_RESPONSE_LIMIT,
        )
        self.assertTrue(rendered.startswith("@Luís "))
        self.assertNotIn("http", rendered)
        self.assertNotIn("\n", rendered)

    def test_short_answer_is_not_split_or_given_an_unneeded_link(self):
        rendered = twitch_bot.format_twitch_response(
            "O programa propõe reforçar o SNS.", "Luis"
        )
        self.assertEqual(
            rendered, "@Luis O programa propõe reforçar o SNS."
        )
        self.assertNotIn("politometro.vercel.app", rendered)


if __name__ == "__main__":
    unittest.main()
