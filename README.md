# 🎵 Björnify

Björnify is a Discord bot based on [discord.py](https://github.com/Rapptz/discord.py) that adds requested tracks to your Spotify playback queue.

## 🧠 How It Works

- Listens for `!add`, `!next`, `!pause`, and `!np` commands.
- Uses [Spotipy](https://github.com/spotipy-dev/spotipy) to search tracks and manage playback via Spotify Web API.
- Queues tracks on the current Spotify session. If playback is paused, resumes the existing song before queueing the requested track next; if no session exists, prefers `DEFAULT_DEVICE` and then the first eligible Spotify Connect device, skipping devices flagged `is_restricted` or lacking a device ID.
- When an active, ungrouped Sonos speaker rejects queueing as a restricted device, uses a bundled [librespot](https://github.com/librespot-org/librespot) Connect fallback with separately paired credentials.
- Falls back to [SoCo](https://github.com/SoCo/SoCo) for supported playback controls when Spotify rejects a restricted device.
- Uses autocomplete and dropdown UI for enhanced slash command experience (`/add`).

## 🚀 Usage examples

### 💬 Text commands

- 🎶 `!add Souvlaki Space Station` — Add the first track that matches your query
- 🧩 `!add track:Anti-Hero album:Midnights artist:Taylor Swift year:2022` — Add a track using detailed filters
- ⏭️ `!next` — Skip to the next track
- ⏸️ `!pause` — Pause playback
- 🎧 `!np` — Show the currently playing track

### 🧵 Slash commands

These are visible only to you and provide autocomplete support:

- 🔍 `/add` — Search for tracks and add the selected one to the queue
- ⏭️ `/next` — Skip to the next track
- ⏸️ `/pause` — Pause playback
- 🎧 `/np` — Show the currently playing track

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
    user: "1028:100"
    volumes:
      - "/mnt/docker/bjornify/logs:/app/logs"
      - "/mnt/docker/bjornify/secrets:/app/secrets"
```

### `bjornify.txt`

```shell
SPOTIPY_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SPOTIPY_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SPOTIPY_REDIRECT_URI=https://spotify.example.com/spotify/callback
DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
CHANNEL_ID=xxxxxxxxxxxxxxxxxxx
GUILD_ID=xxxxxxxxxxxxxxxxxxx
DEFAULT_DEVICE=Everywhere
LOG_OUTPUT=console
LOG_LEVEL=INFO
LIB_LOG_LEVEL=ERROR
HASS_DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
HASS_CHANNEL_ID=xxxxxxxxxxxxxxxxxxx
HA_URL=https://hass.local/api/conversation/process
HA_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 🔐 Environmental variables

| Variable Name              | Description                                                                                         |
| -------------------------- | --------------------------------------------------------------------------------------------------- |
| `SPOTIPY_CLIENT_ID`        | Your Spotify app’s client ID used for API authentication.                                           |
| `SPOTIPY_CLIENT_SECRET`    | Your Spotify app’s client secret.                                                                   |
| `SPOTIPY_REDIRECT_URI`     | Redirect URI registered with your Spotify app.                                                      |
| `DISCORD_BOT_TOKEN`        | Token for Björnify to access the Discord API.                                                       |
| `CHANNEL_ID`               | Discord channel ID where Björnify listens for `!` commands.                                         |
| `GUILD_ID`                 | Optional: Discord guild ID where Björnify listens for `/` commands.                                 |
| `DEFAULT_DEVICE`           | Optional: Device used to start playback if no devices are currently playing.                        |
| `BJORNIFY_CONNECT_ENABLED` | Optional: `true` (default) enables restricted-Sonos Connect queue fallback. Set `false` to disable. |
| `LOG_OUTPUT`               | Optional: Comma-separated list to configure logging destinations: `console, file`.                  |
| `LOG_FORMAT`               | Optional: `plain` for human-readable, `json` for JSON.                                              |
| `LOG_LEVEL`                | Optional: Log level for Björnify: `DEBUG` > `INFO` > `WARNING` > `ERROR` > `CRITICAL`.              |
| `LIB_LOG_LEVEL`            | Optional: Log level for `asyncio`, `discord`, `soco`, `spotipy`, and `urllib3`.                     |

Routine Discord login and gateway messages are logged at `DEBUG`, while warnings and errors remain visible at normal verbosity. JSON timestamps include a timezone offset.

To get your own Spotify Client ID and secret, please create a new app using the [Spotify for Developers](https://developer.spotify.com/dashboard) Dashboard. You can add `http://127.0.0.1:3000` to _Redirect URIs_, and select `Web API` for _APIs used_.

### 🎵 Sonos Spotify Connect queue fallback

Björnify includes an optional Spotify Connect queue backend for Sonos speakers.
It is **enabled by default** but does nothing unless a separate playback grant
has been paired. Ordinary queue requests continue through Spotipy's Web API.
If the Web API rejects an **actively playing** Sonos speaker with the specific
`403 Restricted device` error, Björnify validates its current Spotify player
and Sonos ZeroConf identity before sending **one** queue command through the
bundled librespot binary.

**One-time setup:** while the new container is running, authorize the desktop
playback grant with Spotify's headless device pairing:

```bash
docker exec -it bjornify python /app/connect_playback_auth.py authorize
```

Visit [Spotify Pair](https://spotify.com/pair), enter the temporary pairing code,
and approve. This is a **separate** grant from `/spotify connect`, using the
desktop playback-client identity; no Spotify Developer Dashboard callback or
client secret is required. After authorization, the grant and refresh token are
stored as `/app/secrets/spotify_connect_playback.cache` with file mode `0600`.
Because `/app/secrets` is already a persistent volume, the pairing survives
container updates and restarts. The bot automatically refreshes the short-lived
playback access token when necessary.

Check the saved grant without printing credentials:

```bash
docker exec bjornify python /app/connect_playback_auth.py status
```

If you already completed the experimental pairing in an existing container,
you can preserve that separate grant **before recreating the container**:

```bash
docker exec bjornify sh -c 'umask 077; cp /tmp/bjornify-connect-playback.cache /app/secrets/spotify_connect_playback.cache'
```

Only use the copy command if that experimental cache exists. Otherwise pair
again after upgrading.

For safety, the queue fallback refuses paused/unknown sessions, grouped Sonos
rooms, ambiguous room names, and mismatching Connect identities. It never
transfers or starts playback or retries an uncertain queue write. A private
Spotify Connect-state endpoint is used; Spotify can change it without notice.
An accepted command is **not independently verified** against the Spotify
queue. If the request fails or times out, the bot reports that the outcome is
unknown rather than claiming the song was queued.

Set `BJORNIFY_CONNECT_ENABLED=false` to disable this optional backend while
keeping normal Spotify queueing, Sonos pause/skip, and metadata functions
unchanged. If the separate grant expires or is revoked, rerun the pairing
command. **Never paste or commit OAuth cache contents.**

### 🔑 Connect Spotify

Spotify Development Mode requires a Premium Spotify account authorized for
your app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).

To connect or reconnect, use **`/spotify connect`** in Discord as the owner of
the Discord bot. Click **Connect Spotify**, approve access, and Spotify will
redirect to Björnify. The browser confirms when authorization is complete.
Use **`/spotify status`** to check whether credentials are saved.

For automatic login, set **`SPOTIPY_REDIRECT_URI`** to an HTTPS callback, such
as `https://spotify.example.com/spotify/callback`. Register the **exact same
URL** in Spotify's Developer Dashboard. Route that HTTPS URL through your
reverse proxy to Björnify's internal HTTP callback on port `8765`
(`SPOTIFY_AUTH_CALLBACK_PORT` can override the port). The callback endpoint
must be reachable by your browser, including on mobile.

Spotify refresh tokens expire after six months, so you will occasionally need
to use `/spotify connect` again. The connection link is private, valid for ten
minutes, and can only be created by the Discord application owner. Björnify checks
the saved authorization periodically and sends a **one-time Discord DM** when
reconnection is needed. If DMs are disabled, it notifies the existing `CHANNEL_ID`
channel instead. Successful reconnection resets the alert; ordinary access-token
refreshes do not trigger notifications.

If you cannot configure HTTPS yet, the existing owner-only terminal fallback
is `docker exec -it bjornify python /app/spotify_auth.py authorize` with
`http://127.0.0.1:3000` as the registered callback. Spotify no longer accepts
`localhost` for this purpose.

To get your own Discord token, please create a new application using the [Discord Developer Portal](https://discord.com/developers/applications). You will need the `bot` and the `applications.commands` scopes. Under _Bot permissions_, you will need to check `View Channels`, `Send Messages`, `Add Reactions`, `Read Message History`, and `Use External Emojis`.

### 💾 Persistent volumes

| Volume         | Description                                                           |
| -------------- | --------------------------------------------------------------------- |
| `/app/logs`    | Location of logs                                                      |
| `/app/secrets` | Persistent Spotipy and separate Spotify Connect playback token caches |

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
  -e SPOTIPY_REDIRECT_URI=http://127.0.0.1:3000 \
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
  --user 1028:100 \
  -v /mnt/docker/bjornify/logs:/app/logs \
  -v /mnt/docker/bjornify/secrets:/app/secrets \
  ghcr.io/ebertek/bjornify:latest
```

## 🐞 Submitting Issues

If you encounter a problem, feel free to [open an issue](https://github.com/ebertek/bjornify/issues). Please include the following to help us investigate:

- A description of the issue and how to reproduce it
- What command you used (e.g. `/add` or `!next`)
- The relevant section of the debug logs from `/app/logs/bjornify.log` (set `LOG_OUTPUT` to `file`, and `LOG_LEVEL` and `LIB_LOG_LEVEL` to `DEBUG`)

Logs help tremendously with diagnosing errors. If possible, redact any personal information (like tokens or private names).
