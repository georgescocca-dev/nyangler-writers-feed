from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_writer_report_offshore_scope_test",
    ROOT / "scripts" / "generate_writer_report.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def writer(domain: str) -> dict:
    return {
        "id": "test-offshore" if domain == "offshore" else "test-inshore",
        "name": "Test Writer",
        "role": "Zone Writer",
        "domain": domain,
        "zone_name": "Test Zone",
        "area": "Test waters",
        "beat_species": ["bluefin tuna"],
        "landmarks": [],
        "voice": "direct",
        "mood": "measured",
        "style_tags": [],
        "system_prompt": "Use supplied evidence only.",
    }


class OffshoreScopeTests(unittest.TestCase):
    def test_offshore_writer_receives_ten_nautical_mile_contract(self):
        system, user = MODULE.build_prompt(writer("offshore"), [], {}, [])
        payload = json.loads(user)
        self.assertIn("OFFSHORE COVERAGE CONTRACT", system)
        self.assertEqual(
            payload["offshore_coverage_scope"]["minimum_distance_from_shore_nm"],
            10,
        )
        self.assertIn("wreck", payload["offshore_coverage_scope"]["structure_types"])

    def test_inshore_writer_does_not_receive_offshore_contract(self):
        system, user = MODULE.build_prompt(writer("inshore"), [], {}, [])
        payload = json.loads(user)
        self.assertNotIn("OFFSHORE COVERAGE CONTRACT", system)
        self.assertIsNone(payload["offshore_coverage_scope"])


if __name__ == "__main__":
    unittest.main()