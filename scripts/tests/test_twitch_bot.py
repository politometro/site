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


if __name__ == "__main__":
    unittest.main()
