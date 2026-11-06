"""No-credentials tests for the isolated Spotify Connect OAuth experiment."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bjornify"))
MODULE_PATH = ROOT / "experiments" / "connect-probe" / "oauth_probe.py"
SPEC = importlib.util.spec_from_file_location("connect_oauth_probe", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load the isolated Spotify Connect OAuth probe")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ConnectOAuthProbeTests(unittest.TestCase):
    """Keep the experiment read-only and isolated from production tokens."""

    def test_streaming_scope_is_added_without_dropping_existing_scopes(self):
        """The probe requests streaming on the existing Spotify application."""
        self.assertEqual(probe.PROBE_SCOPES, [*probe.SCOPE, "streaming"])
        self.assertNotEqual(probe.PROBE_SCOPES, probe.SCOPE)

    def test_manager_uses_existing_app_with_separate_cache(self):
        """Probe token must never replace /app/secrets/spotipy_token.cache."""
        with (
            patch.dict(
                os.environ,
                {
                    "SPOTIPY_CLIENT_ID": "existing-client",
                    "SPOTIPY_CLIENT_SECRET": "test-value",
                },
            ),
            patch.object(probe, "HeadlessSpotifyOAuth") as manager,
        ):
            probe.make_probe_manager(state="state")
        kwargs = manager.call_args.kwargs
        self.assertEqual(kwargs["client_id"], "existing-client")
        self.assertEqual(kwargs["scope"], probe.PROBE_SCOPES)
        self.assertEqual(kwargs["state"], "state")
        self.assertNotEqual(
            kwargs["cache_handler"].cache_path,
            "/app/secrets/spotipy_token.cache",
        )
        self.assertEqual(kwargs["cache_handler"].cache_path, str(probe.CACHE_PATH))

    def test_missing_streaming_scope_is_rejected(self):
        """A standard Björnify grant is insufficient for Connect login."""
        manager = MagicMock()
        manager.get_access_token.return_value = {
            "access_token": "private",
            "scope": "user-modify-playback-state",
        }
        with self.assertRaisesRegex(ValueError, "no 'streaming' scope"):
            probe.read_token(manager)

    def test_streaming_grant_is_accepted(self):
        """A granted streaming scope is required even in the read-only probe."""
        manager = MagicMock()
        token = {
            "access_token": "private",
            "scope": "user-modify-playback-state streaming",
        }
        manager.get_access_token.return_value = token
        self.assertIs(probe.read_token(manager), token)

    def test_refuse_to_print_access_token_to_terminal(self):
        """Never print a Spotify secret when a user omits the pipe."""
        with (
            patch.object(probe.sys, "stdout", MagicMock(isatty=lambda: True)),
            patch.object(probe, "make_probe_manager") as manager,
            self.assertRaisesRegex(ValueError, "Refusing to print"),
        ):
            probe.pipe_token()
        manager.assert_not_called()

    def test_status_without_cache_requires_authorization(self):
        """An absent experimental cache is harmless to the running bot."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(probe, "CACHE_PATH", Path(directory) / "missing"),
        ):
            self.assertEqual(probe.status(), 1)

    def test_incorrect_callback_never_exchanges_authorization_code(self):
        """Reject URLs for a different hostname before parsing their code."""
        manager = MagicMock(redirect_uri="https://spotify.example/spotify/callback")
        with (
            patch.object(probe.sys, "stdin", MagicMock(isatty=lambda: True)),
            patch.object(probe, "make_probe_manager", return_value=manager),
            patch.object(
                probe.getpass,
                "getpass",
                return_value="https://other.example/spotify/callback?code=secret",
            ),
            self.assertRaisesRegex(ValueError, "does not match"),
        ):
            probe.authorize()
        manager.get_access_token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
