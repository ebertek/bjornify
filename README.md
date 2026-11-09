# 🎵 Björnify

Björnify is a Discord music bot that searches Spotify, queues tracks, and controls playback. It uses [Spotipy](https://github.com/spotipy-dev/spotipy) for Spotify's Web API, [SoCo](https://github.com/SoCo/SoCo) for supported Sonos controls, and [librespot](https://github.com/librespot-org/librespot) to queue tracks on restricted Sonos Spotify Connect sessions.

## Commands

| Text          | Slash    | Action                  |
| ------------- | -------- | ----------------------- |
| `!add <song>` | `/add`   | Search and queue a song |
| `!next`       | `/next`  | Skip to the next track  |
| `!pause`      | `/pause` | Pause playback          |
| `!np`         | `/np`    | Show what's playing     |

`/add` supports search autocomplete. If playback is paused, Björnify resumes it before queueing. If nothing is playing, it tries an available Spotify device. For an active, ungrouped Sonos player that Spotify reports as restricted, Björnify can queue through Spotify Connect instead.

## Setup

Create a [Discord application](https://discord.com/developers/applications) and a [Spotify app](https://developer.spotify.com/dashboard). Enable the Discord bot's Message Content intent. Spotify Development Mode requires an authorized Premium account.

Create a `.env` file:

```dotenv
DISCORD_BOT_TOKEN=your_discord_bot_token
CHANNEL_ID=your_discord_channel_id
GUILD_ID=your_discord_server_id
SPOTIPY_CLIENT_ID=your_spotify_client_id
SPOTIPY_CLIENT_SECRET=your_spotify_client_secret
SPOTIPY_REDIRECT_URI=https://spotify.example.com/spotify/callback
```

Create `compose.yaml`:

```yaml
services:
  bjornify:
    image: ghcr.io/ebertek/bjornify:latest
    container_name: bjornify
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./logs:/app/logs
      - ./secrets:/app/secrets
```

Start with `docker compose up -d`. The `/app/secrets` volume stores Spotify credentials across restarts.

For Discord-based Spotify Web API authorization, register the exact HTTPS `SPOTIPY_REDIRECT_URI` in your Spotify app and route its `/spotify/callback` path through your reverse proxy to Björnify's internal port `8765`.

## Spotify authorization

Only the Discord application owner can use these commands:

| Discord command             | Purpose                                                                   |
| --------------------------- | ------------------------------------------------------------------------- |
| `/spotify connect`          | Authorize the Spotify Web API using the HTTPS callback                    |
| `/spotify connect-playback` | Pair Spotify Connect through [spotify.com/pair](https://spotify.com/pair) |
| `/spotify status`           | Check both connections                                                    |

Follow the private instructions in Discord. The Web API and Connect playback use separate credentials.

Server administrators can also authorize through the CLI:

```bash
docker exec -it bjornify python /app/spotify_auth.py authorize
docker exec -it bjornify python /app/connect_playback_auth.py authorize
```

The Web API CLI flow can use `http://127.0.0.1:3000` as its registered redirect URI. For Connect playback, the CLI displays a pairing code for [Spotify Pair](https://spotify.com/pair).

## Configuration

Optional environment variables:

- `DEFAULT_DEVICE`: Preferred Spotify device when playback is inactive.
- `BJORNIFY_CONNECT_ENABLED=false`: Disable the restricted-Sonos queue fallback.
- `SPOTIFY_AUTH_CALLBACK_PORT`: Internal callback server port (default `8765`).
- `LOG_LEVEL`, `LIB_LOG_LEVEL`: Logging verbosity (default `INFO` and `ERROR`).
- `LOG_OUTPUT`, `LOG_FORMAT`: Logging destinations and format.

The Connect queue fallback requires a playing, ungrouped Sonos room and won't retry an uncertain queue write. Spotify's private Connect interface may change without notice.

## Home Assistant (optional)

The image also includes `hass.py`, a Discord bot for Home Assistant conversations. Configure `HASS_DISCORD_BOT_TOKEN`, `HASS_CHANNEL_ID`, `HA_URL`, and `HA_ACCESS_TOKEN` to use it.

## Support

[Open an issue](https://github.com/ebertek/bjornify/issues) with the command, steps to reproduce, and relevant logs. Remove credentials and other private data before sharing.
