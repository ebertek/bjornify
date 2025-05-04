#!/usr/bin/python
"""Load Björnify Discord.py bot"""

# pylint: disable=duplicate-code

import asyncio
import logging
import os

import discord
import soco
import spotipy
from discord import app_commands
from discord.ext import commands
from spotipy.oauth2 import SpotifyOAuth

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

# Apply to root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)  # Log everything
root_logger.addHandler(file_handler)

# Create app-specific logger
_LOGGER = logging.getLogger("bjornify")
_LOGGER.setLevel(logging.DEBUG)
_LOGGER.propagate = True  # Let messages bubble up to root

# Reduce verbosity
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)
logging.getLogger("discord.state").setLevel(logging.WARNING)
logging.getLogger("spotipy.client").setLevel(logging.INFO)
logging.getLogger("spotipy.oauth2").setLevel(logging.INFO)
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

# Load environment variables
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# Validate that all required env vars are set
required_env_vars = [
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "SPOTIPY_REDIRECT_URI",
    "DISCORD_BOT_TOKEN",
    "CHANNEL_ID",
]

for var in required_env_vars:
    if not os.getenv(var):
        raise EnvironmentError(f"Missing required environment variable: {var}")

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
        """Register cogs and slash commands to the guild."""
        await self.add_cog(AddTrackCog(self))
        await self.add_cog(PlaybackControlCog(self))

        GUILD_ID = os.getenv("GUILD_ID")
        if GUILD_ID:
            try:
                guild = discord.Object(id=int(GUILD_ID))
                synced = await self.tree.sync(guild=guild)
                _LOGGER.info(
                    "Synced %d slash commands to guild ID %s", len(synced), GUILD_ID
                )
            except Exception as e:
                _LOGGER.warning("Failed to sync commands to guild %s: %s", GUILD_ID, e)
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


def refresh_spotify_token():
    """Force refresh Spotify access token."""
    global spotify  # pylint: disable=global-statement
    _LOGGER.info("Refreshing Spotify access token manually.")

    auth_manager.refresh_access_token(auth_manager.get_cached_token()["refresh_token"])
    spotify = spotipy.Spotify(auth_manager=auth_manager)


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
            _LOGGER.warning(
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
                _LOGGER.info(
                    "Speaker %s playing Spotify (via URI)", speaker.player_name
                )
                return speaker

            # Fallback: VirtualLineInSource pointing to Spotify
            if uri.startswith("x-sonos-vli:") and "spotify:" in uri:
                _LOGGER.info(
                    "Speaker %s playing Spotify (via VirtualLineInSource)",
                    speaker.player_name,
                )
                return speaker

            # Optional fallback: metadata sniffing
            if "x-sonos-spotify:" in metadata:
                _LOGGER.info(
                    "Speaker %s playing Spotify (via metadata)", speaker.player_name
                )
                return speaker

        except Exception as e:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
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
            _LOGGER.debug("%s via Spotify", action_name)
            return "✅"
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 401:
                _LOGGER.warning("Spotify token expired, refreshing token.")
                refresh_spotify_token()
                try:
                    spotify_action()  # Retry once after refreshing
                    _LOGGER.debug("%s via Spotify (after refresh)", action_name)
                    return "✅"
                except Exception as ex:  # pylint: disable=broad-exception-caught
                    _LOGGER.error("Failed after token refresh: %s", ex)
                    return "🚫"
            if e.http_status == 403:
                _LOGGER.warning(
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
                        _LOGGER.error("Failed to %s via SoCo: %s", action_name, ex)
                        return "🚫"
                _LOGGER.warning("No active speaker found via SoCo.")
                return "🚫"
            _LOGGER.error("Unexpected Spotify error during %s: %s", action_name, e)
            return "🚫"
    _LOGGER.debug("%s failed: no playback found.", action_name.capitalize())
    return "🚫"


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
        _LOGGER.error("Failed to sync slash commands: %s", e)
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


@bot.event
async def on_message(message):
    """New message"""
    await bot.process_commands(message)


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

            # GET /me/player
            playback_results = spotify.current_playback()
            if playback_results is not None:
                # POST /me/player/queue
                spotify.add_to_queue(uri)
                _LOGGER.debug("Queued: %s - %s", artist, name)
                return f"Queued: {artist} - {name}"

            # GET /me/player/devices
            devices = spotify.devices()
            device_id = None
            for device in devices["devices"]:
                if device["name"] == "Everywhere":
                    device_id = device["id"]
                    break
            if device_id is None and devices["devices"]:
                device_id = devices["devices"][0]["id"]
            if device_id:
                _LOGGER.debug("device_id: %s", device_id)
                # PUT /me/player/play
                spotify.start_playback(device_id=device_id, uris=[uri])
                _LOGGER.debug("Started playback: %s - %s", artist, name)
                return f"Started playback: {artist} - {name}"
            _LOGGER.warning("No available devices to start playback.")

        return "No results"
    except spotipy.exceptions.SpotifyException as e:
        _LOGGER.error("Spotify error during add to queue: %s", e)
        return "Failed to add track to queue."
    except Exception as e:  # pylint: disable=broad-exception-caught
        _LOGGER.error("Unexpected error during add to queue: %s", e)
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


@app_commands.guild_only()
class AddTrackCog(commands.Cog):
    """Cog for handling slash command /add with autocomplete and fallback UI."""

    def __init__(self, discord_bot):
        self.bot = discord_bot

    async def autocomplete_tracks(self, _: discord.Interaction, current: str):
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

    @app_commands.command(name="add", description="Add a song to the Spotify queue")
    @app_commands.describe(query="Search for a song")
    @app_commands.autocomplete(query=autocomplete_tracks)
    async def add_slash(self, interaction: discord.Interaction, query: str):
        """Command that queues the selected song"""
        if not query.startswith("spotify:track:"):
            # fallback mode: query is not a URI, it's a search string
            results = spotify.search(q=query, limit=5, type="track")
            tracks = results.get("tracks", {}).get("items", [])

            if not tracks:
                await interaction.response.send_message(
                    "🚫 No results found.", ephemeral=True
                )
                return

            # Build fallback dropdown
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
                """Dropdown UI for selecting a fallback track from search results."""

                def __init__(self):
                    super().__init__(
                        placeholder="Select a track to queue",
                        min_values=1,
                        max_values=1,
                        options=options,
                    )

                async def callback(self, interaction_dropdown: discord.Interaction):
                    """Handle user selection and add the chosen track to the Spotify queue."""
                    uri = self.values[0]
                    try:
                        spotify.add_to_queue(uri)
                        await interaction_dropdown.response.send_message(
                            "✅ Queued selected track!", delete_after=10
                        )
                    except spotipy.exceptions.SpotifyException as e:
                        await interaction_dropdown.response.send_message(
                            f"🚫 Failed to add track: {e}", delete_after=10
                        )

            class FallbackDropdownView(
                discord.ui.View
            ):  # pylint: disable=too-few-public-methods
                """View that wraps the fallback dropdown for track selection."""

                def __init__(self):
                    super().__init__(timeout=30)
                    self.add_item(FallbackDropdown())

            await interaction.response.send_message(
                "Select a track:", view=FallbackDropdownView()
            )
            return

        # Normal case: user selected a real URI via autocomplete
        try:
            spotify.add_to_queue(query)
            await interaction.response.send_message(
                "✅ Queued selected track!", delete_after=10
            )
        except spotipy.exceptions.SpotifyException as e:
            await interaction.response.send_message(
                f"🚫 Failed to add track: {e}", delete_after=10
            )


@app_commands.guild_only()
class PlaybackControlCog(commands.Cog):
    """Cog for /pause and /next slash commands."""

    def __init__(self, discord_bot: commands.Bot):
        """Initialize the playback control cog with a Discord bot instance."""
        self.bot = discord_bot

    @app_commands.command(name="pause", description="Pause the current playback")
    async def pause_slash(self, interaction: discord.Interaction):
        """Slash command to pause Spotify playback."""
        _LOGGER.debug("/pause command by %s", interaction.user.name)
        response = await self.bot.loop.run_in_executor(None, player_pause_playback)
        await interaction.response.send_message(
            f"{response} Paused playback.", ephemeral=True
        )

    @app_commands.command(name="next", description="Skip to the next track")
    async def next_slash(self, interaction: discord.Interaction):
        """Slash command to skip to the next track on Spotify."""
        _LOGGER.debug("/next command by %s", interaction.user.name)
        response = await self.bot.loop.run_in_executor(None, player_skip_to_next)
        await interaction.response.send_message(
            f"{response} Skipped to next track.", ephemeral=True
        )


async def main():
    """Initialize the bot, add cogs, and start it."""
    await bot.start(DISCORD_BOT_TOKEN)


asyncio.run(main())
