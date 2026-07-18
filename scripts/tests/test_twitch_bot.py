import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import twitch_bot


class TwitchBotTests(unittest.TestCase):
    def setUp(self):
        twitch_bot._last_user_response.clear()
        twitch_bot._last_channel_response.clear()
        twitch_bot._busy_notice_pending.clear()
        twitch_bot._user_notice_last.clear()
        twitch_bot._channel_slow_seconds.clear()
        twitch_bot._channel_last_sent.clear()
        twitch_bot._channel_last_attempt.clear()
        twitch_bot._recent_chat_sends.clear()
        twitch_bot._channel_waiting_for_send.clear()
        twitch_bot._channel_pending_questions.clear()
        twitch_bot._queue_full_notice_pending.clear()
        twitch_bot._seen_source_messages.clear()

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

    def test_shared_chat_duplicate_is_processed_only_once(self):
        original = twitch_bot._parse_privmsg(
            "@display-name=Luis;id=orig;room-id=100;"
            "source-id=orig;source-room-id=100 "
            ":luis!luis@luis.tmi.twitch.tv PRIVMSG #canal_a "
            ":@politometro pergunta"
        )
        duplicate = twitch_bot._parse_privmsg(
            "@display-name=Luis;id=copia;room-id=200;"
            "source-id=orig;source-room-id=100 "
            ":luis!luis@luis.tmi.twitch.tv PRIVMSG #canal_b "
            ":@politometro pergunta"
        )

        self.assertEqual(original["source_room_id"], "100")
        self.assertEqual(duplicate["source_room_id"], "100")
        self.assertTrue(twitch_bot._claim_source_message(original))
        self.assertFalse(twitch_bot._claim_source_message(duplicate))

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

    def test_parse_twitch_rejection_notice(self):
        notice = twitch_bot._parse_notice(
            "@msg-id=msg_requires_verified_phone_number "
            ":tmi.twitch.tv NOTICE #canal :É necessário verificar o telefone."
        )

        self.assertEqual(
            notice["id"], "msg_requires_verified_phone_number"
        )
        self.assertIn("telefone", notice["text"])

    def test_parse_roomstate_slow_mode(self):
        state = twitch_bot._parse_roomstate(
            "@emote-only=0;slow=12;subs-only=0 "
            ":tmi.twitch.tv ROOMSTATE #canal"
        )

        self.assertEqual(
            state, {"channel": "canal", "slow_seconds": 12}
        )

    def test_question_cooldown_is_independent_per_channel(self):
        with (
            mock.patch.object(
                twitch_bot, "TWITCH_USER_COOLDOWN_SECONDS", 30
            ),
            mock.patch.object(
                twitch_bot, "TWITCH_GLOBAL_COOLDOWN_SECONDS", 1
            ),
        ):
            self.assertEqual(
                twitch_bot._cooldown_status("pessoa", "canal_a")[0],
                "accepted",
            )
            self.assertEqual(
                twitch_bot._cooldown_status("outra", "canal_a")[0],
                "busy",
            )
            self.assertEqual(
                twitch_bot._cooldown_status("pessoa", "canal_b")[0],
                "accepted",
            )

    def test_busy_question_uses_static_message_without_chatbot(self):
        twitch_bot._last_channel_response["canal"] = (
            twitch_bot.time.monotonic()
        )
        with (
            mock.patch.object(
                twitch_bot, "TWITCH_GLOBAL_COOLDOWN_SECONDS", 1
            ),
            mock.patch.object(
                twitch_bot, "query_politometro_chat"
            ) as query,
            mock.patch.object(twitch_bot, "_send_message") as send,
            mock.patch.object(
                twitch_bot.threading, "Thread"
            ) as thread,
        ):
            twitch_bot._handle_incoming_question(
                mock.Mock(),
                {
                    "channel": "canal",
                    "display_name": "Luis",
                    "user": "luis",
                },
                "pergunta",
            )
            target = thread.call_args.kwargs["target"]
            args = thread.call_args.kwargs["args"]
            target(*args)

        query.assert_not_called()
        self.assertIn("várias perguntas", send.call_args.args[2])
        self.assertIn("@Luis", send.call_args.args[2])

    def test_slow_mode_accepts_three_queued_questions(self):
        self.assertTrue(twitch_bot._reserve_question_slot("canal"))
        self.assertTrue(twitch_bot._reserve_question_slot("canal"))
        self.assertTrue(twitch_bot._reserve_question_slot("canal"))
        self.assertTrue(twitch_bot._reserve_question_slot("canal"))
        self.assertFalse(twitch_bot._reserve_question_slot("canal"))

    def test_slow_mode_bypasses_only_channel_cooldown(self):
        now = twitch_bot.time.monotonic()
        twitch_bot._last_channel_response["canal"] = now
        twitch_bot._channel_slow_seconds["canal"] = 10
        twitch_bot._channel_waiting_for_send.add("canal")

        with mock.patch.object(
            twitch_bot, "TWITCH_GLOBAL_COOLDOWN_SECONDS", 1
        ):
            state, _ = twitch_bot._cooldown_status("pessoa", "canal")

        self.assertEqual(state, "accepted")

    def test_slow_mode_waits_before_attempting_to_send(self):
        twitch_bot._channel_slow_seconds["canal"] = 10
        twitch_bot._channel_last_sent["canal"] = 100
        sock = mock.Mock()

        with (
            mock.patch.object(
                twitch_bot.time,
                "monotonic",
                side_effect=[105, 110, 110],
            ),
            mock.patch.object(twitch_bot.time, "sleep") as sleep,
            mock.patch.object(twitch_bot, "_send") as send,
        ):
            twitch_bot._send_message(sock, "canal", "Resposta")

        sleep.assert_called_once_with(5)
        send.assert_called_once_with(
            sock, "PRIVMSG #canal :Resposta"
        )

    def test_slow_mode_in_one_channel_does_not_delay_another(self):
        twitch_bot._channel_slow_seconds["canal_a"] = 30
        twitch_bot._channel_last_sent["canal_a"] = time_value = 100
        sock = mock.Mock()

        with (
            mock.patch.object(
                twitch_bot.time,
                "monotonic",
                side_effect=[time_value, time_value, time_value],
            ),
            mock.patch.object(twitch_bot.time, "sleep") as sleep,
            mock.patch.object(twitch_bot, "_send") as send,
        ):
            twitch_bot._send_message(sock, "canal_b", "Resposta")

        sleep.assert_not_called()
        send.assert_called_once_with(
            sock, "PRIVMSG #canal_b :Resposta"
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
