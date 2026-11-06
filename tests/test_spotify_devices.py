"""Fallback device choice should never target a restricted or unidentified player."""

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bjornify"))
select_fallback_device = importlib.import_module(
    "spotify_devices"
).select_fallback_device


class SpotifyDeviceSelectionTests(unittest.TestCase):
    """Spotify may provide several different Connect device types and IDs."""

    def test_preferred_available_device_wins(self):
        """Preferred device wins over an earlier eligible device."""
        response = {
            "devices": [
                {"name": "CarPlay", "type": "Smartphone", "id": "car"},
                {"name": "Everywhere", "type": "Speaker", "id": "preferred"},
            ]
        }
        self.assertEqual(
            select_fallback_device(response, "Everywhere")["id"], "preferred"
        )

    def test_restricted_preferred_is_skipped(self):
        """Do not send playback to a preferred device that rejects Web API calls."""
        response = {
            "devices": [
                {
                    "name": "Everywhere",
                    "type": "Speaker",
                    "id": "restricted",
                    "is_restricted": True,
                },
                {"name": "CarPlay", "type": "Smartphone", "id": "car"},
            ]
        }
        self.assertEqual(select_fallback_device(response, "Everywhere")["id"], "car")

    def test_restricted_first_is_skipped_when_preferred_missing(self):
        """Fallback chooses the first *eligible*, not simply the first device."""
        response = {
            "devices": [
                {"name": "Echo", "id": "echo", "is_restricted": True},
                {"name": "Sonos", "id": "sonos", "is_restricted": False},
            ]
        }
        self.assertEqual(select_fallback_device(response, "absent")["id"], "sonos")

    def test_missing_id_is_skipped(self):
        """Null and empty device IDs cannot be targeted without a session."""
        response = {
            "devices": [
                {"name": "Everywhere", "id": None},
                {"name": "Phone", "id": ""},
                {"name": "Computer", "id": "desktop"},
            ]
        }
        self.assertEqual(
            select_fallback_device(response, "Everywhere")["id"], "desktop"
        )

    def test_volume_and_active_flags_are_not_playability_flags(self):
        """Do not exclude offline-looking available devices or fixed-volume devices."""
        response = {
            "devices": [
                {
                    "name": "Speaker",
                    "id": "speaker",
                    "is_active": False,
                    "supports_volume": False,
                    "volume_percent": None,
                }
            ]
        }
        self.assertEqual(select_fallback_device(response, "No match")["id"], "speaker")

    def test_no_valid_device_returns_none(self):
        """An all-restricted or unidentified list is not a playable fallback."""
        response = {
            "devices": [
                {"name": "Restricted", "id": "x", "is_restricted": True},
                {"name": "Unknown", "id": None},
                None,
            ]
        }
        self.assertIsNone(select_fallback_device(response, "Restricted"))

    def test_missing_response_and_empty_list_return_none(self):
        """Spotify may return no device list or a null device collection."""
        for response in (None, {}, {"devices": None}, {"devices": []}):
            with self.subTest(response=response):
                self.assertIsNone(select_fallback_device(response, "Everywhere"))

    def test_missing_restriction_flag_is_not_assumed_restricted(self):
        """Old or partial API responses remain usable when they include an ID."""
        response = {"devices": [{"name": "Computer", "id": "desktop"}]}
        self.assertEqual(select_fallback_device(response, "Computer")["id"], "desktop")


if __name__ == "__main__":
    unittest.main()
