"""Owner-bound, nonblocking Spotify desktop device pairing for Discord.

The public Spotify device code appears only in an ephemeral Discord response.
The device secret, access token and refresh token never leave the bot process.
"""

import asyncio
import importlib
import logging
import time

_LOGGER = logging.getLogger(__name__)


class PairingInProgress(RuntimeError):
    """A Discord pairing is already waiting for Spotify approval."""


class DiscordPlaybackPairings:
    """Run at most one Connect pairing session inside a Discord bot process."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._task = None

    @property
    def pending(self):
        """Report only whether a pairing is running, never pairing credentials."""
        return self._task is not None and not self._task.done()

    async def begin(self, on_complete):
        """Return the user-facing challenge and start background OAuth polling.

        The caller must verify bot ownership before calling this function.
        on_complete is an async callable accepting (success, safe_message).
        """
        async with self._lock:
            if self.pending:
                raise PairingInProgress("A playback pairing is already in progress.")
            helper = importlib.import_module("connect_playback_auth")
            challenge = await asyncio.to_thread(helper.begin_pairing)
            self._task = asyncio.create_task(
                self._monitor(helper, challenge, on_complete)
            )
            return {
                "user_code": challenge["user_code"],
                "expires_in": challenge["expires_in"],
            }

    async def _monitor(self, helper, challenge, on_complete):
        interval = challenge["interval"]
        deadline = time.monotonic() + challenge["expires_in"]
        success = False
        message = (
            "Spotify Connect pairing expired or was declined. "
            "Retry /spotify connect-playback."
        )
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))
                if time.monotonic() >= deadline:
                    break
                reply = await asyncio.to_thread(
                    helper.poll_pairing, challenge["device_code"]
                )
                error = reply.get("error")
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    interval = min(interval + 5, 60)
                    continue
                if error:
                    # Spotify may supply identifiers in error_description; never print it.
                    message = (
                        "Spotify Connect pairing was not approved. "
                        "Retry /spotify connect-playback."
                    )
                    break
                await asyncio.to_thread(helper.save_grant, reply)
                success = True
                message = (
                    "✅ Spotify Connect playback is connected. "
                    "Sonos queue fallback is available."
                )
                _LOGGER.info("Spotify Connect playback pairing completed")
                break
        except (OSError, ValueError, TypeError, KeyError) as error:
            _LOGGER.warning("Spotify Connect pairing failed (%s)", type(error).__name__)
            message = "Spotify Connect pairing failed. Retry /spotify connect-playback."
        except Exception as error:  # pylint: disable=broad-exception-caught
            _LOGGER.error(
                "Unexpected Spotify Connect pairing failure (%s)", type(error).__name__
            )
            message = "Spotify Connect pairing failed. Check bot logs and retry."
        finally:
            try:
                await on_complete(success, message)
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.warning("Could not update the private Spotify pairing message")


def playback_grant_status():
    """Read cached grant metadata without refreshing or revealing credentials."""
    helper = importlib.import_module("connect_playback_auth")
    try:
        saved = helper.read_grant()
    except (ValueError, TypeError, OSError, KeyError):
        return "Not connected. Use /spotify connect-playback."
    if (
        not isinstance(saved.get("refresh_token"), str)
        or "streaming" not in str(saved.get("scope", "")).split()
    ):
        return "Playback grant is incomplete. Use /spotify connect-playback."
    return "Connected (playback grant saved; refreshes automatically)."
