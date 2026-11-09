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

## Read-only diagnostic for a failed Connect-state write

If a guarded queue-write attempt returns `librespot error category:
FailedPrecondition`, do **not** retry the track yet. This category can arise
before the Spotify Connect-state request is sent. A successful `Session::connect`
does not prove the separate Spotify `login5` service-token exchange works.

After updating the experimental branch and rebuilding the standalone probe,
run this **read-only** prerequisite test with the existing isolated OAuth grant:

```bash
set -o pipefail
docker exec -i bjornify python /app/spotify_connect_probe.py pipe-token | \
  docker run --rm -i bjornify-connect-auth-probe \
    "$(docker exec bjornify printenv SPOTIPY_CLIENT_ID)" diagnose
```

It reports only whether the reusable login credential is present and whether
Spotify client-token issuance, login5 service-token issuance, and service
endpoint resolution succeed. Token **values**, account IDs, service URLs, and
raw error messages are never printed. This makes authentication-related
requests only; it does **not** register a player, change playback, or attempt
any queue write. Share the diagnostic result, not an OAuth token.

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

### Finding the Sonos Connect identity when Spotify returns a null ID

Spotify can report an active Sonos device with `id: null` and
`is_restricted: true`. The Web API then cannot identify the queue target.
An `inspect` result like this is expected; it is not an OAuth failure.

The separate Python helper uses the existing SoCo installation to discover
Sonos receivers, then performs only
`GET http://<Sonos LAN IP>:1400/spotifyzc?action=getInfo`.
The helper prints the non-secret receiver identity, name and transport state,
not OAuth tokens, private keys or account information.

```bash
docker cp experiments/connect-probe/sonos_discovery.py \
  bjornify:/app/sonos_discovery.py
docker exec bjornify python /app/sonos_discovery.py
```

The discovery helper is read-only and makes no Spotify queue commands.
A ZeroConf device ID alone **does not prove** it is the device actively
playing the account's Spotify session, especially in a grouped Sonos system.
The generic `add` command still refuses missing Web API device IDs.

The experimental `add-sonos` mode provides an explicitly gated alternative.
It queries the specified private-LAN Sonos receiver's ZeroConf endpoint
and checks the name, type, and Connect ID against the user's inputs.
It then checks Spotify's current playback is **playing** on an **active,
restricted speaker** with the same name, and that any Spotify-reported
ID agrees with the receiver. A mismatch stops the command before any write.
The matching name is not a cryptographic proof of the active Connect session;
on grouped/renamed speakers or if playback switches between checks, avoid the
write test. A Connect-cluster identity check may still be needed for production.

**Only run this with a single test track when you're happy to modify
the active Spotify queue.** With Spotify playing on the inspected Sonos
room, insert the room's LAN address and 40-character ZeroConf ID:

```bash
set -o pipefail
docker exec -i bjornify python /app/spotify_connect_probe.py pipe-token | \
  docker run --rm -i bjornify-connect-auth-probe \
    "$(docker exec bjornify printenv SPOTIPY_CLIENT_ID)" \
    add-sonos "ROOM_NAME" "PRIVATE_IPV4" "SONOS_CONNECT_ID" \
    "spotify:track:22_CHARACTER_TRACK_ID" --confirm-write
```

It sends one Connect-state `add_to_queue` command if all checks pass.
`SENT` only means Spotify acknowledged the command, not that the track
is in the queue. Check your Spotify app queue afterward. A timeout
leaves the outcome unknown; do not blindly retry or you may enqueue
the same track twice. This command never transfers, pauses or skips playback.

If the command fails, the CLI reports a **safe HTTP status and category**
(e.g. `HTTP 403 (Connect-state permission denied)`) or a generic librespot
error category. It deliberately never prints service URLs, raw response
bodies, access tokens, or other sensitive error details. It also prevents
librespot's automatic retries of this queue POST to reduce duplicate writes.

An authenticated librespot login alone may not create a registered Connect
playback session. Spotifast keeps a full playback engine running and
registers its Connect state; this small experimental CLI currently does not.
A failed queue command does **not** establish that Spotify forbids all
unofficial Connect-state control from this account. Do not assume a 403 is
caused by OAuth scope without additional evidence.

The optional queue test is _deliberately manual_. Only try it if the output is
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
