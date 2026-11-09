# Repository guidance for coding agents

This file is the starting point for work in `ebertek/bjornify`. Read the
current code and CI configuration before making changes; do not assume
details from an earlier chat, branch, or deployment are still accurate.

## Project

- Björnify is a Python 3.15 Discord music bot. The optional Home Assistant
  conversation bot shares its Docker image.
- Normal Spotify search, metadata, and playback control use Spotipy.
- Sonos control uses SoCo where supported.
- When Spotify's Web API returns a _specific_ restricted-device error for an
  active Sonos session, Björnify can queue through a separately authenticated
  librespot/Spotify Connect backend.
- Both `!` commands and Discord `/` commands are supported. Keep their
  outcomes, failure messages, and authorization checks consistent.

## Source map

- `bjornify/bjornify.py`: Discord bot, commands, queue orchestration.
- `bjornify/hass.py`: optional Home Assistant conversation bot.
- `bjornify/spotify_auth.py`: personal Spotify Web API OAuth and CLI.
- `bjornify/spotify_discord_auth.py`: owner-only Web API OAuth via HTTPS
  callback (`/spotify connect`).
- `bjornify/spotify_connect_discord_auth.py`: owner-only device-code pairing
  for Connect playback (`/spotify connect-playback`).
- `experiments/connect-probe/playback_device_auth.py`: **production-used**
  Connect playback OAuth/pairing and refresh logic; the Dockerfile installs
  it as `/app/connect_playback_auth.py`.
- `bjornify/spotify_connect_queue.py`: guarded Sonos queue fallback.
- `bjornify/spotify_devices.py`: choose a usable device when nothing plays.
- `bjornify/spotify_errors.py`: classify Spotify API failures.
- `bjornify/spotify_notifications.py`: reauthorization notifications.
- `bjornify/bot_logging.py`: structured logging and noise filtering.
- `experiments/connect-probe/src/main.rs`: Rust Connect-state command and
  preflight checks; **production-used** despite its directory name.
- `experiments/connect-probe/Cargo.toml`: Rust dependencies.
- `bjornify/Dockerfile`: multi-stage production image with the Rust binary.
- `bjornify/wrapper.sh`: starts the main bot and optional HA bot, healthcheck.
- `tests/`: mocked tests; `.github/workflows/`: lint, Rust/Docker validation,
  and GHCR publishing; `.github/renovate.json`: dependency-update policy.

## Authorization and secrets

- Maintain **two independent OAuth grants**. Personal Spotify Web API OAuth
  uses `/app/secrets/spotipy_token.cache`, `/spotify connect` in Discord,
  and `python /app/spotify_auth.py authorize` in the CLI.
- Spotify Connect desktop playback OAuth uses
  `/app/secrets/spotify_connect_playback.cache`,
  `/spotify connect-playback` in Discord, and
  `python /app/connect_playback_auth.py authorize` in the CLI.
- `/spotify status` reports both grants without exposing credentials.
  Discord authorization commands are owner-only; status and pairing codes
  are private/ephemeral. Playback device-code auth uses `spotify.com/pair`,
  not the Web API callback.
- Preserve owner checks, OAuth state/expiry checks, permission scoping,
  separate token refresh, private cache permissions (0600), and nonblocking
  Discord interactions. Keep CLI and Discord flows functional.
- Never commit or log tokens, refresh tokens, one-time OAuth URLs/codes,
  secrets, private account data, or actual home-network/device identifiers.
  Tests should use synthetic placeholders. Do not log raw HTTP/subprocess
  responses that might contain credentials.

## Playback behavior and safety

- Keep Spotipy the primary path. Do **not** replace normal Web API queueing
  with the private Connect endpoint.
- Resume paused playback before ordinary queueing. If there is no active
  playback, prefer `DEFAULT_DEVICE`, then a usable nonrestricted device;
  skip restricted devices and entries without a usable Spotify device ID.
- The Connect fallback requires verified _active, playing, restricted_
  Spotify speaker state, a uniquely matched ungrouped/playing Sonos room,
  private-LAN receiver identity via ZeroConf, and separate Web API and
  playback credentials. Fail closed on any mismatch or uncertainty.
- Connect-state queue commands are undocumented. Send at most **one** write
  per request; never blindly retry on failure or timeout, because the
  queue may already have changed. `SENT` is an acknowledgment, not proof
  of final delivery. Do not start/transfer playback as part of the fallback.
- `BJORNIFY_CONNECT_ENABLED=false` disables only the Connect fallback.
- Avoid blocking Discord's event loop: use deferred interactions and
  `asyncio.to_thread` for blocking Spotify/Sonos/subprocess work.
- Keep `!add`, `/add` (including selection UI), `!next`, `/next`,
  `!pause`, `/pause`, `!np`, and `/np` behavior covered by tests.

## Development and validation

Run from the repository root. Install `bjornify/requirements.txt` into a
local Python environment before running tests. CI uses Python 3.15.

```bash
python -m unittest discover -s tests -v
python -m pip install -r .github/requirements-lint.txt
ruff check --extend-select UP,TRY,BLE,DTZ,EXE,I bjornify scripts tests
black --check bjornify scripts tests
cargo fmt --manifest-path experiments/connect-probe/Cargo.toml --all -- --check
cargo check --manifest-path experiments/connect-probe/Cargo.toml
cargo test --manifest-path experiments/connect-probe/Cargo.toml
docker build -f bjornify/Dockerfile -t bjornify:local .
```

- Add or update mocked regression tests for every behavior change, especially
  both Discord command styles, OAuth flows, Sonos identity checks, and
  uncertain queue outcomes. Never require live Spotify, Discord, Sonos, or
  real credentials for CI tests.
- The Docker build context is the **repository root**, not `bjornify/`.
  The production image copies the Python playback helper and Rust binary
  from `experiments/connect-probe/`. Validate both when editing those paths.
- The Rust dependency `vergen = "=9.0.6"` is intentionally pinned for the
  `librespot-core = "=0.8.0"` build-script compatibility issue. Keep the
  matching scoped Renovate exception until a compatible upstream release
  is published and verified; do not casually uplift one crate in isolation.
- CI may auto-fix Python with Ruff/Black. Check the **latest PR head** and
  all relevant workflows after it does so. A successful local test alone
  does not establish that the PR is green.
- Only a successful `main` Lint push triggers GHCR multi-architecture image
  publishing. Do not treat PR builds as deployed releases.

## Change and documentation rules

- Make narrow, reviewable commits/PRs against `main`; inspect the current
  repository state and existing open PRs first. Don't claim an unrun live
  integration test has passed. Don't merge or deploy unless requested.
- Keep `README.md` **short and user-facing**, describing only current
  capabilities, setup, commands, and configuration. Put migration history,
  experiments, implementation rationale, and troubleshooting detail in PRs,
  issues, or developer documentation instead.
- Keep real deployment Compose files, volumes, and secrets outside this
  repository. Do not edit running deployment directories or infrastructure
  repositories as part of a Björnify code change unless explicitly asked.
- Before wrapping up, report the branch/PR, what changed, CI results,
  any unverified behavior, and whether the change was merged or deployed.
