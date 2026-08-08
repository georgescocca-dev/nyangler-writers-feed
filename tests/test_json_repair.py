from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
