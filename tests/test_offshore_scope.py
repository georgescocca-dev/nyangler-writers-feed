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

    def test_wrong_zone_named_lead_evidence_is_not_accepted(self):
        report = {
            "headline": "Bacardi tuna signal reaches Massachusetts waters",
            "subhead": "A source-backed report is still outside this writer's assigned zone.",
            "body_markdown": "Bacardi bluefin tuna report. " + "x" * 1000,
            "tags": ["bluefin-tuna", "bacardi", "massachusetts-offshore"],
            "offshore_locations_used": ["Bacardi"],
        }
        errors = MODULE.report_quality_errors(
            report,
            hooper={
                "named_lead_evidence": {
                    "Bacardi": [
                        {
                            "date": "2026-08-03",
                            "source": "captain report",
                            "preferred_report_zone": "hudson-canyon",
                        }
                    ]
                }
            },
            writer={"id": "ma-offshore-stellwagen", "domain": "offshore"},
        )
        self.assertIn("wrong-zone named lead: Bacardi", errors)

    def test_unlisted_coastwide_spot_reaches_only_its_closest_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            briefing = Path(temp) / "hooper_2026-08-03.json"
            briefing.write_text(
                json.dumps(
                    {
                        "date": "2026-08-03",
                        "zone_analysis": {
                            "point-judith-block-island": "Bluefin tuna were taken on Coxes Ledge.",
                            "ma-offshore-stellwagen": "Different regional read.",
                        },
                        "offshore_structure_evidence": [
                            {
                                "location_name": "Coxes Ledge",
                                "structure_type": "ledge",
                                "report_zone": "point-judith-block-island",
                                "species": "bluefin tuna",
                                "date": "2026-08-03",
                                "source": "Rhode Island captain report",
                                "source_record_id": "ri-source-1",
                            }
                        ],
                    }
                )
            )
            with mock.patch.object(MODULE, "_hooper_files", return_value=[briefing]):
                ri = MODULE.load_hooper_briefing(
                    "point-judith-block-island",
                    ["bluefin tuna"],
                    include_species_analysis=False,
                )
                ma = MODULE.load_hooper_briefing(
                    "ma-offshore-stellwagen",
                    ["bluefin tuna"],
                    include_species_analysis=False,
                )
            self.assertEqual(
                ri["offshore_structure_evidence"][0]["location_name"],
                "Coxes Ledge",
            )
            self.assertNotIn("offshore_structure_evidence", ma)
            self.assertEqual(
                ma["offshore_structure_route_index"]["Coxes Ledge"],
                "point-judith-block-island",
            )

        report = {
            "headline": "Bluefin show on Coxes Ledge",
            "subhead": "A dated Rhode Island report puts tuna on local structure.",
            "body_markdown": "Coxes Ledge bluefin tuna report. " + "x" * 1000,
            "tags": ["bluefin-tuna", "coxes-ledge", "rhode-island-offshore"],
            "offshore_locations_used": ["Coxes Ledge"],
        }
        evidence = {
            "offshore_structure_evidence": [
                {
                    "location_name": "Coxes Ledge",
                    "report_zone": "point-judith-block-island",
                    "date": "2026-08-03",
                    "source": "Rhode Island captain report",
                }
            ]
        }
        self.assertNotIn(
            "wrong-zone offshore structure: Coxes Ledge",
            MODULE.report_quality_errors(
                report,
                hooper=evidence,
                writer={"id": "point-judith-block-island", "domain": "offshore"},
            ),
        )
        self.assertIn(
            "wrong-zone offshore structure: Coxes Ledge",
            MODULE.report_quality_errors(
                report,
                hooper=evidence,
                writer={"id": "ma-offshore-stellwagen", "domain": "offshore"},
            ),
        )

    def test_wrong_writer_and_unsupported_local_spot_cannot_bypass_publication_gate(self):
        report = {
            "headline": "Bluefin Push Across the Local Ledge",
            "subhead": "A current offshore signal puts tuna on a named piece of structure.",
            "body_markdown": "Coxes Ledge bluefin tuna report. " + "x" * 1000,
            "tags": ["bluefin-tuna", "offshore", "local-structure"],
            "offshore_locations_used": ["Coxes Ledge"],
        }
        route_only = {"offshore_structure_route_index": {"Coxes Ledge": "point-judith-block-island"}}
        errors = MODULE.report_quality_errors(
            report, hooper=route_only,
            writer={"id": "ma-offshore-stellwagen", "domain": "offshore"},
        )
        self.assertIn("wrong-zone offshore structure: Coxes Ledge", errors)

        report["body_markdown"] = "Invented Bank bluefin tuna report. " + "x" * 1000
        report["offshore_locations_used"] = ["Invented Bank"]
        errors = MODULE.report_quality_errors(
            report, hooper=route_only,
            writer={"id": "ma-offshore-stellwagen", "domain": "offshore"},
        )
        self.assertIn("unsupported offshore structure: Invented Bank", errors)

        report.pop("offshore_locations_used")
        errors = MODULE.report_quality_errors(
            report, hooper=route_only,
            writer={"id": "ma-offshore-stellwagen", "domain": "offshore"},
        )
        self.assertIn("missing offshore location disclosure", errors)

        report["offshore_locations_used"] = []
        errors = MODULE.report_quality_errors(
            report, hooper=route_only,
            writer={"id": "ma-offshore-stellwagen", "domain": "offshore"},
        )
        self.assertIn("undisclosed offshore structure: Invented Bank", errors)
        self.assertIn("unsupported offshore structure: Invented Bank", errors)


if __name__ == "__main__":
    unittest.main()