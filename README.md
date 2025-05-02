# 🎵 Björnify

Björnify is a Discord bot that adds requested tracks to the Spotify playback queue. It uses [discord.py](https://discordpy.readthedocs.io/), [Spotipy](https://spotipy.readthedocs.io/), and [SoCo](https://github.com/SoCo/SoCo) to manage playback across Spotify and Sonos speakers.

## 📦 Usage examples

- `!add Souvlaki Space Station`
- `!add track:Anti-Hero album:Midnights artist:Taylor Swift year:2022`
- `!next`
- `!pause`

## 🔧 Requirements

You must set these environment variables (via `.env` or Docker Compose):

### Required for Björnify:

- `SPOTIPY_CLIENT_ID`
- `SPOTIPY_CLIENT_SECRET`
- `SPOTIPY_REDIRECT_URI`
- `DISCORD_BOT_TOKEN`
- `CHANNEL_ID`

### (Optional) Required for HASS integration:

- `HASS_DISCORD_BOT_TOKEN`
- `HASS_CHANNEL_ID`
- `HA_URL`
- `HA_ACCESS_TOKEN`
