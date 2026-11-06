#!/usr/bin/python
"""Load Björnify Discord.py bot"""

# pylint: disable=duplicate-code

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress

import discord
import soco
import spotipy
from bot_logging import (
    JsonFormatter,
    filter_routine_discord_info,
    suppress_unused_discord_voice_notices,
)
from discord import app_commands
from discord.ext import commands
from spotify_auth import (
    SpotifyAuthorizationRequired,
    make_auth_manager,
)
from spotify_discord_auth import SpotifyAuthorizations, start_spotify_callback
from spotify_devices import select_fallback_device
from spotify_notifications import SpotifyAuthNotifier
from spotipy.exceptions import SpotifyOauthError

try:
    from version import __version__
except ImportError:
    __version__ = "dev"  # fallback for local dev without version.py

# Define valid log levels
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Get and validate configured log levels
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in VALID_LOG_LEVELS:
    LOG_LEVEL = "INFO"  # fallback to default
LIB_LOG_LEVEL = os.getenv("LIB_LOG_LEVEL", "ERROR").upper()
if LIB_LOG_LEVEL not in VALID_LOG_LEVELS:
    LIB_LOG_LEVEL = "ERROR"  # fallback to default
DEBUG_ENABLED = LOG_LEVEL == "DEBUG" or LIB_LOG_LEVEL == "DEBUG"

# Get and validate configured log output
LOG_OUTPUT_RAW = os.getenv("LOG_OUTPUT", "console")
if LOG_OUTPUT_RAW.strip() == "":
    LOG_OUTPUT = set()
else:
    LOG_OUTPUT = {
        entry.strip().lower() for entry in LOG_OUTPUT_RAW.split(",") if entry.strip()
    }

LOG_PATH = "logs/bjornify.log"

# Make sure log folder exists
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


# Common formatters
json_formatter: logging.Formatter = JsonFormatter(debug_enabled=DEBUG_ENABLED)

plain_formatter = logging.Formatter(
    "%(asctime)s.%(msecs)03d | %(levelname)-8s | bjornify | %(name)-30s | %(message)s",
    "%Y-%m-%d %H:%M:%S",
)

LOG_FORMAT = os.getenv("LOG_FORMAT", "plain").strip().lower()

log_formatter: logging.Formatter
if LOG_FORMAT == "json":
    log_formatter = json_formatter
else:
    log_formatter = plain_formatter

# Create and configure file handler
LOG_HANDLERS: list[logging.Handler] = []

if "file" in LOG_OUTPUT:
    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    file_handler.setFormatter(log_formatter)
    LOG_HANDLERS.append(file_handler)

# Create and configure console (stdout) handler
if "console" in LOG_OUTPUT:
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(log_formatter)
    LOG_HANDLERS.append(console_handler)

# Apply log level to root logger
root_logger = logging.getLogger()
if LOG_HANDLERS:
    root_logger.setLevel(LOG_LEVEL)
    for handler in LOG_HANDLERS:
        root_logger.addHandler(handler)
else:
    root_logger.handlers.clear()
    logging.disable(logging.CRITICAL)

# Create app-specific logger
_LOGGER = logging.getLogger("bjornify")
_LOGGER.setLevel(LOG_LEVEL)
_LOGGER.propagate = True  # Let messages bubble up to root

# Apply lib log level to third-party modules
for lib in ("asyncio", "discord", "soco", "spotipy", "urllib3"):
    logging.getLogger(lib).setLevel(LIB_LOG_LEVEL)

# These optional modules are irrelevant because neither bot uses voice.
suppress_unused_discord_voice_notices()
# Keep only unusual Discord events at INFO; routine login/connect events are DEBUG.
filter_routine_discord_info(debug_enabled=DEBUG_ENABLED)

# Validate that all required environmental variables are set
required_env_vars = [
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "DISCORD_BOT_TOKEN",
    "CHANNEL_ID",
]

for var in required_env_vars:
    if not os.getenv(var):
        raise OSError(f"Missing required environment variable: {var}")

# Load environmental variables
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
DEFAULT_DEVICE = os.getenv("DEFAULT_DEVICE", "Everywhere")

# Share one OAuth cache with the owner's interactive authorization command.
# Never attempt to prompt for authorization inside the Discord bot.
auth_manager = make_auth_manager()
# Let 429 responses reach us intact: Spotipy's default retry loop hides the
# QUOTA_EXCEEDED response body that Spotify uses for Development Mode limits.
spotify = spotipy.Spotify(
    auth_manager=auth_manager,
    status_forcelist=(500, 502, 503, 504),
)


spotify_authorizations = SpotifyAuthorizations()


# Set up Discord
class BjornifyBot(commands.Bot):  # pylint: disable=too-few-public-methods
    """Custom bot class."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.spotify_callback_server = None
        self.spotify_monitor_task = None

    async def setup_hook(self):
        """Start the Spotify callback and sync slash commands to the guild."""
        self.spotify_callback_server = start_spotify_callback(spotify_authorizations)
        self.spotify_monitor_task = spotify_notifier.start()
        guild_id_str = os.getenv("GUILD_ID")
        if guild_id_str:
            try:
                guild = discord.Object(id=int(guild_id_str))
                for cmd in self.tree.get_commands(guild=guild):
                    _LOGGER.debug("Registered slash command before sync: %s", cmd.name)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                _LOGGER.debug(
                    "Synced %d slash commands to guild ID %s", len(synced), guild_id_str
                )
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to sync commands to guild %s", guild_id_str)
        else:
            _LOGGER.debug("GUILD_ID not set — slash commands will not be registered")


intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True

bot = BjornifyBot(
    command_prefix="!",
    description="Björnify adds requested tracks to Björngrottan's Spotify playback queue",
    intents=intents,
)
spotify_notifier = SpotifyAuthNotifier(bot, auth_manager, channel_id=CHANNEL_ID)


spotify_group = app_commands.Group(
    name="spotify", description="Manage the Spotify account"
)


@spotify_group.command(name="connect", description="Connect Spotify (bot owner only)")
async def spotify_connect(interaction):
    """Start a private, short-lived OAuth flow using an HTTPS callback."""
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message(
            "Only the bot owner can connect Spotify.", ephemeral=True
        )
        return

    if not auth_manager.redirect_uri.startswith("https://"):
        await interaction.response.send_message(
            "Automatic Spotify login requires an HTTPS callback URL. "
            "Configure SPOTIPY_REDIRECT_URI and the reverse proxy first.",
            ephemeral=True,
        )
        return

    loop = asyncio.get_running_loop()

    def on_complete(connected):
        """Update the original private Discord message from the HTTP thread."""
        if connected:
            spotify_notifier.clear_alert()
        content = (
            "✅ Spotify connected! You can now use Björnify."
            if connected
            else "❌ Spotify authorization failed. Use /spotify connect to retry."
        )
        future = asyncio.run_coroutine_threadsafe(
            interaction.edit_original_response(content=content, view=None),
            loop,
        )

        def report_edit_result(done):
            try:
                done.result()
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Could not update private Spotify connect message")

        future.add_done_callback(report_edit_result)

    try:
        authorize_url = spotify_authorizations.begin(
            interaction.user.id, on_complete=on_complete
        )
    except (OSError, ValueError):
        _LOGGER.exception("Unable to start Spotify connection")
        await interaction.response.send_message(
            "Spotify connection setup failed. Check the bot logs.",
            ephemeral=True,
        )
        return

    view = discord.ui.View(timeout=600)
    view.add_item(
        discord.ui.Button(
            label="Connect Spotify",
            style=discord.ButtonStyle.link,
            url=authorize_url,
        )
    )
    await interaction.response.send_message(
        "Open Spotify to authorize Björnify. After approval, "
        "the callback page will confirm the connection. "
        "This link expires in 10 minutes.",
        view=view,
        ephemeral=True,
    )


@spotify_group.command(name="status", description="Check Spotify login (owner only)")
async def spotify_status(interaction):
    """Show only the authorization cache status, never secret tokens."""
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message(
            "Only the bot owner can check Spotify.", ephemeral=True
        )
        return

    cached = auth_manager.cache_handler.get_cached_token()
    status = (
        "Spotify authorization is saved. Try a playback command to verify it."
        if cached and cached.get("refresh_token")
        else "Spotify is not connected. Run /spotify connect."
    )
    await interaction.response.send_message(status, ephemeral=True)


bot.tree.add_command(spotify_group)


def refresh_spotify_token(force: bool = False):
    """Refresh the cached Spotify token without replacing the Spotify client."""
    token_info = auth_manager.cache_handler.get_cached_token()
    if not token_info or not token_info.get("refresh_token"):
        raise SpotifyAuthorizationRequired(
            "Spotify authorization is missing; the bot owner must reconnect Spotify."
        )
    if force or auth_manager.is_token_expired(token_info):
        _LOGGER.info("Refreshing Spotify access token")
        # This updates the shared cache. Do NOT replace the client: commands
        # hold bound Spotify methods that must use the same auth manager.
        auth_manager.refresh_access_token(token_info["refresh_token"])


def spotify_request(method, *args, **kwargs):
    """Retry an API request once after a Spotify 401 authentication response."""
    try:
        return method(*args, **kwargs)
    except spotipy.exceptions.SpotifyException as error:
        if error.http_status != 401:
            raise
        _LOGGER.warning("Spotify returned 401; refreshing token and retrying once")
        refresh_spotify_token(force=True)
        return method(*args, **kwargs)


def spotify_error_message(error):  # pylint: disable=too-many-return-statements
    """Return a safe, actionable message without leaking tokens or API URLs."""
    if isinstance(error, SpotifyAuthorizationRequired):
        _LOGGER.warning("Spotify authorization required; notifying the bot owner")
        spotify_notifier.schedule_alert()
        return "Spotify needs to be reconnected by the bot owner."

    if isinstance(error, SpotifyOauthError):
        _LOGGER.warning("Spotify OAuth failure: %s", error.error or "unknown")
        return "Spotify authorization failed. Ask the bot owner to reconnect Spotify."

    if isinstance(error, spotipy.exceptions.SpotifyException):
        _LOGGER.warning(
            "Spotify API failed: HTTP %s, reason %s",
            error.http_status,
            error.reason or "unknown",
        )
        if error.http_status == 401:
            return "Spotify authorization was rejected. Ask the bot owner to reconnect."
        if error.http_status == 403:
            return (
                "Spotify denied access (403). Check Premium, Development Mode "
                "allowlisting and app permissions."
            )
        if error.http_status == 429:
            if error.reason == "QUOTA_EXCEEDED":
                return "Spotify Development Mode API quota exceeded. Try again later."
            retry_after = (error.headers or {}).get("Retry-After", "")
            if str(retry_after).isdecimal():
                return f"Spotify is rate-limited. Try again in {retry_after} seconds."
            return "Spotify is rate-limited. Try again shortly."

    _LOGGER.error("Spotify request failed: %s", type(error).__name__)
    return "Spotify is unavailable. Try again later."


def is_sonos_device_restriction(error):
    """Do not mistake Premium/allowlist failures for a device restriction."""
    if error.http_status != 403:
        return False
    reason = f"{error.reason or ''} {error.msg or ''}".lower()
    return "restricted device" in reason or "restriction violated" in reason


def find_playing_speaker():
    """Find and return the first Sonos speaker that is currently playing Spotify."""
    speakers = soco.discover()

    if not speakers:
        _LOGGER.info("No Sonos speakers found")
        return None

    for speaker in speakers:
        try:
            # Check if the speaker is playing
            state = speaker.get_current_transport_info()["current_transport_state"]
            if state != "PLAYING":
                continue
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Failed to get transport state for %s", speaker.player_name
            )
            continue

        try:
            # Check current track URI
            track_info = speaker.get_current_track_info()
            uri = track_info.get("uri", "")
            metadata = track_info.get("metadata", "")
            _LOGGER.debug("Checking speaker: %s | URI: %s", speaker.player_name, uri)

            # Primary: Explicit x-sonos-spotify
            if "x-sonos-spotify:" in uri:
                _LOGGER.debug(
                    "Speaker %s playing Spotify (via URI)", speaker.player_name
                )
                return speaker

            # Fallback: VirtualLineInSource pointing to Spotify
            if uri.startswith("x-sonos-vli:") and "spotify:" in uri:
                _LOGGER.debug(
                    "Speaker %s playing Spotify (via VirtualLineInSource)",
                    speaker.player_name,
                )
                return speaker

            # Optional fallback: metadata sniffing
            if "x-sonos-spotify:" in metadata:
                _LOGGER.debug(
                    "Speaker %s playing Spotify (via metadata)", speaker.player_name
                )
                return speaker

        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Error while checking speaker %s", speaker.player_name)

    _LOGGER.debug("No currently playing Sonos speaker using Spotify was found")
    return None


def spotify_action_with_soco_fallback(
    spotify_action, soco_action, action_name
):  # pylint: disable=too-many-return-statements
    """Use Spotify first, then Sonos only for a known device restriction."""
    try:
        playback = spotify_request(spotify.current_playback)
        if playback is None:
            return "No active Spotify playback found."
        spotify_request(spotify_action)
        _LOGGER.info("%s via Spotify", action_name)
        return "✅"
    except spotipy.exceptions.SpotifyException as error:
        if is_sonos_device_restriction(error):
            _LOGGER.info("Device restriction during %s; trying Sonos", action_name)
            try:
                speaker = find_playing_speaker()
                if speaker is not None:
                    soco_action(speaker)
                    _LOGGER.info("%s via Sonos", action_name)
                    return "✅"
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Sonos fallback failed during %s", action_name)
                return "The Sonos playback fallback failed."
            return (
                "Spotify restricted this device and no active Sonos player was found."
            )
        return spotify_error_message(error)
    except (SpotifyOauthError, SpotifyAuthorizationRequired) as error:
        return spotify_error_message(error)
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error during %s", action_name)
        return "Playback control failed unexpectedly."


@bot.event
async def on_ready():
    """Bot logged in"""
    _LOGGER.info("Logged in as %s", bot.user)


@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Sync slash commands to the current guild."""
    _LOGGER.debug("User %s (%s) issued !sync command", ctx.author.name, ctx.author.id)

    if not ctx.guild:
        await ctx.send("❌ This command must be used in a server.")
        return

    try:
        synced = await bot.tree.sync(guild=ctx.guild)
        _LOGGER.info(
            "Successfully synced %d slash commands to guild %s (%s)",
            len(synced),
            ctx.guild.name,
            ctx.guild.id,
        )
        await ctx.send(f"✅ Synced {len(synced)} slash commands to this guild.")
    except discord.HTTPException as e:
        _LOGGER.exception("Failed to sync slash commands")
        await ctx.send(f"❌ Failed to sync commands: {e}")
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error during sync")
        await ctx.send("❌ An unexpected error occurred during sync.")


@bot.command(name="add")
async def add_track(ctx, *, query: str):
    """Add a track to the Spotify playback queue."""
    _LOGGER.debug("!add command by %s: %s", ctx.author.name, query)
    response = player_add_item_to_playback_queue(query)
    await ctx.send(response)


@bot.command(name="pause", aliases=["stop"])
async def pause_track(ctx):
    """Pause the current Spotify playback."""
    _LOGGER.debug("!pause command by %s", ctx.author.name)
    response = await asyncio.get_running_loop().run_in_executor(
        None, player_pause_playback
    )
    await ctx.message.add_reaction("✅" if response == "✅" else "❌")
    if response != "✅":
        await ctx.send(f"❌ {response}")


@bot.command(name="next", aliases=["skip"])
async def skip_track(ctx):
    """Skip to the next Spotify track."""
    _LOGGER.debug("!next command by %s", ctx.author.name)
    response = await asyncio.get_running_loop().run_in_executor(
        None, player_skip_to_next
    )
    await ctx.message.add_reaction("✅" if response == "✅" else "❌")
    if response != "✅":
        await ctx.send(f"❌ {response}")


@bot.command(name="np", help="Show the currently playing track")
async def now_playing(ctx):
    """Show the currently playing track."""
    _LOGGER.debug("!np command by %s", ctx.author.name)
    embed = get_now_playing_embed()
    if embed:
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Nothing is playing.")


@bot.command(name="version")
async def version(ctx):
    """Return version number."""
    await ctx.send(f"Björnify version: {__version__}")


@bot.event
async def on_message(message):
    """New message"""
    await bot.process_commands(message)


def player_add_track(
    uri, artist=None, name=None
):  # pylint: disable=too-many-return-statements
    """Add the track to the playback queue."""
    if artist and name:
        _LOGGER.debug("Adding: %s - %s (%s)", artist, name, uri)
    else:
        _LOGGER.debug("Adding: %s", uri)

    try:
        # GET /me/player
        playback_results = spotify_request(spotify.current_playback)
        if playback_results is not None:
            was_paused = playback_results.get("is_playing") is False
            if was_paused:
                # No new URI/context: keep the paused song on this device.
                device = playback_results.get("device") or {}
                device_id = device.get("id")
                resume_kwargs = {"device_id": device_id} if device_id else {}
                spotify_request(spotify.start_playback, **resume_kwargs)
                _LOGGER.info("Resumed paused Spotify playback on its current device")

            # Queue on the same active session after requesting resume.
            spotify_request(spotify.add_to_queue, uri)
            queued = f"{artist} - {name}" if artist and name else uri
            _LOGGER.info("Queued: %s", queued)
            if was_paused:
                return f"Queued: {queued} (resumed paused playback)"
            return f"Queued: {queued}"

        # GET /me/player/devices
        devices = spotify_request(spotify.devices)
        target = select_fallback_device(devices, DEFAULT_DEVICE)
        if target:
            device_id = target["id"]
            _LOGGER.info("Starting on Spotify device: %s", target.get("name"))
            # PUT /me/player/play
            spotify_request(spotify.start_playback, device_id=device_id, uris=[uri])
            if artist and name:
                _LOGGER.info("Started playback: %s - %s", artist, name)
                return f"Started playback: {artist} - {name}"
            _LOGGER.info("Started playback: %s", uri)
            return f"Started playback: {uri}"
        _LOGGER.warning("No available devices to start playback")
        return "No available devices to start playback."
    except (
        spotipy.exceptions.SpotifyException,
        SpotifyOauthError,
        SpotifyAuthorizationRequired,
    ) as error:
        return spotify_error_message(error)
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error during add to queue")
        return "Failed to add track to queue."


def player_add_item_to_playback_queue(query):
    """Add the track to the playback queue if there are any search results"""
    try:
        # Get the top result for the query: GET tracks.items[0].uri
        search_results = spotify_request(spotify.search, q=query, limit=1, type="track")
        search_items = search_results["tracks"]["items"]

        if len(search_items) > 0:
            artist = search_items[0]["artists"][0]["name"]
            name = search_items[0]["name"]
            uri = search_items[0]["uri"]
            _LOGGER.debug("Artist: %s, name: %s, uri: %s", artist, name, uri)
            return player_add_track(uri, artist, name)

        _LOGGER.debug("No search results")
        return "No search results."
    except (
        spotipy.exceptions.SpotifyException,
        SpotifyOauthError,
        SpotifyAuthorizationRequired,
    ) as error:
        return spotify_error_message(error)
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error during add to queue")
        return "Failed to add track to queue."


def player_skip_to_next():
    """Skip to next track"""
    return spotify_action_with_soco_fallback(
        spotify_action=spotify.next_track,
        soco_action=lambda speaker: speaker.next(),
        action_name="skip to next track",
    )


def player_pause_playback():
    """Pause playback"""
    return spotify_action_with_soco_fallback(
        spotify_action=spotify.pause_playback,
        soco_action=lambda speaker: speaker.pause(),
        action_name="pause playback",
    )


def get_now_playing_embed():  # pylint: disable=too-many-locals, too-many-statements
    """Try to get now playing info from Spotify, fall back to SoCo."""
    _LOGGER.debug("Checking Spotify current playback...")
    spotify_problem = None
    # Try Spotify first
    try:
        playback = spotify_request(spotify.current_playback)
        if playback is None:
            _LOGGER.debug("Spotify returned no playback (None)")
            raise ValueError("No active Spotify playback")

        item = playback.get("item")
        if not item:
            _LOGGER.debug("Spotify playback has no 'item' field")
            raise ValueError("Spotify playback item missing.")

        artist = ", ".join(a["name"] for a in item["artists"])
        title = item["name"]
        album = item["album"]["name"]
        url = item["external_urls"]["spotify"]
        image_url = item["album"]["images"][0]["url"]
        progress_ms = playback["progress_ms"]
        duration_ms = item["duration_ms"]
        device = playback.get("device", {}).get("name", "Unknown Device")
        is_playing = playback.get("is_playing", False)

        _LOGGER.info(
            "Spotify: '%s' by %s [%s] on %s — %d ms into %d ms",
            title,
            artist,
            album,
            device,
            progress_ms,
            duration_ms,
        )

        # Compute progress
        progress_min = int(progress_ms / 60000)
        progress_sec = int((progress_ms % 60000) / 1000)
        duration_min = int(duration_ms / 60000)
        duration_sec = int((duration_ms % 60000) / 1000)

        status_emoji = "▶️" if is_playing else "⏸️"

        # Best-effort queue position via context
        context = playback.get("context")
        context_type = context.get("type") if isinstance(context, dict) else ""
        queue_info = f"📦 Context: *{context_type}*" if context_type else ""

        description = (
            f"{status_emoji} **Now Playing on {device}:** [{artist} – {title}]({url})\n"
            f"💿 Album: *{album}*\n"
            f"⏱️ {progress_min}:{progress_sec:02d} / {duration_min}:{duration_sec:02d}\n"
            f"{queue_info}"
        )

        embed = discord.Embed(description=description)
        embed.set_thumbnail(url=image_url)
        return embed

    # Best-effort Spotify lookup: fall back to Sonos on any API or payload error.
    except (
        SpotifyOauthError,
        SpotifyAuthorizationRequired,
        spotipy.exceptions.SpotifyException,
    ) as error:
        spotify_problem = spotify_error_message(error)
    except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        _LOGGER.debug("Spotify now playing lookup failed: %s", type(error).__name__)

    # Try SoCo fallback
    _LOGGER.debug("Trying Sonos fallback...")
    try:
        speaker = find_playing_speaker()
        if not speaker:
            _LOGGER.debug("No Sonos speaker found")
            return None

        track_info = speaker.get_current_track_info()
        title = track_info.get("title", "Unknown Title")
        artist = track_info.get("artist", "Unknown Artist")
        album = track_info.get("album", "Unknown Album")
        duration = track_info.get("duration", 0)
        position = speaker.get_current_transport_info().get("current_position", "0:00")
        album_art_uri = track_info.get("album_art_uri", "")
        album_art_url = (
            speaker.get_album_art_full_uri(album_art_uri) if album_art_uri else None
        )

        device = speaker.player_name

        _LOGGER.info(
            "Sonos: '%s' by %s [%s] on %s (%s / %s)",
            title,
            artist,
            album,
            device,
            position,
            duration,
        )

        description = (
            f"📡 **Sonos Playback on {device}:** {artist} – {title}\n"
            f"💿 Album: *{album}*\n"
            f"⏱️ {position} / {duration}"
        )

        embed = discord.Embed(description=description)
        if album_art_url:
            embed.set_thumbnail(url=album_art_url)
        return embed

    # A failed fallback must not interrupt the Discord command.
    except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        _LOGGER.debug("SoCo now playing fallback failed: %s", e)

    if spotify_problem:
        return discord.Embed(description=f"❌ {spotify_problem}")

    return None


async def autocomplete_tracks(_: discord.Interaction, current: str):
    """Fetch Spotify search suggestions based on current input"""
    if not current:
        return []

    try:
        results = spotify_request(spotify.search, q=current, limit=5, type="track")
    except (
        spotipy.exceptions.SpotifyException,
        SpotifyOauthError,
        SpotifyAuthorizationRequired,
    ) as error:
        spotify_error_message(error)
        return []

    tracks = results.get("tracks", {}).get("items", [])

    def truncate(text, max_length=100):
        """data.choices.i.name: Must be between 1 and 100 in length."""
        return text if len(text) <= max_length else text[:97] + "..."

    return [
        app_commands.Choice(
            name=truncate(f"{track['artists'][0]['name']} - {track['name']}"),
            value=track["uri"],
        )
        for track in tracks
    ]


@bot.tree.command(name="add", description="Add a song to the Spotify queue")
@app_commands.describe(query="Search for a song")
@app_commands.autocomplete(query=autocomplete_tracks)
async def add_slash(interaction: discord.Interaction, query: str):
    """Slash command to queue a Spotify track by URI or search string.

    If the input is a Spotify URI, it will be queued directly.
    If it's a plain search string, the bot will show a dropdown of top results.
    """
    _LOGGER.debug("/add command by %s", interaction.user.name)

    def truncate(text: str, max_length: int = 100) -> str:
        return text if len(text) <= max_length else text[:97] + "..."

    # If input is already a URI, queue directly
    if query.startswith("spotify:track:"):
        try:
            track = spotify_request(spotify.track, query)
            title = track["name"]
            artist = ", ".join(a["name"] for a in track["artists"])
            result = player_add_track(query, artist, title)
            if result.startswith(("Queued:", "Started playback:")):
                await interaction.response.send_message(f"✅ {result}", delete_after=10)
            else:
                await interaction.response.send_message(f"❌ {result}", ephemeral=True)
        except (
            spotipy.exceptions.SpotifyException,
            SpotifyOauthError,
            SpotifyAuthorizationRequired,
        ) as error:
            await interaction.response.send_message(
                f"❌ {spotify_error_message(error)}", ephemeral=True
            )
        return

    try:
        results = spotify_request(spotify.search, q=query, limit=5, type="track")
        tracks = results.get("tracks", {}).get("items", [])

        if not tracks:
            await interaction.response.send_message(
                "❌ No results found.", ephemeral=True
            )
            return

        class FallbackDropdown(
            discord.ui.Select
        ):  # pylint: disable=too-few-public-methods
            """Dropdown UI component for letting the user select from search results."""

            def __init__(self, parent_view: discord.ui.View):
                options = [
                    discord.SelectOption(
                        label=truncate(
                            f"{track['artists'][0]['name']} - {track['name']}"
                        ),
                        value=track["uri"],
                    )
                    for track in tracks
                ]
                super().__init__(
                    placeholder="Select a track to queue",
                    min_values=1,
                    max_values=1,
                    options=options,
                )
                self.parent_view = parent_view

            async def callback(self, interaction_dropdown: discord.Interaction):
                """Disable menu and queue selected track."""
                uri = self.values[0]
                try:
                    track = spotify_request(spotify.track, uri)
                    title = track["name"]
                    artist = ", ".join(a["name"] for a in track["artists"])
                    result = player_add_track(uri, artist, title)
                    if not result.startswith(("Queued:", "Started playback:")):
                        await interaction_dropdown.response.send_message(
                            f"❌ {result}", ephemeral=True
                        )
                        return

                    self.disabled = (  # pylint: disable=attribute-defined-outside-init
                        True
                    )
                    await interaction_dropdown.message.edit(view=self.parent_view)

                    await interaction_dropdown.response.send_message(
                        f"✅ {result}", delete_after=10
                    )
                    await interaction_dropdown.message.delete(delay=10)
                except (
                    spotipy.exceptions.SpotifyException,
                    SpotifyOauthError,
                    SpotifyAuthorizationRequired,
                ) as error:
                    await interaction_dropdown.response.send_message(
                        f"❌ {spotify_error_message(error)}", ephemeral=True
                    )

        class FallbackDropdownView(
            discord.ui.View
        ):  # pylint: disable=too-few-public-methods
            """Encapsulates the fallback dropdown in a Discord UI view."""

            message: discord.Message | None

            def __init__(self):
                super().__init__()
                self.message = None

        view = FallbackDropdownView()
        view.add_item(FallbackDropdown(parent_view=view))
        await interaction.response.send_message("Select a track:", view=view)
        view.message = await interaction.original_response()

    except (
        spotipy.exceptions.SpotifyException,
        SpotifyOauthError,
        SpotifyAuthorizationRequired,
    ) as error:
        await interaction.response.send_message(
            f"❌ {spotify_error_message(error)}", ephemeral=True
        )


@bot.tree.command(name="pause", description="Pause the current playback")
async def pause_slash(interaction: discord.Interaction):
    """Slash command to pause Spotify playback."""
    _LOGGER.debug("/pause command by %s", interaction.user.name)
    response = await asyncio.get_running_loop().run_in_executor(
        None, player_pause_playback
    )
    await interaction.response.send_message(
        "✅ Paused playback." if response == "✅" else f"❌ {response}",
        ephemeral=True,
    )


@bot.tree.command(name="next", description="Skip to the next track")
async def next_slash(interaction: discord.Interaction):
    """Slash command to skip to the next track on Spotify."""
    _LOGGER.debug("/next command by %s", interaction.user.name)
    response = await asyncio.get_running_loop().run_in_executor(
        None, player_skip_to_next
    )
    await interaction.response.send_message(
        "✅ Skipped to next track." if response == "✅" else f"❌ {response}",
        ephemeral=True,
    )


@bot.tree.command(name="np", description="Show the currently playing track")
async def np_slash(interaction: discord.Interaction):
    """Slash command to show the currently playing track."""
    _LOGGER.debug("/np command by %s", interaction.user.name)
    await interaction.response.defer(ephemeral=True)
    embed = get_now_playing_embed()
    if embed:
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send("❌ Nothing is playing.")


async def main():
    """Initialize and start the bot."""
    _LOGGER.info("Björnify version: %s", __version__)
    await bot.start(DISCORD_BOT_TOKEN)


async def shutdown():
    """Handle graceful shutdown of the bot."""
    _LOGGER.info("Received stop signal. Shutting down gracefully...")
    monitor = getattr(bot, "spotify_monitor_task", None)
    if monitor is not None:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor
    await bot.close()
    server = getattr(bot, "spotify_callback_server", None)
    if server is not None:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
    _LOGGER.info("Shutdown complete")
    for log_handler in logging.getLogger().handlers:
        log_handler.flush()


def handle_signal(*_):
    """Signal handler that initiates bot shutdown."""
    asyncio.create_task(shutdown())


def run_bot():
    """Run the bot and set up signal handlers."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, handle_signal)
    try:
        loop.run_until_complete(main())
    finally:
        _LOGGER.debug("Cleaning up asyncio loop")
        loop.close()


if __name__ == "__main__":
    run_bot()
