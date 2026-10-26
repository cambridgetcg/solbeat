from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = REPO_ROOT / "src" / "solbeat"
SOLBEAT = REPO_ROOT / "bin" / "solbeat"


class PackagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_shipped_codex_config_leaves_approval_to_the_runner(self) -> None:
        with (REPO_ROOT / ".codex" / "config.toml").open("rb") as handle:
            config = tomllib.load(handle)
        self.assertNotIn("approval_policy", config)
        self.assertEqual(config["sandbox_mode"], "workspace-write")
        self.assertIs(config["sandbox_workspace_write"]["network_access"], False)
        self.assertEqual(config["web_search"], "disabled")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("never waits for an interactive approval", readme)

    def test_default_toml_is_declared_as_package_data(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        package_data = project["tool"]["setuptools"]["package-data"]
        self.assertIn("default.toml", package_data["solbeat"])
        with (SOURCE_PACKAGE / "default.toml").open("rb") as handle:
            default = tomllib.load(handle)
        self.assertNotIn("state", default)
        self.assertEqual(default["policy"], {"timezone": "UTC"})
        self.assertEqual(default["checkin"], {"checkin_enabled": False})

    def test_isolated_package_copy_uses_neutral_default(self) -> None:
        site_packages = self.root / "site-packages"
        shutil.copytree(SOURCE_PACKAGE, site_packages / "solbeat")
        state_home = self.root / "state"
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(site_packages),
        }
        environment.pop("SOLBEAT_CONFIG", None)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "solbeat",
                "--home",
                str(state_home),
                "gate",
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {"reason": "no_events", "wakeAgent": False},
        )
        self.assertEqual(result.stderr, "")

        hermes_home = self.root / "hermes-profile"
        installed = subprocess.run(
            [
                sys.executable,
                "-m",
                "solbeat",
                "--home",
                str(state_home),
                "install-hermes",
                "--hermes-home",
                str(hermes_home),
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        target = Path(json.loads(installed.stdout)["physical_script"])
        self.assertTrue(target.is_file())
        self.assertEqual(
            target.read_text(encoding="utf-8").splitlines()[0],
            f"#!{Path(sys.executable).resolve()}",
        )

        clean_environment = {
            **os.environ,
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        clean_environment.pop("PYTHONPATH", None)
        clean_environment.pop("SOLBEAT_CONFIG", None)
        clean_environment.pop("SOLBEAT_HOME", None)
        shim = subprocess.run(
            [str(target)],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
            env=clean_environment,
        )
        self.assertEqual(shim.returncode, 0, shim.stderr)
        self.assertEqual(len(shim.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(shim.stdout),
            {"reason": "no_events", "wakeAgent": False},
        )
        self.assertEqual(shim.stderr, "")

    def test_source_wrapper_pins_device_configuration(self) -> None:
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOLBEAT_CONFIG": str(self.root / "missing.toml"),
            "SOLBEAT_HOME": str(self.root / "ambient-state"),
        }
        result = subprocess.run(
            [
                str(SOLBEAT),
                "--home",
                str(self.root / "wrapper-state"),
                "gate",
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {"reason": "no_events", "wakeAgent": False},
        )
        self.assertEqual(result.stderr, "")

    def test_source_wrapper_ignores_ambient_routing_but_allows_cli_override(
        self,
    ) -> None:
        configured_home = self.root / "configured-state"
        explicit_home = self.root / "explicit-state"
        ambient_home = self.root / "ambient-state"
        config_path = self.root / "explicit.toml"
        config_path.write_text(
            f"[state]\nhome = {json.dumps(str(configured_home))}\n",
            encoding="utf-8",
        )
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOLBEAT_CONFIG": str(self.root / "missing.toml"),
            "SOLBEAT_HOME": str(ambient_home),
        }

        configured = subprocess.run(
            [str(SOLBEAT), "--config", str(config_path), "init"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertTrue((configured_home / "state.sqlite3").is_file())
        self.assertFalse(ambient_home.exists())

        explicit = subprocess.run(
            [
                str(SOLBEAT),
                "--config",
                str(config_path),
                "--home",
                str(explicit_home),
                "init",
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertTrue((explicit_home / "state.sqlite3").is_file())
        self.assertFalse(ambient_home.exists())


if __name__ == "__main__":
    unittest.main()
