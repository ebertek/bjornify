# Spotify Connect OAuth experiment

This is an **isolated Spotify Connect experiment**, not a production Sonos queue fix. The default mode and `inspect` mode are read-only. After a successful authentication test, the optional, explicitly confirmed `add` mode attempts **exactly one** queue command to the current restricted playback device.

The probe does not start a Discord bot, register a playback device, transfer playback, or start/stop/skip songs. **Only the optional `add` mode may change Spotify's Connect queue.** It never targets the Sonos local queue.

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

This builds a standalone probe executable. Its default mode is read-only.
Björnify stays running; the only change inside its container is a temporary helper file
and an isolated token cache, both removable afterward.

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

## Optional phase 2: one guarded queue-write experiment

**Do not run this phase before the read-only authentication test returns PASS.** This is an undocumented Spotify Connect-state endpoint based on the Sonos implementation merged into [Spotifast PR #823](https://github.com/crmne/spotifast/pull/823). It may change without notice and a successful request is **not proof** the item appeared in the queue.

Update the checkout and rebuild after phase 1:

```bash
git pull --ff-only
docker build -t bjornify-connect-auth-probe \
  -f experiments/connect-probe/Dockerfile .
```

First, inspect the current Spotify playback **without changing anything**:

```bash
set -o pipefail
docker exec -i bjornify python /app/spotify_connect_probe.py pipe-token | \
  docker run --rm -i bjornify-connect-auth-probe \
    "$(docker exec bjornify printenv SPOTIPY_CLIENT_ID)" inspect
```

It prints the currently playing device's name and Spotify Connect device ID, plus `restricted` and `active` flags. It is safe to share this output; **never share the access token, authorization code, or a redirect URL**.

The optional queue test is *deliberately manual*. Only try it if the output is
for your expected Sonos room, with both `restricted: true` and `active: true`.
Pick one short test track and replace both placeholders below.

The CLI verifies that the device ID still matches active playback immediately
before submitting the single command. It refuses non-restricted devices,
malformed track URIs, and unexpected or missing active devices.

```bash
set -o pipefail
docker exec -i bjornify python /app/spotify_connect_probe.py pipe-token | \
  docker run --rm -i bjornify-connect-auth-probe \
    "$(docker exec bjornify printenv SPOTIPY_CLIENT_ID)" \
    add "PASTE_DEVICE_ID_FROM_INSPECT" \
    "spotify:track:PASTE_22_CHARACTER_ID" \
    --confirm-write
```

`SENT` means Spotify's Connect-state endpoint acknowledged the command, **not** that the track is in the queue. Check the actual Spotify app queue on the Sonos speaker. Do not repeatedly retry a timed-out request: its outcome may be unknown, and retrying could add duplicates. No playback transfer, pause, or Sonos local queue operation is sent.

The probe's regular invocation with just `CLIENT_ID` remains read-only; `inspect` is also read-only. The production Discord bot and its `/add` implementation remain unchanged, even after a successful experimental write.

## Cleanup

```bash
docker exec bjornify rm -f /tmp/bjornify-connect-probe.cache \
  /app/spotify_connect_probe.py
docker image rm bjornify-connect-auth-probe
```

The test token cache lives in the running container's `/tmp` (not the secrets volume); the temporary helper is under `/app` in that container only. This experiment requires no Docker Compose modifications or changes to `/add`. Running the optional `add` mode deliberately alters Spotify's Connect queue if successful.

Do not use the Sonos local queue as a workaround: the observed Spotify playback source was `x-sonos-vli` with an empty Sonos queue, so that queue is not the same one as Spotify Connect playback.
