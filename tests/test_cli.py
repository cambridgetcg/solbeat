from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOLBEAT = REPO_ROOT / "bin" / "solbeat"


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "state"

    def run_cli(
        self, *arguments: str, expected: int = 0
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(SOLBEAT), "--home", str(self.home), *arguments],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def test_gate_stdout_is_exactly_one_json_line(self) -> None:
        result = self.run_cli("gate")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(result.stdout), {"reason": "no_events", "wakeAgent": False}
        )
        self.assertEqual(result.stderr, "")

    def test_emit_does_not_echo_input(self) -> None:
        marker = "private-marker-that-must-not-echo"
        result = self.run_cli(
            "emit",
            "--source",
            "test",
            "--kind",
            "signal",
            "--summary",
            marker,
            "--payload-json",
            json.dumps({"secret": marker}),
        )
        self.assertNotIn(marker, result.stdout)
        self.assertNotIn(marker, result.stderr)
        self.assertTrue(json.loads(result.stdout)["inserted"])

    def test_install_hermes_creates_physical_owner_only_script(self) -> None:
        hermes_home = self.root / "hermes-profile"
        result = self.run_cli("install-hermes", "--hermes-home", str(hermes_home))
        target = Path(json.loads(result.stdout)["physical_script"])
        self.assertTrue(target.is_file())
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.stat().st_mode & 0o077, 0)
        first = target.read_text(encoding="utf-8")
        shebang = first.splitlines()[0]
        self.assertTrue(shebang.startswith("#!/"))
        self.assertNotIn("/usr/bin/env", shebang)
        self.assertTrue(Path(shebang[2:]).is_file())
        self.run_cli("install-hermes", "--hermes-home", str(hermes_home))
        self.assertEqual(target.read_text(encoding="utf-8"), first)

    def test_missing_lease_is_nonzero_without_content(self) -> None:
        result = self.run_cli("ack", "0" * 32, expected=3)
        self.assertEqual(json.loads(result.stdout)["handled"], 0)
        self.assertEqual(result.stderr, "")

    def test_timed_pause_reports_deadline_and_resume_clears_it(self) -> None:
        paused = json.loads(
            self.run_cli(
                "pause",
                "--for-minutes",
                "5",
                "--reason",
                "recharging",
            ).stdout
        )
        self.assertTrue(paused["paused"])
        self.assertIsNotNone(paused["paused_until"])
        status = json.loads(self.run_cli("status").stdout)
        self.assertTrue(status["paused"])
        self.assertEqual(status["paused_until"], paused["paused_until"])

        resumed = json.loads(self.run_cli("resume").stdout)
        self.assertFalse(resumed["paused"])
        self.assertIsNone(resumed["paused_until"])
        self.assertFalse(json.loads(self.run_cli("status").stdout)["paused"])


if __name__ == "__main__":
    unittest.main()
