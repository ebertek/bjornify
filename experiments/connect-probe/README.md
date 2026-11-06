# Spotify Connect OAuth experiment

This is a **read-only test**, not a Sonos queue fix. It checks whether a token issued to Björnify's existing Spotify application with the extra `streaming` scope can establish a librespot session. Passing this test does not prove Sonos queue commands work.

The probe does not start a Discord bot, register a playback device, transfer playback, play songs, or modify either the Spotify queue or Sonos queue.

The additional OAuth approval uses the same Spotify client ID as Björnify, with an isolated cache at `/tmp/bjornify-connect-probe.cache`. It does **not** modify the production `/app/secrets/spotipy_token.cache`. The temporary grant is for testing; the goal is to reuse a single authorization flow if the Connect approach proves feasible.

## On Pleiades

### 1. Clone the experimental branch and build the checker

```bash
git clone --branch experiment/spotify-connect-oauth-probe --single-branch \
  https://github.com/ebertek/bjornify.git bjornify-connect-probe
cd bjornify-connect-probe
docker build -t bjornify-connect-auth-probe \
  -f experiments/connect-probe/Dockerfile .
```

This builds a standalone read-only command. The running Björnify container stays untouched.

### 2. Copy the temporary OAuth helper

```bash
docker cp experiments/connect-probe/oauth_probe.py \
  bjornify:/app/spotify_connect_probe.py
```

### 3. Approve streaming for the existing Spotify application

```bash
docker exec -it bjornify python /app/spotify_connect_probe.py authorize
```

Open the displayed Spotify authorization URL. Spotify may redirect to the running Björnify callback, which can show an expected **400 error** because it does not recognize the probe's OAuth state. Copy the **full redirected URL** from the browser address bar, then paste it into the hidden terminal prompt. Do not share this URL, which contains a one-time authorization code.

Inspect only the granted scopes (no secrets are displayed):

```bash
docker exec bjornify python /app/spotify_connect_probe.py status
```

### 4. Test the Connect login without playback

```bash
set -o pipefail
docker exec -i bjornify python /app/spotify_connect_probe.py pipe-token | \
  docker run --rm -i bjornify-connect-auth-probe \
    "$(docker exec bjornify printenv SPOTIPY_CLIENT_ID)"
```

The access token travels through a local stdin pipe, not a command-line argument or visible terminal output. Only share the final **PASS**, **FAIL**, or timeout message here, never token or authorization URL contents.

- **PASS:** Spotify accepts a librespot session for Björnify's personal app. We can then prototype queueing on a restricted Sonos device on this branch.
- **FAIL:** This OAuth grant or app identity is not accepted for a Connect session. We must reconsider the authentication approach without affecting the working bot.
- **Timeout:** The handshake could not be completed; the result does not prove whether the app is authorized.

Spotify's published `streaming` scope is intended for its Web Playback SDK, so approval of that scope does not guarantee compatibility with librespot.

## Cleanup

```bash
docker exec bjornify rm -f /tmp/bjornify-connect-probe.cache \
  /app/spotify_connect_probe.py
docker image rm bjornify-connect-auth-probe
```

The test token cache lives in the running container's `/tmp` (not the secrets volume); the temporary helper is under `/app` in that container only. This experiment requires no Docker Compose modifications or changes to `/add`.

Do not use the Sonos local queue as a workaround: the observed Spotify playback source was `x-sonos-vli` with an empty Sonos queue, so that queue is not the same one as Spotify Connect playback.
