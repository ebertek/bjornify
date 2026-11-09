"""Restricted-Sonos Spotify Connect queue fallback for Björnify.

The public Spotify Web API handles normal playback, search, and queuing.
When it refuses an *already active* Sonos speaker as restricted, this helper
uses the separately paired desktop playback grant and bundled librespot CLI.
It never transfers or starts playback, and it never retries a queue POST.
"""

import importlib
import ipaddress
import json
import logging
import os
import re
import subprocess

import requests
import soco

_LOGGER = logging.getLogger(__name__)
CONNECT_BINARY = "/usr/local/bin/bjornify-connect"
CONNECT_TIMEOUT_SECONDS = 65
LAN_TIMEOUT = (2, 4)
MAX_ZERO_CONF_BYTES = 16 * 1024
VALID_DEVICE_ID = re.compile(r"[0-9a-fA-F]{40}\Z")
VALID_TRACK_URI = re.compile(r"spotify:track:[A-Za-z0-9]{22}\Z")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class ConnectQueueError(RuntimeError):
    """Safe, actionable queue failure without OAuth or receiver secrets."""


def private_sonos_ip(value):
    """Return a private IPv4 address; reject DNS names and unusual network targets."""
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return None
    return (
        str(address)
        if any(address in network for network in PRIVATE_NETWORKS)
        else None
    )


def get_receiver_id(address):
    """Get a bounded, read-only ZeroConf receiver identity."""
    ip_address = private_sonos_ip(address)
    if not ip_address:
        raise ConnectQueueError("Sonos address is outside the private LAN.")
    session = requests.Session()
    session.trust_env = False
    try:
        with session.get(
            f"http://{ip_address}:1400/spotifyzc",
            params={"action": "getInfo"},
            timeout=LAN_TIMEOUT,
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.status_code != 200:
                raise ConnectQueueError("Sonos did not provide a Connect identity.")
            body = bytearray()
            for chunk in response.iter_content(4096):
                body.extend(chunk)
                if len(body) > MAX_ZERO_CONF_BYTES:
                    raise ConnectQueueError("Sonos identity response was too large.")
            data = json.loads(body)
    except (requests.RequestException, ValueError, TypeError) as error:
        raise ConnectQueueError("Sonos Connect identity could not be read.") from error
    finally:
        session.close()

    if not isinstance(data, dict):
        raise ConnectQueueError("Sonos Connect identity is malformed.")
    device_id = data.get("deviceID")
    if not isinstance(device_id, str) or not VALID_DEVICE_ID.fullmatch(device_id):
        raise ConnectQueueError("Sonos Connect device ID is missing or invalid.")
    return data.get("remoteName"), data.get("deviceType"), device_id


def find_ungrouped_receiver(name):
    """Require one discoverable, playing, ungrouped Sonos receiver."""
    try:
        speakers = soco.discover(timeout=5) or set()
        matches = [speaker for speaker in speakers if speaker.player_name == name]
        if len(matches) != 1:
            raise ConnectQueueError("Expected exactly one Sonos room with this name.")
        speaker = matches[0]
        group = speaker.group
        if (
            group.coordinator.ip_address != speaker.ip_address
            or len(group.members) != 1
        ):
            raise ConnectQueueError(
                "Grouped Sonos rooms are not supported for queue fallback."
            )
        state = speaker.get_current_transport_info().get("current_transport_state")
        if state != "PLAYING":
            raise ConnectQueueError("The Sonos room is no longer playing.")
        address = private_sonos_ip(speaker.ip_address)
        if not address:
            raise ConnectQueueError("Sonos has no usable private IPv4 address.")
        return address
    except ConnectQueueError:
        raise
    except (OSError, ValueError, TypeError, soco.exceptions.SoCoException) as error:
        raise ConnectQueueError(
            "Sonos room discovery or state check failed."
        ) from error


def personal_access_token(auth_manager):
    """Read or refresh the shared Web API authorization, without reauthorizing."""
    cached = auth_manager.cache_handler.get_cached_token()
    if not isinstance(cached, dict) or not cached.get("refresh_token"):
        raise ConnectQueueError("Spotify Web API must be connected first.")
    if "user-read-playback-state" not in (cached.get("scope") or "").split():
        raise ConnectQueueError("Spotify Web API playback permission is missing.")
    token = auth_manager.get_access_token(as_dict=False)
    if not isinstance(token, str) or not token:
        raise ConnectQueueError("Spotify Web API authorization is unavailable.")
    return token


def queue_restricted_sonos(uri, playback, auth_manager):
    """Queue one track on a verified active Sonos speaker, or fail closed.

    Returns only after the command endpoint acknowledges the single addition.
    The Spotify app, rather than this private endpoint, confirms delivery.
    """
    if os.getenv("BJORNIFY_CONNECT_ENABLED", "true").lower() not in {
        "true",
        "1",
        "yes",
    }:
        raise ConnectQueueError("The Spotify Connect fallback is disabled.")
    if not VALID_TRACK_URI.fullmatch(uri):
        raise ConnectQueueError("Only one Spotify track URI is supported.")
    if not isinstance(playback, dict) or playback.get("is_playing") is not True:
        raise ConnectQueueError("Spotify playback is not active.")
    device = playback.get("device") or {}
    name = device.get("name")
    if (
        device.get("is_active") is not True
        or device.get("is_restricted") is not True
        or str(device.get("type", "")).lower() != "speaker"
        or not isinstance(name, str)
        or not name
        or len(name) > 80
        or any(char in name for char in "\r\n\0")
    ):
        raise ConnectQueueError("Spotify does not report an active restricted speaker.")

    address = find_ungrouped_receiver(name)
    remote_name, remote_type, receiver_id = get_receiver_id(address)
    if remote_name != name or str(remote_type).lower() != "speaker":
        raise ConnectQueueError(
            "Sonos Connect identity does not match Spotify's device."
        )

    # Spotify's desktop playback grant is entirely separate from the personal
    # Web API grant. Neither credential appears in the subprocess arguments.
    web_token = personal_access_token(auth_manager)
    playback_auth = importlib.import_module("connect_playback_auth")
    try:
        grant = playback_auth.valid_grant()
    except (
        ValueError,
        TypeError,
        OSError,
        KeyError,
        requests.RequestException,
    ) as error:
        raise ConnectQueueError(
            "Connect playback authorization is missing or expired; pair Spotify again."
        ) from error
    playback_token = grant.get("access_token")
    if not isinstance(playback_token, str) or not playback_token:
        raise ConnectQueueError("Spotify Connect playback authorization is missing.")
    if web_token == playback_token:
        raise ConnectQueueError("Spotify Connect and Web API grants must be separate.")

    envelope = json.dumps(
        {"playback_token": playback_token, "web_api_token": web_token}
    )
    command = [
        CONNECT_BINARY,
        playback_auth.PLAYBACK_CLIENT_ID,
        "add-sonos",
        name,
        address,
        receiver_id,
        uri,
        "--confirm-write",
    ]
    try:
        completed = subprocess.run(
            command,
            input=envelope,
            text=True,
            capture_output=True,
            check=False,
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConnectQueueError(
            "Connect queue command failed or timed out; outcome is unknown."
        ) from error

    if completed.returncode != 0 or not completed.stdout.startswith(
        "SENT: one Connect-state queue command"
    ):
        # Never log stdout/stderr: the subprocess error details and service
        # responses are not trusted to remain free of account metadata.
        _LOGGER.warning(
            "Restricted-Sonos queue command failed (exit status %d)",
            completed.returncode,
        )
        raise ConnectQueueError(
            "Spotify Connect did not confirm the queue request; outcome is unknown."
        )
    _LOGGER.info("Spotify Connect accepted a queue addition to Sonos room %s", name)
    return True
