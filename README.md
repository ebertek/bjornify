# 🎵 Björnify

Björnify is a Discord bot based on [discord.py](https://github.com/scarletcafe/discord.py-docker) that adds requested tracks to your Spotify playback queue.

## 🧠 How It Works

- Listens for `!add`, `!next`, and `!pause` commands.
- Uses [Spotipy](https://github.com/spotipy-dev/spotipy) to search tracks and manage playback via Spotify Web API.
- Queues tracks or starts playback if nothing is playing.
- Falls back to controlling Sonos speakers via [SoCo](https://github.com/SoCo/SoCo) if Spotify playback fails due to device restrictions.
- Uses autocomplete and dropdown UI for enhanced slash command experience (`/add`).

## 🚀 Usage examples

### 💬 Text commands

- 🎶 `!add Souvlaki Space Station` — Add the first track that matches your query
- 🧩 `!add track:Anti-Hero album:Midnights artist:Taylor Swift year:2022` — Add a track using detailed filters
- ⏭️ `!next` — Skip to the next track
- ⏸️ `!pause` — Pause playback

### 🧵 Slash commands

These are visible only to you and provide autocomplete support:

- 🔍 `/add` — Search for tracks and add the selected one to the queue
- ⏭️ `/next` — Skip to the next track
- ⏸️ `/pause` — Pause playback

## 🧩 Docker Compose

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
DEFAULT_DEVICE=Everywhere
LOG_LEVEL=INFO
LIB_LOG_LEVEL=WARNING
HASS_DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
HASS_CHANNEL_ID=xxxxxxxxxxxxxxxxxxx
HA_URL=https://hass.local/api/conversation/process
HA_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 🔐 Environmental variables

| Variable Name           | Description                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------- |
| `SPOTIPY_CLIENT_ID`     | Your Spotify app’s client ID used for API authentication.                              |
| `SPOTIPY_CLIENT_SECRET` | Your Spotify app’s client secret.                                                      |
| `SPOTIPY_REDIRECT_URI`  | Redirect URI registered with your Spotify app.                                         |
| `DISCORD_BOT_TOKEN`     | Token for Björnify to access the Discord API.                                          |
| `CHANNEL_ID`            | Discord channel ID where Björnify listens for `!add`, `!next`, and `!pause`.           |
| `GUILD_ID`              | Optional: Discord guild ID where Björnify listens for `/add`, `/next`, and `/pause`.   |
| `DEFAULT_DEVICE`        | Optional: Device used to start playback if no devices are currently playing.           |
| `LOG_LEVEL`             | Optional: Log level for Björnify: `DEBUG` > `INFO` > `WARNING` > `ERROR` > `CRITICAL`. |
| `LIB_LOG_LEVEL`         | Optional: Log level for `asyncio`, `discord`, `soco`, `spotipy`, and `urllib3`.        |

To get your own Spotify Client ID and secret, please create a new app using the [Spotify for Developers](https://developer.spotify.com/dashboard) Dashboard. You can add `http://localhost:3000` to _Redirect URIs_, and select `Web API` for _APIs used_.

To get your own Discord token, please create a new application using the [Discord Developer Portal](https://discord.com/developers/applications). You will need the `bot` and the `applications.commands` scopes. Under _Bot permissions_, you will need to check `View Channels`, `Send Messages`, `Add Reactions`, `Read Message History`, and `Use External Emojis`.

### 💾 Persistent volumes

| Volume         | Description                       |
| -------------- | --------------------------------- |
| `/app/logs`    | Location of logs                  |
| `/app/secrets` | Location of `spotipy_token.cache` |

### 🤖 Home Assistant conversation bot

A second bot, `hass.py` is also included in this image. It sends text to Home Assistant’s conversation API. If you want to use it, please create a second application using the [Discord Developer Portal](https://discord.com/developers/applications). You will also need to configure the following environmental variables in `bjornify.txt`:

| Variable Name            | Description                                                             |
| ------------------------ | ----------------------------------------------------------------------- |
| `HASS_DISCORD_BOT_TOKEN` | Token for the Home Assistant conversation bot.                          |
| `HASS_CHANNEL_ID`        | Discord channel ID for Home Assistant conversation bot commands.        |
| `HA_URL`                 | URL to your Home Assistant’s `/api/conversation/process` endpoint.      |
| `HA_ACCESS_TOKEN`        | Long-lived Home Assistant access token for authenticating API requests. |

## 🐳 Docker

As an alternative to Docker Compose, you can simply run the container using `docker run`:

```shell
docker run --name bjornify \
  --cpus="1.0" \
  --memory="250m" \
  -e SPOTIPY_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  -e SPOTIPY_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  -e SPOTIPY_REDIRECT_URI=http://localhost:3000 \
  -e DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  -e CHANNEL_ID=xxxxxxxxxxxxxxxxxxx \
  -e GUILD_ID=xxxxxxxxxxxxxxxxxxx \
  -e DEFAULT_DEVICE=Everywhere \
  -e HASS_DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  -e HASS_CHANNEL_ID=xxxxxxxxxxxxxxxxxxx \
  -e HA_URL=https://hass.local/api/conversation/process \
  -e HA_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  -e TZ=Europe/Stockholm \
  --hostname bjornify \
  --restart=no \
  --stop-timeout=180 \
  --stop-signal=SIGINT \
  --user 1028:100 \
  -v /mnt/docker/bjornify/logs:/app/logs \
  -v /mnt/docker/bjornify/secrets:/app/secrets \
  ghcr.io/ebertek/bjornify:latest
```

## 🐞 Submitting Issues

If you encounter a problem, feel free to [open an issue](https://github.com/ebertek/bjornify/issues). Please include the following to help us investigate:

- A description of the issue and how to reproduce it
- What command you used (e.g. `/add` or `!next`)
- The relevant section of the debug logs from `/app/logs/bjornify.log`

Logs help tremendously with diagnosing errors. If possible, redact any personal information (like tokens or private names).
