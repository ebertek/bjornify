"""Read-only Sonos Spotify Connect identity discovery for the Björnify experiment.

Run inside the existing Björnify container so SoCo uses its working LAN discovery.
Requests use ONLY private LAN IPs discovered by SoCo and Sonos port 1400.
No OAuth, playback control, credentials, or queue writes.
"""

import ipaddress
import re
import sys

import requests
import soco

SONOS_NETWORKS = tuple(
    ipaddress.IPv4Network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
MAX_REPLY_BYTES = 16 * 1024
DEVICE_ID = re.compile(r"[0-9a-fA-F]{10,128}\\Z")


def safe_text(value):
    """Bound arbitrary LAN-supplied strings and remove control characters."""
    if not isinstance(value, str):
        return "<missing>"
    return "".join(c if c.isprintable() else " " for c in value)[:80] or "<empty>"


def lan_ipv4(address):
    """Accept RFC1918 IPv4 only: no DNS, loopback, or arbitrary HTTP targets."""
    try:
        parsed = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return None
    return str(parsed) if any(parsed in network for network in SONOS_NETWORKS) else None


def extract_info(payload):
    """Do not retain public keys, account names, or other getInfo fields."""
    if not isinstance(payload, dict):
        return None
    device_id = payload.get("deviceID")
    if not isinstance(device_id, str) or not DEVICE_ID.fullmatch(device_id):
        device_id = None
    return {
        "id": device_id,
        "remote_name": safe_text(payload.get("remoteName")),
        "device_type": safe_text(payload.get("deviceType")),
    }


def zero_conf_info(address, session=None):
    """Read GET /spotifyzc?action=getInfo with a bounded response, no redirects."""
    ip = lan_ipv4(address)
    if ip is None:
        return None, "Not an RFC1918 Sonos address"

    client = session if session is not None else requests.Session()
    if session is None:
        client.trust_env = False  # Do not route the LAN receiver through a proxy.
    try:
        with client.get(
            f"http://{ip}:1400/spotifyzc",
            params={"action": "getInfo"},
            timeout=(2, 4),
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.status_code != 200:
                return None, f"HTTP {response.status_code}"
            body = bytearray()
            for chunk in response.iter_content(4096):
                body.extend(chunk)
                if len(body) > MAX_REPLY_BYTES:
                    return None, "Response too large"
            try:
                payload = requests.models.complexjson.loads(body)
            except (ValueError, UnicodeDecodeError):
                return None, "Invalid JSON"
            details = extract_info(payload)
            if not details:
                return None, "Unexpected getInfo response"
            return details, None
    except requests.RequestException as error:
        return None, f"Request error: {type(error).__name__}"
    finally:
        if session is None:
            client.close()


def discover():
    """List LAN Sonos device IDs; never imply that a discovered ID is active."""
    speakers = soco.discover(timeout=5) or set()
    if not speakers:
        print("NO SONOS: SoCo did not discover any speakers on this network.")
        return 1

    for speaker in sorted(speakers, key=lambda item: item.player_name.casefold()):
        name = safe_text(speaker.player_name)
        address = speaker.ip_address
        if lan_ipv4(address) is None:
            print(f"SONOS: {name} | address unavailable or outside private LAN")
            continue

        try:
            transport = speaker.get_current_transport_info().get(
                "current_transport_state", "unknown"
            )
        except Exception:  # SoCo may fail independently of ZeroConf.
            transport = "unknown"
        details, error = zero_conf_info(address)
        print(f"SONOS: {name} | IPv4: {address} | state: {safe_text(transport)}")
        if error:
            print(f"  Connect ZeroConf: {error}; no device ID established")
        else:
            print(
                f"  Connect ZeroConf: remoteName={details['remote_name']}, "
                f"type={details['device_type']}, "
                f"deviceID={details['id'] or '<missing or non-hex>'}"
            )
    print("READ ONLY: no Spotify account, playback, or queue modifications.")
    print(
        "Do not use an ID to send a queue command until the active device "
        "is independently verified."
    )
    return 0


if __name__ == "__main__":
    sys.exit(discover())
