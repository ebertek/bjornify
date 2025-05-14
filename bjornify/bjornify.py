#!/usr/bin/python
"""Load Björnify Discord.py bot"""

# pylint: disable=duplicate-code

import asyncio
import logging
import os
import signal
import sys

import discord
import soco
import spotipy
from discord import app_commands
from discord.ext import commands
from spotipy.oauth2 import SpotifyOAuth

try:
    from version import __version__
except ImportError:
    __version__ = "dev"  # fallback for local dev without version.py

LOG_PATH = "logs/bjornify.log"

# Make sure log folder exists
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# Create and configure file handler
file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        "%Y-%m-%d - %H:%M:%S",
    )
)

# Define valid log levels
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Get and validate configured log levels
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in VALID_LOG_LEVELS:
    LOG_LEVEL = "INFO"  # fallback to default
LIB_LOG_LEVEL = os.getenv("LIB_LOG_LEVEL", "WARNING").upper()
if LIB_LOG_LEVEL not in VALID_LOG_LEVELS:
    LIB_LOG_LEVEL = "WARNING"  # fallback to default

# Apply to root logger
root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)
root_logger.addHandler(file_handler)

# Create app-specific logger
_LOGGER = logging.getLogger("bjornify")
_LOGGER.setLevel(LOG_LEVEL)
_LOGGER.propagate = True  # Let messages bubble up to root

# Apply lib log level to third-party modules
for lib in ("asyncio", "discord", "soco", "spotipy", "urllib3"):
    logging.getLogger(lib).setLevel(LIB_LOG_LEVEL)

# Validate that all required environmental variables are set
required_env_vars = [
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "DISCORD_BOT_TOKEN",
    "CHANNEL_ID",
]

for var in required_env_vars:
    if not os.getenv(var):
        raise EnvironmentError(f"Missing required environment variable: {var}")

# Load environmental variables
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://localhost:3000")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
DEFAULT_DEVICE = os.getenv("DEFAULT_DEVICE", "Everywhere")

# Set up Spotify
SCOPE = (
    "user-library-read"
    ",user-read-currently-playing"
    ",user-read-playback-state"
    ",user-modify-playback-state"
)

auth_manager = SpotifyOAuth(
    client_id=SPOTIPY_CLIENT_ID,
    client_secret=SPOTIPY_CLIENT_SECRET,
    redirect_uri=SPOTIPY_REDIRECT_URI,
    scope=SCOPE,
    open_browser=False,
    cache_path="/app/secrets/spotipy_token.cache",
)
spotify = spotipy.Spotify(auth_manager=auth_manager)


# Set up Discord
class BjornifyBot(commands.Bot):  # pylint: disable=too-few-public-methods
    """Custom bot class."""

    async def setup_hook(self):
        """Sync slash commands to the guild."""
        guild_id_str = os.getenv("GUILD_ID")
        if guild_id_str:
            try:
                guild = discord.Object(id=int(guild_id_str))
                for cmd in self.tree.get_commands(guild=guild):
                    _LOGGER.debug("Registered slash command before sync: %s", cmd.name)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                _LOGGER.info(
                    "Synced %d slash commands to guild ID %s", len(synced), guild_id_str
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                _LOGGER.exception(
                    "Failed to sync commands to guild %s: %s", guild_id_str, e
                )
        else:
            _LOGGER.info("GUILD_ID not set — slash commands will not be registered.")


intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True

bot = BjornifyBot(
    command_prefix="!",
    description="Björnify adds requested tracks to Björngrottan's Spotify playback queue",
    intents=intents,
)


def refresh_spotify_token(force: bool = False):
    """Refresh Spotify access token if expired, or always if force=True."""
    global spotify  # pylint: disable=global-statement
    token_info = auth_manager.get_cached_token()

    if force or auth_manager.is_token_expired(token_info):
        _LOGGER.info(
            "Refreshing Spotify token %s.",
            "forcefully" if force else "because it expired",
        )
        auth_manager.refresh_access_token(token_info["refresh_token"])
        spotify = spotipy.Spotify(auth_manager=auth_manager)
    else:
        _LOGGER.debug("Spotify token still valid — no refresh needed.")


def find_playing_speaker():
    """Find and return the first Sonos speaker that is currently playing Spotify."""
    speakers = soco.discover()

    if not speakers:
        _LOGGER.info("No Sonos speakers found.")
        return None

    for speaker in speakers:
        try:
            # Check if the speaker is playing
            state = speaker.get_current_transport_info()["current_transport_state"]
            if state != "PLAYING":
                continue
        except Exception as e:  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Failed to get transport state for %s: %s", speaker.player_name, e
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

        except Exception as e:  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Error while checking speaker %s: %s", speaker.player_name, e
            )

    _LOGGER.info("No currently playing Sonos speaker using Spotify was found.")
    return None


def spotify_action_with_soco_fallback(
    spotify_action, soco_action, action_name
):  # pylint: disable=too-many-return-statements
    """Try a Spotify action, fallback to a SoCo action if Spotify fails with 403."""
    # GET /me/player
    playback_results = spotify.current_playback()
    if playback_results is not None:
        try:
            # POST /me/player/next or PUT /me/player/pause
            spotify_action()
            _LOGGER.info("%s via Spotify", action_name)
            return "✅"
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 401:
                _LOGGER.warning("Spotify token expired, refreshing token.")
                refresh_spotify_token(force=True)
                try:
                    spotify_action()  # Retry once after refreshing
                    _LOGGER.info("%s via Spotify (after refresh)", action_name)
                    return "✅"
                except Exception as ex:  # pylint: disable=broad-exception-caught
                    _LOGGER.exception("Failed after token refresh: %s", ex)
                    return "❌"
            if e.http_status == 403:
                _LOGGER.exception(
                    "Spotify refused to %s: Restricted device. Trying with SoCo.",
                    action_name,
                )
                playing_speaker = find_playing_speaker()
                if playing_speaker:
                    try:
                        soco_action(playing_speaker)
                        _LOGGER.info(
                            "%s using SoCo: %s",
                            action_name.capitalize(),
                            playing_speaker.player_name,
                        )
                        return "✅"
                    except Exception as ex:  # pylint: disable=broad-exception-caught
                        _LOGGER.exception("Failed to %s via SoCo: %s", action_name, ex)
                        return "❌"
                _LOGGER.info("No active speaker found via SoCo.")
                return "❌"
            _LOGGER.exception("Unexpected Spotify error during %s: %s", action_name, e)
            return "❌"
    _LOGGER.debug("%s failed: no playback found.", action_name.capitalize())
    return "❌"


@bot.event
async def on_ready():
    """Bot logged in"""
    _LOGGER.info("Logged in as %s", bot.user)


@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Sync slash commands to the current guild."""
    _LOGGER.debug("User %s (%s) issued !sync command.", ctx.author.name, ctx.author.id)

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
        _LOGGER.exception("Failed to sync slash commands: %s", e)
        await ctx.send(f"❌ Failed to sync commands: {e}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error during sync: %s", e)
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
    response = await bot.loop.run_in_executor(None, player_pause_playback)
    await ctx.message.add_reaction(response)


@bot.command(name="next", aliases=["skip"])
async def skip_track(ctx):
    """Skip to the next Spotify track."""
    _LOGGER.debug("!next command by %s", ctx.author.name)
    response = await bot.loop.run_in_executor(None, player_skip_to_next)
    await ctx.message.add_reaction(response)


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
    _LOGGER.debug("Adding %s (%s - %s)", uri, artist, name)
    try:
        # GET /me/player
        playback_results = spotify.current_playback()
        if playback_results is not None:
            # POST /me/player/queue
            spotify.add_to_queue(uri)
            if artist and name:
                _LOGGER.info("Queued: %s - %s", artist, name)
                return f"Queued: {artist} - {name}"
            _LOGGER.info("Queued: %s", uri)
            return f"Queued: {uri}"

        # GET /me/player/devices
        devices = spotify.devices()
        device_id = None
        for device in devices["devices"]:
            _LOGGER.debug("Found device: %s", device["name"])
            if device["name"] == DEFAULT_DEVICE:
                device_id = device["id"]
                break
        # Fall back to the first device found
        if device_id is None and devices["devices"]:
            device_id = devices["devices"][0]["id"]
        if device_id:
            _LOGGER.debug("device_id: %s", device_id)
            # PUT /me/player/play
            spotify.start_playback(device_id=device_id, uris=[uri])
            if artist and name:
                _LOGGER.info("Started playback: %s - %s", artist, name)
                return f"Started playback: {artist} - {name}"
            _LOGGER.info("Started playback: %s", uri)
            return f"Started playback: {uri}"
        _LOGGER.warning("No available devices to start playback.")
        return "No available devices to start playback."
    except spotipy.exceptions.SpotifyException as e:
        _LOGGER.exception("Spotify error during add to queue: %s", e)
        return "Failed to add track to queue."
    except Exception as e:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error during add to queue: %s", e)
        return "Failed to add track to queue."


def player_add_item_to_playback_queue(query):
    """Add the track to the playback queue if there are any search results"""
    try:
        # Get the top result for the query: GET tracks.items[0].uri
        search_results = spotify.search(q=query, limit=1, type="track")
        search_items = search_results["tracks"]["items"]

        if len(search_items) > 0:
            artist = search_items[0]["artists"][0]["name"]
            name = search_items[0]["name"]
            uri = search_items[0]["uri"]
            _LOGGER.debug("Artist: %s, name: %s, uri: %s", artist, name, uri)
            return player_add_track(uri, artist, name)

        _LOGGER.debug("No search results.")
        return "No search results."
    except spotipy.exceptions.SpotifyException as e:
        _LOGGER.exception("Spotify error during add to queue: %s", e)
        return "Failed to add track to queue."
    except Exception as e:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error during add to queue: %s", e)
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
    # Try Spotify first
    try:
        playback = spotify.current_playback()
        if playback is None:
            _LOGGER.debug("Spotify returned no playback (None).")
            raise ValueError("No active Spotify playback.")

        item = playback.get("item")
        if not item:
            _LOGGER.debug("Spotify playback has no 'item' field.")
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

        _LOGGER.debug(
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

    except Exception as e:  # pylint: disable=broad-exception-caught
        _LOGGER.warning("Spotify now playing lookup failed: %s", e)

    # Try SoCo fallback
    _LOGGER.debug("Trying Sonos fallback...")
    try:
        speaker = find_playing_speaker()
        if not speaker:
            _LOGGER.debug("No Sonos speaker found.")
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

        _LOGGER.debug(
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

    except Exception as e:  # pylint: disable=broad-exception-caught
        _LOGGER.warning("SoCo now playing fallback failed: %s", e)

    return None


async def autocomplete_tracks(_: discord.Interaction, current: str):
    """Fetch Spotify search suggestions based on current input"""
    if not current:
        return []

    try:
        results = spotify.search(q=current, limit=5, type="track")
    except spotipy.exceptions.SpotifyException:
        return []

    tracks = results.get("tracks", {}).get("items", [])

    return [
        app_commands.Choice(
            name=f"{track['artists'][0]['name']} - {track['name']}",
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
    if not query.startswith("spotify:track:"):
        results = spotify.search(q=query, limit=5, type="track")
        tracks = results.get("tracks", {}).get("items", [])

        if not tracks:
            await interaction.response.send_message(
                "❌ No results found.", ephemeral=True
            )
            return

        options = [
            discord.SelectOption(
                label=f"{track['artists'][0]['name']} - {track['name']}",
                value=track["uri"],
            )
            for track in tracks
        ]

        class FallbackDropdown(
            discord.ui.Select
        ):  # pylint: disable=too-few-public-methods
            """Dropdown UI component for letting the user select from search results."""

            def __init__(self):
                super().__init__(
                    placeholder="Select a track to queue",
                    min_values=1,
                    max_values=1,
                    options=options,
                )

            async def callback(self, interaction_dropdown: discord.Interaction):
                """Handle the user's selection from the dropdown."""
                uri = self.values[0]
                try:
                    player_add_track(uri)
                    await interaction_dropdown.response.send_message(
                        "✅ Queued selected track!", delete_after=10
                    )
                except spotipy.exceptions.SpotifyException as e:
                    await interaction_dropdown.response.send_message(
                        f"❌ Failed to add track: {e}", delete_after=10
                    )

        class FallbackDropdownView(
            discord.ui.View
        ):  # pylint: disable=too-few-public-methods
            """Encapsulates the fallback dropdown in a Discord UI view with timeout."""

            def __init__(self):
                super().__init__(timeout=30)
                self.add_item(FallbackDropdown())

        await interaction.response.send_message(
            "Select a track:", view=FallbackDropdownView()
        )
        return

    try:
        player_add_track(query)
        await interaction.response.send_message(
            "✅ Queued selected track!", delete_after=10
        )
    except spotipy.exceptions.SpotifyException as e:
        await interaction.response.send_message(
            f"❌ Failed to add track: {e}", delete_after=10
        )


@bot.tree.command(name="pause", description="Pause the current playback")
async def pause_slash(interaction: discord.Interaction):
    """Slash command to pause Spotify playback."""
    _LOGGER.debug("/pause command by %s", interaction.user.name)
    response = await bot.loop.run_in_executor(None, player_pause_playback)
    await interaction.response.send_message(
        f"{response} Paused playback.", ephemeral=True
    )


@bot.tree.command(name="next", description="Skip to the next track")
async def next_slash(interaction: discord.Interaction):
    """Slash command to skip to the next track on Spotify."""
    _LOGGER.debug("/next command by %s", interaction.user.name)
    response = await bot.loop.run_in_executor(None, player_skip_to_next)
    await interaction.response.send_message(
        f"{response} Skipped to next track.", ephemeral=True
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
    await bot.close()
    _LOGGER.info("Shutdown complete.")
    sys.exit(0)


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
        _LOGGER.info("Cleaning up asyncio loop.")
        loop.close()


if __name__ == "__main__":
    run_bot()
