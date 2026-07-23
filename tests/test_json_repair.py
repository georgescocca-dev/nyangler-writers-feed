from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
