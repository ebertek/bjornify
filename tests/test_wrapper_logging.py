"""Verify that shell startup logs match the Python ISO timestamp schema."""

import json
import os
import subprocess
import unittest
from datetime import datetime
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[1] / "bjornify" / "wrapper.sh"


class WrapperLoggingTests(unittest.TestCase):
    """Test only the wrapper's logging functions, without starting bot processes."""

    def test_json_timestamp_includes_timezone_offset(self):
        """Shell and Python records both have parseable offset-aware timestamps."""
        script = WRAPPER.read_text(encoding="utf-8").split("PID_FILE=", maxsplit=1)[0]
        script += "\nlog INFO 'Starting bjornify.py...'\n"
        result = subprocess.run(
            ["bash", "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "LOG_FORMAT": "json",
                "LOG_LEVEL": "INFO",
                "TZ": "Europe/Stockholm",
            },
        )
        record = json.loads(result.stdout.strip())
        self.assertEqual(record["logger"], "wrapper.sh")
        self.assertEqual(record["level"], "INFO")
        self.assertEqual(record["message"], "Starting bjornify.py...")
        self.assertRegex(
            record["timestamp"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}[+-]\d{2}:\d{2}$",
        )
        self.assertIsNotNone(datetime.fromisoformat(record["timestamp"]).tzinfo)


if __name__ == "__main__":
    unittest.main()
