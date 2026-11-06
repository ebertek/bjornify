# Experimental Spotify Connect OAuth compatibility test

This is a **read-only first phase**, not the Sonos queue fix. It tests whether
Björnify's **existing Spotify application/client ID** can open a librespot
Connect session with an OAuth token granted `streaming`. If that works, the
next phase can implement adding tracks to restricted Sonos Spotify Connect
sessions. A successful login alone does **not** establish that queue writes
will work.

**Nothing in this experiment starts the Discord bot, transfers playback,
advertises a new playback device, plays audio, adds songs or touches a Sonos
queue.** It must not be merged into main until tested and reviewed.

## Why a temporary grant?

Spotify's published `streaming` permission is for the Web Playback SDK. It
is not evidence that a personal Web API app can establish a librespot session.
We therefore test rather than assume support.

The Python script requests all existing Björnify scopes **plus** `streaming`
using the same `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET`.
It caches the new test grant at
`/tmp/bjornify-connect-probe.cache` inside the currently running Björnify
container. Your production `/app/secrets/spotipy_token.cache` remains unchanged.
The experiment requires **one additional OAuth approval** for the same Spotify
app, only for testing; it is not a second Spotify app and does not require a
new permanent credential or separate production container.

## Run on Pleiades (do not stop Björnify)

**1.** Check out the isolated experiment and build the read-only Rust
authentication checker. This image does not run the Discord bot.

```bash
git clone --branch experiment/spotify-connect-oauth-probe --single-branch \
  https://github.com/ebertek/bjornify.git bjornify-connect-probe
cd bjornify-connect-probe
docker build -t bjornify-connect-auth-probe \
  -f experiments/connect-probe/Dockerfile .
```

**2.** Copy the Python helper into the running Björnify container. It is
read-only with respect to playback and uses a different, temporary token cache.

```bash
docker cp experiments/connect-probe/oauth_probe.py \
  bjornify:/tmp/bjornify-oauth-probe.py
```

**3.** Authorize `streaming` once, using the SAME Spotify application,
client ID and configured redirect URI as Björnify:

```bash
docker exec -it bjornify python /tmp/bjornify-oauth-probe.py authorize
```

Open the printed authorization URL. When Spotify redirects to the existing
Björnify callback, it may display a **400 error**: the existing callback did
not create this experiment's OAuth state. That is expected. Copy the **full**
redirected URL with `code` and `state` from the browser address bar and
paste it at the hidden prompt in the terminal. **Never share that URL in
Discord, chat, logs, screenshots or GitHub.**

Confirm the scope without exposing any credentials:

```bash
docker exec bjornify python /tmp/bjornify-oauth-probe.py status
```

**4.** Run the read-only librespot session handshake. The OAuth token is
sent by a **local pipe**, never as a command-line argument or terminal output.
`pipefail` also reports any failure in the token supplier.

```bash
set -o pipefail
docker exec -i bjornify python /tmp/bjornify-oauth-probe.py pipe-token | \
  docker run --rm -i bjornify-connect-auth-probe \
    "$(docker exec bjornify printenv SPOTIPY_CLIENT_ID)"
```

Expected results:

- **PASS:** librespot accepted the streaming grant for the existing app's
  client ID. Next we can implement and test a restricted-device queue
  controller, still without changing `main`.
- **FAIL:** the grant or this app's identity was rejected, or the session
  could not connect. A successful `streaming` grant is *not* a guarantee
  that Connect will accept a personal client ID.
- **Timeout:** networking or Spotify service connectivity prevented a
  conclusion; this is not evidence the grant was rejected.

You can paste the output of the **Rust program** here; it never prints
tokens or full Spotify request URLs. Do not paste the OAuth URL or any cache
contents.

## Cleanup

The temporary token cache is inside the running container's `/tmp`,
not in your bind-mounted secrets volume:

```bash
docker exec bjornify rm -f /tmp/bjornify-connect-probe.cache \
  /tmp/bjornify-oauth-probe.py
docker image rm bjornify-connect-auth-probe
```

No changes to production Docker Compose, existing OAuth cache, `/add` or
`main` are required. The experiment itself does not touch audio or queues.

## Why not use Sonos' local queue?

The observed Sonos source was `x-sonos-vli` with an empty local Sonos queue.
Changing that local queue would not queue into the currently playing Spotify
Connect session, so it is intentionally out of scope.
