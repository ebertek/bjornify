"""Regression tests for the single administrator CLI.

All Spotify, Sonos and OAuth operations are mocked; no live accounts required.
"""

import contextlib
import importlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bjornify"))
cli = importlib.import_module("cli")


class UnifiedCliTests(unittest.TestCase):
    """Test CLI dispatch and privacy without using any external services."""

    def test_spotify_diagnostics_default_and_full(self):
        """Route both modes to the existing Spotify authorization module."""
        with patch.object(cli.spotify_auth, "main", return_value=0) as main:
            self.assertEqual(cli.main(["spotify", "diagnostics"]), 0)
            main.assert_called_once_with(["diagnostics"])
            main.reset_mock()
            self.assertEqual(cli.main(["spotify", "diagnostics", "--full"]), 0)
            main.assert_called_once_with(["diagnostics", "--full"])

    def test_spotify_authorization_preserves_interactive_handler(self):
        """Do not duplicate Web API authorization or weaken its safeguards."""
        with patch.object(cli.spotify_auth, "main", return_value=0) as main:
            self.assertEqual(cli.main(["spotify", "authorize"]), 0)
            main.assert_called_once_with(["authorize"])

    def test_connect_commands_preserve_existing_handlers(self):
        """Token-pipe operations still use the original TTY protection."""
        with patch.object(cli.connect_playback_auth, "main", return_value=0) as main:
            for operation in ("authorize", "status", "pipe-token", "pipe-dual"):
                self.assertEqual(cli.main(["connect", operation]), 0)
                main.assert_called_with([operation])
            self.assertEqual(main.call_count, 4)

    def test_sonos_discovery_hides_addresses_by_default(self):
        """Sonos information stays readable without leaking private network data."""
        speaker = MagicMock()
        speaker.player_name = "Test Room"
        speaker.ip_address = "192.0.2.10"
        speaker.get_current_transport_info.return_value = {
            "current_transport_state": "PLAYING"
        }
        with (
            patch.object(cli.soco, "discover", return_value={speaker}) as discover,
            io.StringIO() as output,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.main(["sonos", "devices"]), 0)
            report = json.loads(output.getvalue())
        self.assertEqual(report, [{"name": "Test Room", "state": "PLAYING"}])
        discover.assert_called_once_with(timeout=3)

    def test_sonos_full_explicitly_includes_address(self):
        """Full mode is a deliberate local-only opt-in."""
        speaker = MagicMock()
        speaker.player_name = "Test Room"
        speaker.ip_address = "192.0.2.10"
        speaker.get_current_transport_info.return_value = {}
        with (
            patch.object(cli.soco, "discover", return_value={speaker}),
            io.StringIO() as output,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.main(["sonos", "devices", "--full"]), 0)
            report = json.loads(output.getvalue())
        self.assertEqual(report[0]["ip_address"], "192.0.2.10")

    def test_missing_subcommand_is_rejected(self):
        """Do not run any default maintenance or authorization action."""
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            cli.main(["connect"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
