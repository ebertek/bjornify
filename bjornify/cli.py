"""Single administrator-facing CLI for Björnify.

Keep credentials, Spotify API behavior, and Sonos discovery in their own
modules. This file only routes explicit commands; it never starts the bots.
"""

import argparse
import json
import sys

import connect_playback_auth
import requests
import soco
import spotify_auth


def sonos_devices(full=False):
    """Discover Sonos rooms with a small read-only, privacy-conscious report."""
    try:
        speakers = soco.discover(timeout=3) or set()
    except (OSError, requests.RequestException, soco.exceptions.SoCoException):
        print("Sonos discovery failed.", file=sys.stderr)
        return 1

    rooms = []
    for speaker in speakers:
        try:
            room = {
                "name": speaker.player_name,
                "state": speaker.get_current_transport_info().get(
                    "current_transport_state", "unknown"
                ),
            }
            if full:
                room["ip_address"] = speaker.ip_address
            rooms.append(room)
        except (
            OSError,
            ValueError,
            requests.RequestException,
            soco.exceptions.SoCoException,
        ):
            # A single unreachable room must not hide the other discovered ones.
            rooms.append({"name": "unavailable", "state": "unreachable"})

    print(json.dumps(sorted(rooms, key=lambda room: room["name"]), indent=2))
    return 0


def build_parser():
    """Describe supported commands; never expose credential material in help."""
    parser = argparse.ArgumentParser(
        prog="bjornify",
        description="Björnify administrator CLI (run inside the container)",
    )
    groups = parser.add_subparsers(dest="group", required=True)

    spotify = groups.add_parser("spotify", help="Personal Spotify Web API")
    spotify_commands = spotify.add_subparsers(dest="command", required=True)
    spotify_commands.add_parser("authorize", help="Interactive Web API OAuth")
    spotify_diagnostics = spotify_commands.add_parser(
        "diagnostics", help="Available devices and current playback"
    )
    spotify_diagnostics.add_argument(
        "--full", action="store_true", help="Include private Spotify device IDs"
    )

    connect = groups.add_parser("connect", help="Spotify Connect playback grant")
    connect_commands = connect.add_subparsers(dest="command", required=True)
    connect_commands.add_parser("authorize", help="Interactive playback pairing")
    connect_commands.add_parser("status", help="Playback grant status (no tokens)")
    connect_commands.add_parser("client-id", help="Public Spotify client ID")

    sonos = groups.add_parser("sonos", help="Sonos discovery")
    sonos_commands = sonos.add_subparsers(dest="command", required=True)
    sonos_discovery = sonos_commands.add_parser(
        "devices", help="Read-only Sonos room discovery"
    )
    sonos_discovery.add_argument(
        "--full", action="store_true", help="Include private room IP addresses"
    )
    return parser


def main(argv=None):
    """Dispatch an explicit command without duplicating auth/error handling."""
    parsed = build_parser().parse_args(argv)
    if parsed.group == "spotify":
        args = [parsed.command]
        if parsed.command == "diagnostics" and parsed.full:
            args.append("--full")
        return spotify_auth.main(args)

    if parsed.group == "connect":
        return connect_playback_auth.main([parsed.command])

    if parsed.group == "sonos":
        return sonos_devices(full=parsed.full)

    return 2


if __name__ == "__main__":
    sys.exit(main())
