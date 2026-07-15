"""Configuration loading and validation for SolBeat."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "solbeat.toml"
DEFAULT_HOME = Path("~/.hermes/profiles/sol/solbeat").expanduser()


@dataclass(frozen=True)
class Policy:
    timezone: str = "Europe/London"
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    urgent_priority: int = 90
    min_wake_interval_seconds: int = 900
    lease_seconds: int = 1800
    batch_size: int = 8
    max_attempts: int = 5
    retry_base_seconds: int = 300
    retry_max_seconds: int = 14_400
    summary_max_chars: int = 500
    payload_max_bytes: int = 8_192
    checkin_enabled: bool = False
    checkin_interval_seconds: int = 86_400
    checkin_initial_delay_seconds: int = 3_600
    checkin_summary: str = "Gentle SolBeat check-in: inspect SolBeat health and surface at most one genuinely useful observation."

    def validate(self) -> None:
        text_values = {
            "timezone": self.timezone,
            "quiet_start": self.quiet_start,
            "quiet_end": self.quiet_end,
            "checkin_summary": self.checkin_summary,
        }
        invalid_text = [
            name for name, value in text_values.items() if not isinstance(value, str)
        ]
        if invalid_text:
            raise ValueError(
                f"configuration values must be strings: {', '.join(invalid_text)}"
            )
        if not isinstance(self.checkin_enabled, bool):
            raise ValueError("checkin_enabled must be true or false")
        integer_values = {
            "urgent_priority": self.urgent_priority,
            "min_wake_interval_seconds": self.min_wake_interval_seconds,
            "lease_seconds": self.lease_seconds,
            "batch_size": self.batch_size,
            "max_attempts": self.max_attempts,
            "retry_base_seconds": self.retry_base_seconds,
            "retry_max_seconds": self.retry_max_seconds,
            "summary_max_chars": self.summary_max_chars,
            "payload_max_bytes": self.payload_max_bytes,
            "checkin_interval_seconds": self.checkin_interval_seconds,
            "checkin_initial_delay_seconds": self.checkin_initial_delay_seconds,
        }
        invalid_integer = [
            name
            for name, value in integer_values.items()
            if not isinstance(value, int) or isinstance(value, bool)
        ]
        if invalid_integer:
            raise ValueError(
                f"configuration values must be integers: {', '.join(invalid_integer)}"
            )
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc
        for label, value in (
            ("quiet_start", self.quiet_start),
            ("quiet_end", self.quiet_end),
        ):
            if re.fullmatch(r"[0-9]{2}:[0-9]{2}", value, flags=re.ASCII) is None:
                raise ValueError(f"{label} must be HH:MM")
            parts = value.split(":")
            hour, minute = (int(part) for part in parts)
            if hour > 23 or minute > 59:
                raise ValueError(f"{label} must be HH:MM")
        if not 0 <= self.urgent_priority <= 100:
            raise ValueError("urgent_priority must be between 0 and 100")
        positive = {
            "min_wake_interval_seconds": self.min_wake_interval_seconds,
            "lease_seconds": self.lease_seconds,
            "batch_size": self.batch_size,
            "max_attempts": self.max_attempts,
            "retry_base_seconds": self.retry_base_seconds,
            "retry_max_seconds": self.retry_max_seconds,
            "summary_max_chars": self.summary_max_chars,
            "payload_max_bytes": self.payload_max_bytes,
            "checkin_interval_seconds": self.checkin_interval_seconds,
            "checkin_initial_delay_seconds": self.checkin_initial_delay_seconds,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(
                f"configuration values must be positive: {', '.join(invalid)}"
            )
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_max_seconds must be >= retry_base_seconds")
        if self.summary_max_chars < 32:
            raise ValueError("summary_max_chars must be at least 32")
        if self.payload_max_bytes < 128:
            raise ValueError("payload_max_bytes must be at least 128")


@dataclass(frozen=True)
class Settings:
    home: Path
    database: Path
    policy: Policy
    config_path: Path


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"configuration file does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML in {path}: {exc}") from exc


def load_settings(
    *, config_path: str | Path | None = None, home: str | Path | None = None
) -> Settings:
    selected_config = (
        Path(config_path or os.environ.get("SOLBEAT_CONFIG") or DEFAULT_CONFIG)
        .expanduser()
        .resolve()
    )
    document = _read_toml(selected_config)
    state_document = document.get("state", {})
    if not isinstance(state_document, dict):
        raise ValueError("state must be a TOML table")
    configured_home = state_document.get("home", str(DEFAULT_HOME))
    if not isinstance(configured_home, str):
        raise ValueError("state.home must be a string")
    selected_home = (
        Path(home or os.environ.get("SOLBEAT_HOME") or configured_home)
        .expanduser()
        .resolve()
    )
    policy_document = document.get("policy", {})
    checkin_document = document.get("checkin", {})
    if not isinstance(policy_document, dict) or not isinstance(checkin_document, dict):
        raise ValueError("policy and checkin must be TOML tables")
    policy_values = policy_document | checkin_document
    allowed = set(Policy.__dataclass_fields__)
    unknown = sorted(set(policy_values) - allowed)
    if unknown:
        raise ValueError(f"unknown policy keys: {', '.join(unknown)}")
    policy = Policy(**policy_values)
    policy.validate()
    return Settings(
        home=selected_home,
        database=selected_home / "state.sqlite3",
        policy=policy,
        config_path=selected_config,
    )
