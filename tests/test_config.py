from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from solbeat.config import DEFAULT_CONFIG, load_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
DEVICE_CONFIG = REPO_ROOT / "config" / "solbeat.toml"


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def load_document(self, document: str):
        path = self.root / "solbeat.toml"
        path.write_text(document, encoding="utf-8")
        return load_settings(config_path=path, home=self.root / "state")

    def test_packaged_default_is_neutral(self) -> None:
        with mock.patch.dict(os.environ, {"SOLBEAT_HOME": ""}):
            settings = load_settings(config_path=DEFAULT_CONFIG)
        self.assertFalse(settings.policy.checkin_enabled)
        self.assertEqual(settings.policy.timezone, "UTC")
        self.assertEqual(
            settings.home,
            Path("~/.local/state/solbeat").expanduser().resolve(),
        )

    def test_device_configuration_is_valid(self) -> None:
        with mock.patch.dict(os.environ, {"SOLBEAT_HOME": ""}):
            settings = load_settings(config_path=DEVICE_CONFIG)
        self.assertTrue(settings.policy.checkin_enabled)
        self.assertEqual(settings.policy.timezone, "Europe/London")
        self.assertEqual(
            settings.home,
            Path("~/.hermes/profiles/sol/solbeat").expanduser().resolve(),
        )

    def test_unknown_top_level_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "unknown top-level configuration keys: polciy"
        ):
            self.load_document(
                """
[polciy]
quiet_start = "12:34"
"""
            )

    def test_unknown_state_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, r"unknown \[state\] keys: hmoe"):
            self.load_document(
                """
[state]
hmoe = "/tmp/typo"
"""
            )

    def test_policy_key_under_checkin_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, r"unknown \[checkin\] keys: batch_size"):
            self.load_document(
                """
[checkin]
batch_size = 17
"""
            )

    def test_checkin_key_under_policy_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError, r"unknown \[policy\] keys: checkin_enabled"
        ):
            self.load_document(
                """
[policy]
checkin_enabled = true
"""
            )


if __name__ == "__main__":
    unittest.main()
