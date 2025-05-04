# 🎵 Björnify

Björnify is a Discord bot that adds requested tracks to the Spotify playback queue. It uses [discord.py](https://github.com/scarletcafe/discord.py-docker), [Spotipy](https://github.com/spotipy-dev/spotipy), and [SoCo](https://github.com/SoCo/SoCo) to manage playback across Spotify and Sonos speakers.

## 🚀 Usage examples

- 🎶 `!add Souvlaki Space Station` — Add a song by name
- 🧩 `!add track:Anti-Hero album:Midnights artist:Taylor Swift year:2022` — Add a track using detailed filters
- ⏭️ `!next` — Skip to the next song
- ⏸️ `!pause` — Pause playback

## 🐳 Docker Compose

### `compose.yaml`

```yaml
---
name: bjornify

services:
  bjornify:
    container_name: bjornify
    cpu_count: 1
    deploy:
      resources:
        limits:
          # cpus: "1"
          memory: 250M
    env_file: bjornify.txt
    environment:
      TZ: Europe/Stockholm
    hostname: bjornify
    image: "ghcr.io/ebertek/bjornify:latest"
    restart: "no"
    stop_grace_period: 3m
    stop_signal: SIGINT
    user: "1028:100"
    volumes:
      - "/mnt/docker/bjornify/logs:/app/logs"
      - "/mnt/docker/bjornify/secrets:/app/secrets"
```

### `bjornify.txt`

```shell
SPOTIPY_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SPOTIPY_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SPOTIPY_REDIRECT_URI=http://localhost:3000
DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
CHANNEL_ID=xxxxxxxxxxxxxxxxxxx
GUILD_ID=xxxxxxxxxxxxxxxxxxx
HASS_DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
HASS_CHANNEL_ID=xxxxxxxxxxxxxxxxxxx
HA_URL=https://hass.local/api/conversation/process
HA_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Environmental variables

| Variable Name            | Description                                                             |
| ------------------------ | ----------------------------------------------------------------------- |
| `SPOTIPY_CLIENT_ID`      | Your Spotify app’s client ID used for API authentication.               |
| `SPOTIPY_CLIENT_SECRET`  | Your Spotify app’s client secret.                                       |
| `SPOTIPY_REDIRECT_URI`   | Redirect URI registered with your Spotify app.                          |
| `DISCORD_BOT_TOKEN`      | Token for Björnify to access the Discord API.                           |
| `CHANNEL_ID`             | Discord channel ID where Björnify listens for `!add`, `!next`, etc.     |
| `GUILD_ID`               | Discord guild ID where Björnify listens for `/add`, `/next`, etc.       |
| `HASS_DISCORD_BOT_TOKEN` | Token for the Home Assistant conversation bot.                          |
| `HASS_CHANNEL_ID`        | Discord channel ID for Home Assistant conversation bot commands.        |
| `HA_URL`                 | URL to your Home Assistant’s `/api/conversation/process` endpoint.      |
| `HA_ACCESS_TOKEN`        | Long-lived Home Assistant access token for authenticating API requests. |

To get your own Spotify Client ID and secret, please create a new app using the [Spotify for Developers](https://developer.spotify.com/dashboard) Dashboard. You can add `http://localhost:3000` to _Redirect URIs_, and select `Web API` for _APIs used_.
To get your own Discord token, please create a new application using [Discord Developer Portal](https://discord.com/developers/applications). You will need the `bot` and the `applications.commands` scopes. Under _Bot permissions_, you will need to check `View Channels`, `Send Messages`, `Add Reactions`, `Read Message History`, and `Use External Emojis`.

### Persistent volumes

| Volume         | Description                       |
| -------------- | --------------------------------- |
| `/app/logs`    | Location of logs                  |
| `/app/secrets` | Location of `spotipy_token.cache` |
