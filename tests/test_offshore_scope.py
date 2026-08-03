from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

    def test_offshore_writer_folds_inside_tuna_into_closest_area_report(self):
        system, _ = MODULE.build_prompt(writer("offshore"), [], {}, [])
        self.assertIn("closest applicable existing area report", system)
        self.assertIn("tuna are inside at the Virginia Wreck", system)
        self.assertIn("San Diego", system)
        self.assertIn("Mud Hole", system)
        self.assertIn("Texas Tower", system)
        self.assertIn("the Tails", system)
        self.assertIn(
            "verified nearby structure is part of your offshore beat", system
        )
        self.assertNotIn("Montour Canyon", system)

    def test_inshore_writer_does_not_receive_offshore_contract(self):
        system, user = MODULE.build_prompt(writer("inshore"), [], {}, [])
        payload = json.loads(user)
        self.assertNotIn("OFFSHORE COVERAGE CONTRACT", system)
        self.assertIsNone(payload["offshore_coverage_scope"])

    def test_offshore_briefing_does_not_fan_global_tuna_read_to_every_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            briefing = Path(temp) / "hooper_2026-08-03.json"
            briefing.write_text(
                json.dumps(
                    {
                        "date": "2026-08-03",
                        "zone_analysis": {
                            "hudson-canyon": "Virginia Wreck tuna signal",
                            "nj-offshore": "Different regional read",
                        },
                        "species_analysis": {
                            "bluefin tuna": "Global Virginia Wreck tuna signal"
                        },
                    }
                )
            )
            with mock.patch.object(MODULE, "_hooper_files", return_value=[briefing]):
                offshore = MODULE.load_hooper_briefing(
                    "nj-offshore", ["bluefin tuna"], include_species_analysis=False
                )
                inshore = MODULE.load_hooper_briefing(
                    "nj-offshore", ["bluefin tuna"], include_species_analysis=True
                )
            self.assertNotIn("species_analysis", offshore)
            self.assertIn("species_analysis", inshore)

    def test_named_lead_report_requires_hooper_source_evidence(self):
        report = {
            "headline": "Tuna move inside toward Virginia Wreck",
            "subhead": "A verified inside bite changes the New York offshore plan.",
            "body_markdown": "Virginia Wreck bluefin tuna report. " + "x" * 1000,
            "tags": ["bluefin-tuna", "virginia-wreck", "new-york-offshore"],
        }
        unsupported = MODULE.report_quality_errors(report, hooper={})
        supported = MODULE.report_quality_errors(
            report,
            hooper={
                "named_lead_evidence": {
                    "Virginia Wreck": [
                        {"date": "2026-08-03", "source": "captain report"}
                    ]
                }
            },
        )
        self.assertIn("unsupported named lead: Virginia Wreck", unsupported)
        self.assertNotIn("unsupported named lead: Virginia Wreck", supported)


if __name__ == "__main__":
    unittest.main()