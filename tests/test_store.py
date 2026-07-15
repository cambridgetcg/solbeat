from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from solbeat.config import Policy, Settings
from solbeat.security import redact_text, redact_value
from solbeat.store import EventStore, SchemaVersionError


def epoch(hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 1, 2, hour, minute, tzinfo=timezone.utc).timestamp())


class EventStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = replace(
            Policy(),
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="08:00",
            min_wake_interval_seconds=60,
            lease_seconds=10,
            retry_base_seconds=5,
            retry_max_seconds=20,
        )
        self.settings = Settings(
            home=self.root / "state",
            database=self.root / "state" / "state.sqlite3",
            policy=self.policy,
            config_path=self.root / "unused.toml",
        )
        self.store = EventStore(self.settings)
        self.store.initialize()

    def _emit(self, *, now: int, priority: int = 50, dedupe: str = "one") -> str:
        event_id, inserted = self.store.emit(
            source="test",
            kind="signal",
            summary="A useful local signal",
            priority=priority,
            dedupe_key=dedupe,
            now=now,
        )
        self.assertTrue(inserted)
        return event_id

    def test_empty_gate_is_silent(self) -> None:
        result = self.store.gate(now=epoch(12))
        self.assertFalse(result.wake_agent)
        self.assertEqual(result.reason, "no_events")

    def test_active_event_is_deduplicated_but_completed_event_can_recur(self) -> None:
        first, inserted = self.store.emit(
            source="test",
            kind="signal",
            summary="same",
            dedupe_key="stable",
            now=epoch(12),
        )
        duplicate, inserted_again = self.store.emit(
            source="test",
            kind="signal",
            summary="changed wording",
            dedupe_key="stable",
            now=epoch(12),
        )
        self.assertFalse(inserted_again)
        self.assertEqual(duplicate, first)
        lease = self.store.gate(now=epoch(12)).context["lease_id"]
        self.assertEqual(self.store.ack(lease, now=epoch(12) + 1).completed, 1)
        recurring, recurring_inserted = self.store.emit(
            source="test",
            kind="signal",
            summary="same again later",
            dedupe_key="stable",
            now=epoch(12) + 61,
        )
        self.assertTrue(recurring_inserted)
        self.assertNotEqual(recurring, first)

    def test_payload_is_redacted_and_never_exposed_by_gate(self) -> None:
        raw_secret = "sk-example-super-secret-value"
        self.store.emit(
            source="test",
            kind="sensitive",
            summary=f"token={raw_secret} ready",
            payload={"api_key": raw_secret, "nested": f"Bearer {raw_secret}"},
            now=epoch(12),
        )
        with sqlite3.connect(self.settings.database) as connection:
            summary, payload_json = connection.execute(
                "SELECT summary, payload_json FROM events"
            ).fetchone()
        self.assertNotIn(raw_secret, summary)
        self.assertNotIn(raw_secret, payload_json)
        result_json = json.dumps(self.store.gate(now=epoch(12)).as_dict())
        self.assertNotIn(raw_secret, result_json)
        self.assertNotIn("payload_json", result_json)
        self.assertNotIn("api_key", result_json)

    def test_common_header_camel_case_and_account_identifiers_are_redacted(
        self,
    ) -> None:
        markers = (
            "client_secret=alpha-private-value",
            "access_token=beta-private-value",
            "Authorization: Basic encoded-private-value",
            "Cookie: sid=cookie-private-value; preference=also-private",
            "12345678901@s.whatsapp.net",
            "+447700900123",
        )
        cleaned = redact_text("\n".join(markers), max_chars=2_000)
        for secret in (
            "alpha-private-value",
            "beta-private-value",
            "encoded-private-value",
            "cookie-private-value",
            "also-private",
            "12345678901",
            "447700900123",
        ):
            self.assertNotIn(secret, cleaned)
        value = redact_value(
            {
                "accessToken": "camel-access-private",
                "clientSecret": "camel-client-private",
                "chatId": "account-private",
            }
        )
        serialized = json.dumps(value)
        self.assertNotIn("camel-access-private", serialized)
        self.assertNotIn("camel-client-private", serialized)
        self.assertNotIn("account-private", serialized)

    def test_numeric_labels_are_rejected_as_possible_account_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "ASCII letter"):
            self.store.emit(
                source="1234567890",
                kind="signal",
                summary="not accepted",
                now=epoch(12),
            )

    def test_quiet_hours_block_normal_but_not_urgent(self) -> None:
        self._emit(now=epoch(23, 30), dedupe="normal")
        blocked = self.store.gate(now=epoch(23, 30))
        self.assertFalse(blocked.wake_agent)
        self.assertEqual(blocked.reason, "quiet_hours")
        self._emit(now=epoch(23, 30), priority=95, dedupe="urgent")
        urgent = self.store.gate(now=epoch(23, 30))
        self.assertTrue(urgent.wake_agent)
        self.assertEqual(
            [event["priority"] for event in urgent.context["events"]], [95]
        )

    def test_rate_limit_is_bypassed_only_by_urgent_events(self) -> None:
        self._emit(now=epoch(12), dedupe="first")
        first = self.store.gate(now=epoch(12))
        self.store.ack(first.context["lease_id"], now=epoch(12) + 1)
        self._emit(now=epoch(12) + 10, dedupe="normal")
        limited = self.store.gate(now=epoch(12) + 10)
        self.assertFalse(limited.wake_agent)
        self.assertEqual(limited.reason, "rate_limited")
        self._emit(now=epoch(12) + 10, priority=99, dedupe="urgent")
        urgent = self.store.gate(now=epoch(12) + 10)
        self.assertTrue(urgent.wake_agent)
        self.assertEqual(len(urgent.context["events"]), 1)
        self.assertEqual(urgent.context["events"][0]["priority"], 99)

    def test_pending_duplicate_promotes_priority_and_availability(self) -> None:
        self.store.emit(
            source="test",
            kind="signal",
            summary="low and delayed",
            priority=10,
            dedupe_key="promote",
            delay_seconds=3_600,
            now=epoch(12),
        )
        _, inserted = self.store.emit(
            source="test",
            kind="signal",
            summary="now urgent",
            priority=99,
            dedupe_key="promote",
            now=epoch(12),
        )
        self.assertFalse(inserted)
        result = self.store.gate(now=epoch(12))
        self.assertTrue(result.wake_agent)
        self.assertEqual(result.context["events"][0]["priority"], 99)
        self.assertEqual(result.context["events"][0]["summary"], "now urgent")

    def test_duplicate_arriving_during_lease_is_requeued_after_ack(self) -> None:
        self.store.emit(
            source="test",
            kind="signal",
            summary="old revision",
            priority=10,
            dedupe_key="racing",
            now=epoch(12),
        )
        first = self.store.gate(now=epoch(12))
        _, inserted = self.store.emit(
            source="test",
            kind="signal",
            summary="new revision",
            priority=99,
            dedupe_key="racing",
            now=epoch(12) + 1,
        )
        self.assertFalse(inserted)
        ack = self.store.ack(first.context["lease_id"], now=epoch(12) + 2)
        self.assertEqual((ack.completed, ack.requeued), (0, 1))
        self.assertEqual(self.store.status(now=epoch(12) + 2)["counts"]["pending"], 1)
        second = self.store.gate(now=epoch(12) + 2)
        self.assertTrue(second.wake_agent)
        self.assertEqual(second.context["events"][0]["summary"], "new revision")

    def test_expired_lease_retries_after_backoff(self) -> None:
        self._emit(now=epoch(12))
        first = self.store.gate(now=epoch(12))
        first_lease = first.context["lease_id"]
        waiting = self.store.gate(now=epoch(12) + 11)
        self.assertFalse(waiting.wake_agent)
        self.assertEqual(waiting.reason, "waiting_retry")
        # Retry is ready after five seconds, then the global wake spacing
        # remains authoritative and prevents a hot model loop.
        retried = self.store.gate(now=epoch(12) + 60)
        self.assertTrue(retried.wake_agent)
        self.assertNotEqual(retried.context["lease_id"], first_lease)
        with sqlite3.connect(self.settings.database) as connection:
            attempts = connection.execute("SELECT attempts FROM events").fetchone()[0]
        self.assertEqual(attempts, 2)

    def test_expired_final_attempt_is_retained_as_dead_letter(self) -> None:
        policy = replace(self.policy, max_attempts=1)
        settings = replace(self.settings, policy=policy)
        store = EventStore(settings)
        store.initialize()
        store.emit(
            source="test",
            kind="signal",
            summary="one attempt only",
            now=epoch(12),
        )
        store.gate(now=epoch(12))
        result = store.gate(now=epoch(12) + 11)
        self.assertFalse(result.wake_agent)
        self.assertEqual(store.status(now=epoch(12) + 11)["counts"]["dead"], 1)

    def test_release_retries_then_ack_completes(self) -> None:
        self._emit(now=epoch(12))
        lease = self.store.gate(now=epoch(12)).context["lease_id"]
        retried, dead = self.store.release(
            lease,
            reason="temporary block",
            delay_seconds=3,
            now=epoch(12) + 1,
        )
        self.assertEqual((retried, dead), (1, 0))
        self.assertFalse(self.store.gate(now=epoch(12) + 3).wake_agent)
        second = self.store.gate(now=epoch(12) + 61)
        self.assertTrue(second.wake_agent)
        self.assertEqual(
            self.store.ack(second.context["lease_id"], now=epoch(12) + 62).completed,
            1,
        )
        self.assertEqual(self.store.status(now=epoch(12) + 62)["counts"]["done"], 1)

    def test_pause_preserves_pending_event(self) -> None:
        self._emit(now=epoch(12))
        self.store.set_paused(True, now=epoch(12))
        self.assertEqual(self.store.gate(now=epoch(12)).reason, "paused")
        self.assertEqual(self.store.status(now=epoch(12))["counts"]["pending"], 1)
        self.store.set_paused(False, now=epoch(12) + 1)
        self.assertTrue(self.store.gate(now=epoch(12) + 1).wake_agent)

    def test_concurrent_gates_create_one_lease(self) -> None:
        self._emit(now=epoch(12))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(lambda _: self.store.gate(now=epoch(12)), range(2))
            )
        self.assertEqual(sum(result.wake_agent for result in results), 1)
        self.assertEqual(self.store.status(now=epoch(12))["active_leases"], 1)

    def test_checkin_is_due_once_per_interval(self) -> None:
        policy = replace(
            self.policy,
            quiet_start="00:00",
            quiet_end="00:00",
            checkin_enabled=True,
            checkin_initial_delay_seconds=10,
            checkin_interval_seconds=100,
        )
        store = EventStore(replace(self.settings, policy=policy))
        store.initialize()
        self.assertFalse(store.gate(now=epoch(12)).wake_agent)
        due = store.gate(now=epoch(12) + 10)
        self.assertTrue(due.wake_agent)
        self.assertEqual(due.context["events"][0]["kind"], "gentle-checkin")
        store.ack(due.context["lease_id"], now=epoch(12) + 11)
        self.assertFalse(store.gate(now=epoch(12) + 60).wake_agent)
        next_due = store.gate(now=epoch(12) + 110)
        self.assertTrue(next_due.wake_agent)

    def test_paused_gate_does_not_schedule_checkins(self) -> None:
        policy = replace(
            self.policy,
            checkin_enabled=True,
            checkin_initial_delay_seconds=1,
            checkin_interval_seconds=1,
        )
        store = EventStore(replace(self.settings, policy=policy))
        store.initialize()
        store.set_paused(True, now=epoch(12))
        self.assertEqual(store.gate(now=epoch(12) + 10_000).reason, "paused")
        self.assertIsNone(store.status(now=epoch(12) + 10_000)["next_checkin_at"])

    def test_checkin_catchup_uses_arithmetic_not_one_loop_per_interval(self) -> None:
        policy = replace(
            self.policy,
            quiet_start="00:00",
            quiet_end="00:00",
            checkin_enabled=True,
            checkin_initial_delay_seconds=1,
            checkin_interval_seconds=1,
        )
        store = EventStore(replace(self.settings, policy=policy))
        store.initialize()
        store.gate(now=1)
        due = store.gate(now=1_000_000_000)
        self.assertTrue(due.wake_agent)
        with store.connect() as connection:
            next_at = int(store._meta_get(connection, "next_checkin_at"))
        self.assertEqual(next_at, 1_000_000_001)

    def test_in_flight_lease_blocks_a_second_urgent_batch(self) -> None:
        store = EventStore(
            replace(self.settings, policy=replace(self.policy, batch_size=1))
        )
        store.initialize()
        for dedupe in ("urgent-one", "urgent-two"):
            store.emit(
                source="test",
                kind="urgent",
                summary=dedupe,
                priority=99,
                dedupe_key=dedupe,
                now=epoch(12),
            )
        self.assertTrue(store.gate(now=epoch(12)).wake_agent)
        second = store.gate(now=epoch(12))
        self.assertFalse(second.wake_agent)
        self.assertEqual(second.reason, "lease_in_flight")

    def test_future_schema_is_refused_without_relabeling(self) -> None:
        with sqlite3.connect(self.settings.database) as connection:
            connection.execute(
                "UPDATE meta SET value = '99' WHERE key = 'schema_version'"
            )
        with self.assertRaises(SchemaVersionError):
            self.store.initialize()
        with sqlite3.connect(self.settings.database) as connection:
            version = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        self.assertEqual(version, "99")

    def test_quiet_time_requires_strict_hhmm(self) -> None:
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            replace(self.policy, quiet_start="1:2").validate()

    def test_doctor_confirms_owner_only_state(self) -> None:
        result = self.store.doctor()
        self.assertTrue(result["ok"])
        self.assertEqual(self.settings.home.stat().st_mode & 0o077, 0)
        self.assertEqual(self.settings.database.stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
