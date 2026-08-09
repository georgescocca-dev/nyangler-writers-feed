from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_writer_report_json_test",
    ROOT / "scripts" / "generate_writer_report.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class JsonRepairTests(unittest.TestCase):
    def test_repairs_literal_newline_and_tab_inside_string(self):
        raw = (
            '{"headline":"A valid headline","body_markdown":"first line\n'
            'second\tline","tags":["one","two","three"]}'
        )
        payload = MODULE.parse_llm_json(raw)
        self.assertEqual(payload["body_markdown"], "first line\nsecond\tline")

    def test_does_not_change_structural_whitespace(self):
        raw = 'prefix\n```json\n{\n  "headline": "Clean"\n}\n```\ntrailing'
        payload = MODULE.parse_llm_json(raw)
        self.assertEqual(payload, {"headline": "Clean"})

    def test_retries_transient_router_statuses(self):
        for status in (404, 408, 409, 425, 429, 500, 503):
            with self.subTest(status=status):
                self.assertTrue(MODULE.retryable_http_status(status))
        for status in (400, 401, 403, 422):
            with self.subTest(status=status):
                self.assertFalse(MODULE.retryable_http_status(status))

    def test_supplies_grounded_tags_when_model_omits_them(self):
        report = {
            "body_markdown": "A useful report.",
            "tags": [],
        }
        writer = {
            "beat_species": ["striped bass", "bluefish"],
            "zone_slug": "western-sound",
        }
        scrubbed = MODULE.scrub_report(report, writer)
        self.assertEqual(
            scrubbed["tags"],
            ["striped-bass", "bluefish", "western-sound"],
        )

    def test_quality_gate_rejects_short_body(self):
        report = {
            "headline": "Western Sound bass settle into the night shift",
            "subhead": "The bunker remain thick, but the productive window has moved.",
            "body_markdown": "short",
            "tags": ["striped-bass", "bunker", "western-sound"],
        }
        self.assertEqual(
            MODULE.report_quality_errors(report),
            ["body too short"],
        )

    def test_report_datetime_honors_recovery_date(self):
        previous = os.environ.get("NOREASTER_REPORT_DATE")
        os.environ["NOREASTER_REPORT_DATE"] = "2026-08-07"
        try:
            value = MODULE.report_datetime()
        finally:
            if previous is None:
                os.environ.pop("NOREASTER_REPORT_DATE", None)
            else:
                os.environ["NOREASTER_REPORT_DATE"] = previous
        self.assertEqual(value.isoformat(), "2026-08-07T00:00:00+00:00")

    def test_report_datetime_rejects_invalid_recovery_date(self):
        previous = os.environ.get("NOREASTER_REPORT_DATE")
        os.environ["NOREASTER_REPORT_DATE"] = "08/07/2026"
        try:
            with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                MODULE.report_datetime()
        finally:
            if previous is None:
                os.environ.pop("NOREASTER_REPORT_DATE", None)
            else:
                os.environ["NOREASTER_REPORT_DATE"] = previous


def _valid_payload(**overrides) -> dict:
    payload = {
        "headline": "Western Sound bass settle into the night shift",
        "subhead": "The bunker remain thick, but the productive window has moved.",
        "dateline": "HUNTINGTON, NY — August 9",
        "body_markdown": "The Sound gave up a honest week. " + "x" * 1000,
        "tags": ["striped-bass", "bunker", "western-sound"],
    }
    payload.update(overrides)
    return payload


class GenerationRetryTests(unittest.TestCase):
    """End-to-end retry behavior for generate_report_with_retry."""

    def _writer(self) -> dict:
        return {
            "id": "western-sound",
            "name": "Test Writer",
            "role": "Zone Writer",
            "domain": "inshore",
            "zone_slug": "western-sound",
            "zone_name": "Western Sound",
            "beat_species": ["striped bass"],
        }

    def test_malformed_then_valid_json_succeeds_on_retry(self):
        calls: list[tuple[str, str]] = []

        def fake_call(system: str, user: str, model: str) -> str:
            calls.append((system, user))
            if len(calls) == 1:
                return '{"headline": "truncated, no close'
            return json.dumps(_valid_payload())

        with mock.patch.object(MODULE, "call_openrouter", side_effect=fake_call), \
                mock.patch.object(MODULE.time, "sleep", lambda *_: None):
            report = MODULE.generate_report_with_retry(
                self._writer(), "SYSTEM", "USER", "test-model"
            )
        self.assertEqual(report["headline"], _valid_payload()["headline"])
        self.assertEqual(len(calls), 2)
        # The retry must be a schema-repair prompt, not the original prompt.
        self.assertNotEqual(calls[1][1], "USER")
        self.assertIn("schema", calls[1][1].lower())

    def test_persistent_invalid_json_exhausts_with_sanitized_error(self):
        def fake_call(system: str, user: str, model: str) -> str:
            return "this is not json at all"

        with mock.patch.object(MODULE, "call_openrouter", side_effect=fake_call), \
                mock.patch.object(MODULE.time, "sleep", lambda *_: None):
            with self.assertRaises(MODULE.ReportGenerationError) as ctx:
                MODULE.generate_report_with_retry(
                    self._writer(), "SYSTEM", "USER", "test-model"
                )
        message = str(ctx.exception)
        self.assertNotIn("this is not json at all", message)
        self.assertIn("invalid json", message)

    def test_quality_failure_also_triggers_bounded_retry(self):
        calls = 0

        def fake_call(system: str, user: str, model: str) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                return json.dumps(_valid_payload(body_markdown="short"))
            return json.dumps(_valid_payload())

        with mock.patch.object(MODULE, "call_openrouter", side_effect=fake_call), \
                mock.patch.object(MODULE.time, "sleep", lambda *_: None):
            report = MODULE.generate_report_with_retry(
                self._writer(), "SYSTEM", "USER", "test-model"
            )
        self.assertEqual(calls, 2)
        self.assertGreater(len(report["body_markdown"]), 900)

    def test_retry_bound_is_respected(self):
        with mock.patch.object(
            MODULE, "call_openrouter", return_value="not json"
        ) as provider, mock.patch.object(MODULE.time, "sleep", lambda *_: None):
            with self.assertRaises(MODULE.ReportGenerationError):
                MODULE.generate_report_with_retry(
                    self._writer(), "SYSTEM", "USER", "test-model"
                )
        self.assertLessEqual(provider.call_count, 4)


class AtomicEmitTests(unittest.TestCase):
    def test_emit_report_writes_one_atomic_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            reports_dir = Path(temp)
            writer = {
                "id": "western-sound",
                "name": "Test Writer",
                "role": "Zone Writer",
                "zone_slug": "western-sound",
                "zone_name": "Western Sound",
            }
            report = _valid_payload()
            today = MODULE.datetime(2026, 8, 9, tzinfo=MODULE.timezone.utc)
            with mock.patch.object(MODULE, "REPORTS_DIR", reports_dir), \
                    mock.patch.dict(os.environ, {"NOREASTER_WRITE_UNIFIED_DB": "0"}):
                pub = MODULE.emit_report(writer, report, today)
            out_json = reports_dir / f"{pub['id']}.json"
            out_md = reports_dir / f"{pub['id']}.md"
            self.assertTrue(out_json.exists())
            self.assertTrue(out_md.exists())
            loaded = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(loaded["headline"], report["headline"])
            # Exactly one pair for this writer/date.
            self.assertEqual(
                len(list(reports_dir.glob("2026-08-09-western-sound-*.json"))), 1
            )
            self.assertEqual(
                len(list(reports_dir.glob("2026-08-09-western-sound-*.md"))), 1
            )


if __name__ == "__main__":
    unittest.main()
