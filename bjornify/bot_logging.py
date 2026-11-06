"""Shared JSON logging and narrowly scoped Discord voice-notice filtering."""

import json
import logging
from datetime import UTC, datetime

_UNUSED_VOICE_NOTICES = frozenset(
    {
        "PyNaCl is not installed, voice will NOT be supported",
        "davey is not installed, voice will NOT be supported",
    }
)


class JsonFormatter(logging.Formatter):
    """Format structured logs with readable UTF-8 characters."""

    def __init__(self, *, debug_enabled=False):
        super().__init__()
        self.debug_enabled = debug_enabled

    def format(self, record: logging.LogRecord) -> str:
        """Build an unchanged JSON schema without escaping Unicode letters."""
        event = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .astimezone()
            .isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if self.debug_enabled:
            event["module"] = record.module
            event["func"] = record.funcName
            event["line"] = str(record.lineno)
        return json.dumps(event, ensure_ascii=False)


class UnusedDiscordVoiceNoticeFilter(
    logging.Filter
):  # pylint: disable=too-few-public-methods
    """Drop only missing optional voice dependencies, not other warnings."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Preserve all other Discord messages, including gateway warnings."""
        return not (
            record.name == "discord.client"
            and record.levelno == logging.WARNING
            and record.getMessage() in _UNUSED_VOICE_NOTICES
        )


def suppress_unused_discord_voice_notices():
    """Register one filter on discord.client in each bot process."""
    client_logger = logging.getLogger("discord.client")
    if not any(
        isinstance(item, UnusedDiscordVoiceNoticeFilter)
        for item in client_logger.filters
    ):
        client_logger.addFilter(UnusedDiscordVoiceNoticeFilter())


# These are routine Discord library lifecycle messages, not app-level events.
_ROUTINE_DISCORD_INFO = {
    "discord.client": ("logging in using static token",),
    "discord.gateway": (
        " has connected to Gateway ",
        " has successfully RESUMED session ",
    ),
}


class RoutineDiscordInfoFilter(
    logging.Filter
):  # pylint: disable=too-few-public-methods
    """Downgrade a few routine Discord INFO messages to DEBUG, or omit them."""

    def __init__(self, *, debug_enabled=False):
        super().__init__()
        self.debug_enabled = debug_enabled

    def filter(self, record: logging.LogRecord) -> bool:
        """Leave all unrelated messages and all WARNING/ERROR events intact."""
        if record.levelno != logging.INFO:
            return True
        message = record.getMessage()
        if record.name == "discord.client":
            routine = message in _ROUTINE_DISCORD_INFO["discord.client"]
        elif record.name == "discord.gateway":
            routine = message.startswith("Shard ID ") and any(
                phrase in message for phrase in _ROUTINE_DISCORD_INFO["discord.gateway"]
            )
        else:
            routine = False
        if not routine:
            return True
        if not self.debug_enabled:
            return False
        record.levelno = logging.DEBUG
        record.levelname = "DEBUG"
        return True


def filter_routine_discord_info(*, debug_enabled=False):
    """Configure both Discord loggers without installing duplicate filters."""
    for name in ("discord.client", "discord.gateway"):
        logger = logging.getLogger(name)
        existing = next(
            (
                item
                for item in logger.filters
                if isinstance(item, RoutineDiscordInfoFilter)
            ),
            None,
        )
        if existing is not None:
            existing.debug_enabled = debug_enabled
        else:
            logger.addFilter(RoutineDiscordInfoFilter(debug_enabled=debug_enabled))
