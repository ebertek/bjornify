"""Tests for read-only Sonos Connect ID discovery (no LAN requests)."""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "connect-probe"
    / "sonos_discovery.py"
)
SPEC = importlib.util.spec_from_file_location("connect_sonos_discovery", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load Sonos discovery helper")
discovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discovery)


class FakeReply:
    """Streaming HTTP response with a bounded JSON reply."""

    def __init__(self, payload=None, *, status=200, body=None):
        self.status_code = status
        self.body = json.dumps(payload).encode() if body is None else body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, _size):
        yield self.body


class SonosDiscoveryTests(unittest.TestCase):
    """Never treat an unverified remote device as a writable queue target."""

    def test_only_rfc1918_ips_can_be_requested(self):
        """Reject public, loopback and hostname HTTP targets."""
        self.assertEqual(discovery.lan_ipv4("192.168.2.47"), "192.168.2.47")
        for ip in (
            "127.0.0.1",
            "169.254.0.10",
            "8.8.8.8",
            "localhost",
            "192.0.2.1",
            "::1",
        ):
            self.assertIsNone(discovery.lan_ipv4(ip))

    def test_only_intended_non_secret_fields_are_extracted(self):
        """Only the public receiver identity may leave the ZeroConf reply."""
        info = discovery.extract_info(
            {
                "remoteName": "Test speaker",
                "deviceType": "SPEAKER",
                "deviceID": "0123456789abcdef0123456789abcdef01234567",
                "activeUser": "private-account",
                "publicKey": "DO_NOT_PRINT",
                "tokenType": "DO_NOT_PRINT",
            }
        )
        self.assertEqual(
            info,
            {
                "remote_name": "Test speaker",
                "device_type": "SPEAKER",
                "id": "0123456789abcdef0123456789abcdef01234567",
            },
        )
        self.assertNotIn("private-account", repr(info))
        self.assertNotIn("DO_NOT_PRINT", repr(info))

    def test_invalid_ids_are_not_treated_as_connect_device_ids(self):
        """Reject malformed and missing Connect device identifiers."""
        for device_id in (None, "bogus", "fake/id/too/short", "AA\\n", ""):
            self.assertIsNone(discovery.extract_info({"deviceID": device_id})["id"])

    def test_getinfo_is_read_only_and_never_follows_redirects(self):
        """Read only the intended endpoint without redirects or secret output."""
        client = MagicMock()
        client.get.return_value = FakeReply(
            {
                "deviceID": "0123456789abcdef0123456789abcdef01234567",
                "remoteName": "Test speaker",
                "publicKey": "SECRET",
            }
        )
        info, error = discovery.zero_conf_info("192.168.2.47", session=client)
        self.assertIsNone(error)
        self.assertEqual(info["remote_name"], "Test speaker")
        self.assertEqual(info["id"], "0123456789abcdef0123456789abcdef01234567")
        _args, kwargs = client.get.call_args
        self.assertEqual(_args[0], "http://192.168.2.47:1400/spotifyzc")
        self.assertEqual(kwargs["params"], {"action": "getInfo"})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("SECRET", repr(info))

    def test_public_address_never_triggers_http(self):
        """Do not send HTTP requests to public IP addresses."""
        client = MagicMock()
        info, error = discovery.zero_conf_info("1.1.1.1", session=client)
        self.assertIsNone(info)
        self.assertIsNotNone(error)
        client.get.assert_not_called()

    def test_oversized_response_is_rejected_without_disclosing_body(self):
        """Bound untrusted Sonos response size and never print its contents."""
        client = MagicMock()
        client.get.return_value = FakeReply(body=b"A" * 20000)
        info, error = discovery.zero_conf_info("192.168.2.47", session=client)
        self.assertIsNone(info)
        self.assertEqual(error, "Response too large")


if __name__ == "__main__":
    unittest.main()
