"""Tests for the isolated Spotify Connect device-code OAuth helper."""

import importlib.util
import io
import json
import os
import shutil
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PATH = Path(__file__).resolve().parents[1] / "bjornify" / "connect_playback_auth.py"
SPEC = importlib.util.spec_from_file_location("connect_playback_auth", PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load Connect device-code helper")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class Response:  # pylint: disable=too-few-public-methods
    """Small fake HTTP response; no real Spotify requests."""

    def __init__(self, payload, *, status=200):
        self.status_code = status
        self.payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        """Return the simulated JSON response."""
        return self.payload


class PlaybackDeviceAuthTests(unittest.TestCase):
    """Device-code auth and cached grants are separate from production OAuth."""

    def setUp(self):
        """Replace /tmp with a disposable private test cache."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.cache = Path(directory) / "playback.cache"
        original = helper.CACHE_PATH
        helper.CACHE_PATH = self.cache
        self.addCleanup(setattr, helper, "CACHE_PATH", original)

    def test_device_code_request_uses_only_streaming_and_no_client_secret(self):
        """Authorize requests only the playback streaming permission."""
        with patch.object(helper.requests, "post") as call:
            call.return_value = Response(
                {"device_code": "device-secret", "user_code": "ABC123"}
            )
            result = helper.spotify_post(
                helper.DEVICE_AUTHORIZE_URL,
                {"client_id": helper.PLAYBACK_CLIENT_ID, "scope": "streaming"},
            )
        self.assertEqual(result["user_code"], "ABC123")
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["data"]["scope"], "streaming")
        self.assertNotIn("client_secret", kwargs["data"])
        self.assertFalse(kwargs["allow_redirects"])

    def test_discord_pairing_reuses_safe_cli_device_authorization(self):
        """A valid public pairing code is returned without exposing any token."""
        reply = {
            "device_code": "private-device-secret",
            "user_code": "ABC123",
            "expires_in": 600,
            "interval": 5,
        }
        with patch.object(helper, "spotify_post", return_value=reply) as call:
            result = helper.begin_pairing()
        self.assertEqual(result, reply)
        call.assert_called_once_with(
            helper.DEVICE_AUTHORIZE_URL,
            {"client_id": helper.PLAYBACK_CLIENT_ID, "scope": "streaming"},
        )
        self.assertNotIn("access_token", result)

    def test_poll_pairing_exchanges_only_device_code(self):
        """The Discord poll uses the same fixed OAuth endpoint as the CLI."""
        with patch.object(
            helper, "spotify_post", return_value={"error": "authorization_pending"}
        ) as call:
            result = helper.poll_pairing("private-device-secret")
        self.assertEqual(result["error"], "authorization_pending")
        call.assert_called_once_with(
            helper.TOKEN_URL,
            {
                "client_id": helper.PLAYBACK_CLIENT_ID,
                "grant_type": helper.GRANT_TYPE,
                "device_code": "private-device-secret",
            },
        )

    def test_invalid_pairing_challenges_are_rejected(self):
        """Bad user codes and impossible polling windows cannot reach Discord."""
        with patch.object(helper, "spotify_post") as call:
            call.return_value = {
                "device_code": "secret",
                "user_code": "bad!markdown",
                "expires_in": 600,
                "interval": 5,
            }
            with self.assertRaises(ValueError):
                helper.begin_pairing()
            call.return_value["user_code"] = "ABC123"
            call.return_value["interval"] = -1
            with self.assertRaises(ValueError):
                helper.begin_pairing()

    def test_unknown_endpoint_is_refused_before_network_request(self):
        """Only Spotify OAuth endpoints can receive form submissions."""
        with (
            patch.object(helper.requests, "post") as call,
            self.assertRaises(ValueError),
        ):
            helper.spotify_post("https://malicious.invalid", {})
        call.assert_not_called()

    def test_server_errors_are_reduced_to_a_known_error_label(self):
        """Do not propagate untrusted error descriptions or OAuth secrets."""
        with patch.object(helper.requests, "post") as call:
            call.return_value = Response(
                {"error": "invalid_scope", "error_description": "secret"},
                status=400,
            )
            result = helper.spotify_post(helper.DEVICE_AUTHORIZE_URL, {})
        self.assertEqual(result, {"error": "invalid_scope"})
        self.assertNotIn("secret", repr(result))

    def test_invalid_reply_and_oversized_reply_are_refused(self):
        """Unexpected JSON or oversized replies cannot reach token storage."""
        with patch.object(helper.requests, "post") as call:
            call.return_value = Response(["invalid"])
            with self.assertRaises(TypeError):
                helper.spotify_post(helper.DEVICE_AUTHORIZE_URL, {})
            huge = Response({"access_token": "A" * 20000})
            call.return_value = huge
            with self.assertRaises(ValueError):
                helper.spotify_post(helper.TOKEN_URL, {})

    def test_grant_stored_private_and_without_production_token(self):
        """Token cache contains streaming grant only and is mode 0600."""
        helper.save_grant(
            {
                "access_token": "playback-access-secret",
                "refresh_token": "playback-refresh-secret",
                "expires_in": 3600,
                "scope": "streaming",
            }
        )
        self.assertEqual(os.stat(self.cache).st_mode & 0o777, 0o600)
        content = self.cache.read_text(encoding="utf-8")
        self.assertIn("playback-access-secret", content)
        self.assertNotIn("SPOTIPY", content)
        self.assertEqual(helper.read_grant()["scope"], "streaming")

    def test_missing_scope_or_refresh_is_not_cached(self):
        """Reject incomplete playback credentials."""
        for payload in (
            {"access_token": "secret", "expires_in": 3600, "scope": "streaming"},
            {
                "access_token": "secret",
                "refresh_token": "other-secret",
                "expires_in": 3600,
                "scope": "user-read-playback-state",
            },
        ):
            with self.assertRaises(ValueError):
                helper.save_grant(payload)
        self.assertFalse(self.cache.exists())

    def test_refresh_keeps_existing_refresh_token_when_not_rotated(self):
        """Token renewal remains isolated to the separate playback cache."""
        helper.save_grant(
            {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_in": 1,
                "scope": "streaming",
            }
        )
        with patch.object(helper, "spotify_post") as call:
            call.return_value = {
                "access_token": "new-access",
                "scope": "streaming",
                "expires_in": 3600,
            }
            result = helper.valid_grant()
        self.assertEqual(result["access_token"], "new-access")
        self.assertEqual(result["refresh_token"], "old-refresh")
        self.assertEqual(call.call_args.args[1]["grant_type"], "refresh_token")

    def test_pipe_refuses_terminal_output(self):
        """No access token may be pasted accidentally to a terminal."""
        with patch.object(helper.sys, "stdout", io.StringIO()) as output:
            output.isatty = lambda: True
            with self.assertRaises(ValueError):
                helper.pipe_token()
        self.assertEqual(output.getvalue(), "")

    def test_dual_pipe_contains_both_grants_without_refreshing_web_api_cache(self):
        """Read-only Web API grant and isolated playback grant stay independent."""
        manager = MagicMock()
        manager.cache_handler.get_cached_token.return_value = {
            "access_token": "personal-api-token",
            "scope": "user-read-playback-state",
            "expires_at": time.time() + 3600,
        }
        auth = types.ModuleType("spotify_auth")
        auth.make_auth_manager = MagicMock(return_value=manager)
        with (
            patch.dict(helper.sys.modules, {"spotify_auth": auth}),
            patch.object(
                helper,
                "valid_grant",
                return_value={"access_token": "playback-grant-token"},
            ),
            patch.object(helper.sys, "stdout", io.StringIO()) as output,
        ):
            helper.dual_tokens()
            payload = json.loads(output.getvalue())
        self.assertEqual(payload["web_api_token"], "personal-api-token")
        self.assertEqual(payload["playback_token"], "playback-grant-token")
        manager.get_access_token.assert_not_called()
        manager.cache_handler.get_cached_token.assert_called_once_with()

    def test_dual_pipe_refuses_expired_personal_cache_and_terminal_output(self):
        """Never silently refresh production credentials or print tokens to a TTY."""
        manager = MagicMock()
        manager.cache_handler.get_cached_token.return_value = {
            "access_token": "stale-web-token",
            "scope": "user-read-playback-state",
            "expires_at": time.time() - 1,
        }
        auth = types.ModuleType("spotify_auth")
        auth.make_auth_manager = MagicMock(return_value=manager)
        with (
            patch.dict(helper.sys.modules, {"spotify_auth": auth}),
            patch.object(
                helper,
                "valid_grant",
                return_value={"access_token": "playback-grant-token"},
            ),
            patch.object(helper.sys, "stdout", io.StringIO()) as output,
        ):
            with self.assertRaises(ValueError):
                helper.dual_tokens()
            self.assertEqual(output.getvalue(), "")
        manager.get_access_token.assert_not_called()

        with patch.object(helper.sys, "stdout", io.StringIO()) as output:
            output.isatty = lambda: True
            with self.assertRaises(ValueError):
                helper.dual_tokens()
            self.assertEqual(output.getvalue(), "")

    def test_client_id_is_public_but_not_a_playback_secret(self):
        """The desktop client ID is independent of the user's Web API app."""
        self.assertEqual(len(helper.PLAYBACK_CLIENT_ID), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in helper.PLAYBACK_CLIENT_ID))


if __name__ == "__main__":
    unittest.main()
