"""Focused tests for readable JSON logs and optional Discord voice notices."""

import importlib
import json
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bjornify"))
bot_logging = importlib.import_module("bot_logging")


class BotLoggingTests(unittest.TestCase):
    """Confirm logging remains readable without hiding useful diagnostics."""

    def make_record(self, message, *, logger="bjornify", level=logging.INFO):
        """Create a log record as emitted by the running bot."""
        return logging.LogRecord(logger, level, __file__, 1, message, (), None)

    def test_json_preserves_bjornify_unicode(self):
        """Do not replace Swedish characters with backslash-u JSON escapes."""
        formatter = bot_logging.JsonFormatter()
        line = formatter.format(self.make_record("Björnify version: 1.1.123"))
        self.assertIn("Björnify", line)
        self.assertNotIn(r"\u00f6", line)
        parsed = json.loads(line)
        self.assertEqual(parsed["message"], "Björnify version: 1.1.123")
        self.assertEqual(parsed["logger"], "bjornify")
        self.assertEqual(parsed["level"], "INFO")

    def test_json_debug_fields_preserved(self):
        """JSON DEBUG mode still records the source module/function/line."""
        formatter = bot_logging.JsonFormatter(debug_enabled=True)
        parsed = json.loads(formatter.format(self.make_record("Björngrottan")))
        self.assertEqual(parsed["message"], "Björngrottan")
        self.assertIn("module", parsed)
        self.assertIn("func", parsed)
        self.assertIn("line", parsed)

    def test_both_optional_voice_warnings_are_suppressed(self):
        """The two known missing voice dependencies are irrelevant here."""
        filt = bot_logging.UnusedDiscordVoiceNoticeFilter()
        for name in ("PyNaCl", "davey"):
            record = self.make_record(
                f"{name} is not installed, voice will NOT be supported",
                logger="discord.client",
                level=logging.WARNING,
            )
            with self.subTest(name=name):
                self.assertFalse(filt.filter(record))

    def test_other_discord_warnings_are_preserved(self):
        """Do not hide real connection, login or gateway diagnostics."""
        filt = bot_logging.UnusedDiscordVoiceNoticeFilter()
        for logger, level, msg in (
            ("discord.client", logging.WARNING, "Discord connection interrupted"),
            ("discord.client", logging.ERROR, "Failed to log in"),
            ("discord.gateway", logging.WARNING, "Gateway reconnecting"),
            (
                "bjornify",
                logging.WARNING,
                "PyNaCl is not installed, voice will NOT be supported",
            ),
        ):
            with self.subTest(message=msg):
                self.assertTrue(
                    filt.filter(self.make_record(msg, logger=logger, level=level))
                )

    def test_routine_discord_info_is_hidden_by_default(self):
        """Routine login and gateway lifecycle messages do not clutter INFO."""
        filt = bot_logging.RoutineDiscordInfoFilter()
        messages = (
            ("discord.client", "logging in using static token"),
            (
                "discord.gateway",
                "Shard ID None has connected to Gateway (Session ID: sample).",
            ),
            (
                "discord.gateway",
                "Shard ID None has successfully RESUMED session sample.",
            ),
            ("discord.gateway", "Shard ID 0 has connected to Gateway (Session ID: sample)."),
        )
        for logger, message in messages:
            with self.subTest(logger=logger, message=message):
                record = self.make_record(message, logger=logger)
                self.assertFalse(filt.filter(record))

    def test_routine_discord_info_becomes_debug_when_enabled(self):
        """Routine Discord messages are genuinely reclassified as DEBUG."""
        filt = bot_logging.RoutineDiscordInfoFilter(debug_enabled=True)
        record = self.make_record(
            "Shard ID None has connected to Gateway (Session ID: sample).",
            logger="discord.gateway",
        )
        self.assertTrue(filt.filter(record))
        self.assertEqual(record.levelno, logging.DEBUG)
        self.assertEqual(record.levelname, "DEBUG")
        parsed = json.loads(bot_logging.JsonFormatter().format(record))
        self.assertEqual(parsed["level"], "DEBUG")

    def test_important_discord_messages_remain_visible(self):
        """Preserve unrelated INFO, all warnings, and errors for troubleshooting."""
        filt = bot_logging.RoutineDiscordInfoFilter()
        events = (
            ("discord.client", logging.INFO, "Discord user ready"),
            ("discord.gateway", logging.INFO, "Unexpected reconnect detail"),
            ("discord.gateway", logging.WARNING, "Shard ID None connection lost"),
            ("discord.gateway", logging.ERROR, "Gateway authentication failed"),
            ("discord.client", logging.WARNING, "logging in using static token"),
            ("bjornify", logging.INFO, "Shard ID None has connected to Gateway"),
        )
        for logger, level, message in events:
            with self.subTest(message=message):
                record = self.make_record(message, logger=logger, level=level)
                self.assertTrue(filt.filter(record))
                self.assertEqual(record.levelno, level)

    def test_routine_filter_installation_is_idempotent(self):
        """Multiple setup calls reuse and update the existing filter."""
        logger = logging.getLogger("discord.gateway")
        before = list(logger.filters)
        try:
            bot_logging.filter_routine_discord_info(debug_enabled=False)
            bot_logging.filter_routine_discord_info(debug_enabled=True)
            installed = [
                item
                for item in logger.filters
                if isinstance(item, bot_logging.RoutineDiscordInfoFilter)
            ]
            self.assertEqual(len(installed), 1)
            self.assertTrue(installed[0].debug_enabled)
        finally:
            for name in ("discord.client", "discord.gateway"):
                current_logger = logging.getLogger(name)
                for item in current_logger.filters[:]:
                    if isinstance(item, bot_logging.RoutineDiscordInfoFilter):
                        current_logger.removeFilter(item)
            for item in before:
                if item not in logger.filters:
                    logger.addFilter(item)

    def test_filter_installation_is_idempotent(self):
        """Both bot startup routines must not repeatedly add the same filter."""
        logger = logging.getLogger("discord.client")
        before = list(logger.filters)
        try:
            bot_logging.suppress_unused_discord_voice_notices()
            bot_logging.suppress_unused_discord_voice_notices()
            installed = [
                item
                for item in logger.filters
                if isinstance(item, bot_logging.UnusedDiscordVoiceNoticeFilter)
            ]
            self.assertEqual(len(installed), 1)
        finally:
            for item in logger.filters[:]:
                if item not in before:
                    logger.removeFilter(item)


if __name__ == "__main__":
    unittest.main()
