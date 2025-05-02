#!/usr/bin/python
"""Load Björnify Discord.py bot"""

# pylint: disable=R0801

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
intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(
    command_prefix="!",
    description="Björnify adds requested tracks to Björngrottan's Spotify playback queue",
    intents=intents,
)


def refresh_spotify_token():
    """Force refresh Spotify access token."""
    global spotify  # pylint: disable=W0603
    _LOGGER.info("Refreshing Spotify access token manually.")

    auth_manager.refresh_access_token(auth_manager.get_cached_token()["refresh_token"])
    spotify = spotipy.Spotify(auth_manager=auth_manager)


def find_playing_speaker():
    """Find and return the currently playing Sonos speaker using SoCo."""
    speakers = soco.discover()

    if not speakers:
        _LOGGER.info("No Sonos speakers found.")
        return None

    checked_coordinators = set()

    for speaker in speakers:
        coordinator = speaker.group.coordinator

        if coordinator.uid in checked_coordinators:
            continue

        checked_coordinators.add(coordinator.uid)

        # Check if the coordinator is playing
        try:
            state = coordinator.get_current_transport_info()["current_transport_state"]
            if state != "PLAYING":
                continue
        except Exception as e:  # pylint: disable=W0718
            _LOGGER.warning(
                "Failed to get transport state for %s: %s", coordinator.player_name, e
            )
            continue

        # Check current track URI
        try:
            uri = coordinator.get_current_track_info().get("uri", "")
            if "x-sonos-spotify:" in uri:
                _LOGGER.info(
                    "Coordinator %s playing Spotify (via URI)", coordinator.player_name
                )
                return coordinator
        except Exception as e:  # pylint: disable=W0718
            _LOGGER.warning(
                "Failed to get track info for %s: %s", coordinator.player_name, e
            )

        # Check VirtualLineInSource
        try:
            zgs = coordinator.zone_group_state
            for group in zgs.zone_groups:
                for member in group.members:
                    if (
                        member == coordinator
                        and member.virtual_line_in_source == "spotify"
                    ):
                        _LOGGER.info(
                            "Coordinator %s playing Spotify (via VirtualLineInSource)",
                            member.player_name,
                        )
                        return member
        except Exception as e:  # pylint: disable=W0718
            _LOGGER.warning(
                "Failed to parse ZoneGroupState for %s: %s", coordinator.player_name, e
            )

    _LOGGER.info("No Sonos speakers are currently playing.")
    return None


def spotify_action_with_soco_fallback(
    spotify_action, soco_action, action_name
):  # pylint: disable=R0911
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
                except Exception as ex:  # pylint: disable=W0718
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
                    except Exception as ex:  # pylint: disable=W0718
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
    except Exception as e:  # pylint: disable=W0718
        _LOGGER.exception("Unexpected error during sync")
        await ctx.send("❌ An unexpected error occurred during sync.")


@bot.event
async def on_message(message):
    """New message"""
    _LOGGER.debug("New message in %s", message.channel.id)
    # Ignore the bot's own messages
    if message.author == bot.user or message.author.bot:
        _LOGGER.debug("Message ignored from %s", message.author)
        return

    # Check if the message is in the correct channel
    _LOGGER.debug("Check if it matches %s", CHANNEL_ID)
    if int(message.channel.id) == int(CHANNEL_ID):
        _LOGGER.debug("Channel matches")
        query = message.content
        _LOGGER.debug("Query: %s", query)
        if query.startswith("!add "):
            response = player_add_item_to_playback_queue(query.removeprefix("!add "))
            await message.channel.send(response)
        elif query.startswith("!next"):
            response = await bot.loop.run_in_executor(None, player_skip_to_next)
            await message.add_reaction(response)
        elif query.startswith("!pause") or query.startswith("!stop"):
            response = await bot.loop.run_in_executor(None, player_pause_playback)
            await message.add_reaction(response)

    await bot.process_commands(message)  # Ensure commands like !sync are still processed


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
    except Exception as e:  # pylint: disable=W0718
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
    async def add(self, interaction: discord.Interaction, query: str):
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


async def main():
    """Initialize the bot, add cogs, and start it."""
    await bot.add_cog(AddTrackCog(bot))
    await bot.start(DISCORD_BOT_TOKEN)


asyncio.run(main())
