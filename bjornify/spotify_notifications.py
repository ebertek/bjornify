"""Notify the Spotify application's owner when reauthorization is required.

Ordinary access-token expiry is handled silently by Spotipy. These alerts only
apply to missing, revoked, or expired refresh authorization. Never log tokens.
"""

import asyncio
import logging
import os
from pathlib import Path

import discord
import requests
from spotify_auth import SpotifyAuthorizationRequired
from spotipy.exceptions import SpotifyOauthError

_LOGGER = logging.getLogger(__name__)
ALERT_MARKER = Path("/app/secrets/.spotify-reauthorization-notified")
CHECK_INTERVAL_SECONDS = 12 * 60 * 60
ALERT_TEXT = (
    "🔑 **Björnify needs Spotify reauthorization.**\n"
    "Spotify's refresh authorization is missing, expired, or revoked. "
    "In your Discord server, run **/spotify connect** and approve the "
    "new authorization link.\n"
    "This notice is sent only once until Spotify is connected again."
)


class SpotifyAuthNotifier:
    """Send at most one persistent owner alert per disconnected period."""

    def __init__(self, bot, auth_manager, *, marker_path=ALERT_MARKER, channel_id=None):
        self.bot = bot
        self.auth_manager = auth_manager
        self.marker_path = Path(marker_path)
        self.channel_id = channel_id
        self._lock = asyncio.Lock()
        self._loop = None

    def start(self):
        """Start monitoring from the bot's already-running Discord event loop."""
        self._loop = asyncio.get_running_loop()
        return asyncio.create_task(self.monitor())

    def schedule_alert(self):
        """Request an alert safely, even from a synchronous command or HTTP thread."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: loop.create_task(self.send_alert()))

    def clear_alert(self):
        """Allow another alert after the owner successfully reconnects Spotify."""
        self.marker_path.unlink(missing_ok=True)

    async def monitor(self):
        """Check refresh authorization periodically without requiring playback."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self.check_authorization()
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def check_authorization(self):
        """Refresh only expired access tokens; alert on missing/invalid grants."""
        try:
            await asyncio.to_thread(self._check_cached_authorization)
        except SpotifyAuthorizationRequired:
            await self.send_alert()
        except (SpotifyOauthError, requests.RequestException, OSError) as error:
            # Do not mistake a temporary Spotify outage for expired authorization.
            _LOGGER.warning(
                "Could not verify Spotify authorization (%s); will retry",
                type(error).__name__,
            )
        else:
            self.clear_alert()

    def _check_cached_authorization(self):
        """Read the shared Spotipy cache and validate access when needed."""
        token = self.auth_manager.cache_handler.get_cached_token()
        if not token or not token.get("refresh_token"):
            raise SpotifyAuthorizationRequired("Spotify needs to be reconnected")
        if self.auth_manager.is_token_expired(token):
            self.auth_manager.refresh_access_token(token["refresh_token"])

    async def send_alert(self):
        """DM the app owner, with the configured command channel as fallback."""
        async with self._lock:
            if self.marker_path.exists():
                return
            try:
                await self._send_to_discord()
            except (
                discord.DiscordException,
                OSError,
                ValueError,
                AttributeError,
            ) as error:
                _LOGGER.warning(
                    "Unable to deliver Spotify reauthorization notice (%s)",
                    type(error).__name__,
                )
                return
            self.marker_path.touch(mode=0o600)
            _LOGGER.info("Spotify reauthorization notice delivered to Discord owner")

    async def _send_to_discord(self):
        """Resolve the Discord application owner, never a random song requester."""
        override = os.getenv("SPOTIFY_NOTIFY_USER_ID")
        if override:
            owner_id = int(override)
        else:
            try:
                info = await self.bot.application_info()
            except discord.DiscordException:
                _LOGGER.warning(
                    "Spotify notification owner lookup failed; trying channel"
                )
                owner_id = None
            else:
                owner_id = getattr(info.owner, "id", None)

        if owner_id is not None:
            try:
                owner = self.bot.get_user(owner_id) or await self.bot.fetch_user(
                    owner_id
                )
                await owner.send(ALERT_TEXT)
                return
            except (discord.Forbidden, discord.HTTPException):
                _LOGGER.warning("Spotify notice DM unavailable; trying command channel")

        if not self.channel_id:
            raise ValueError("No Spotify notification destination is configured")
        channel_id = int(self.channel_id)
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(
            channel_id
        )
        mention = f"<@{owner_id}> " if owner_id else ""
        await channel.send(
            mention + ALERT_TEXT,
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False
            ),
        )
