from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CloudPathTests(unittest.TestCase):
    def test_editor_verify_uses_cloud_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            feed = temp_path / "feed"
            intel = temp_path / "intel"
            (feed / "reports").mkdir(parents=True)
            (intel / "config").mkdir(parents=True)
            (intel / "config" / "writers_roster.json").write_text(
                json.dumps(
                    {
                        "writers": [
                            {
                                "id": "test-zone",
                                "status": "active",
                                "domain": "inshore",
                            },
                            {
                                "id": "editor-in-chief",
                                "status": "active",
                                "domain": "editorial",
                            },
                        ]
                    }
                )
            )
            (feed / "reports" / "2026-07-23-test-zone-report.json").write_text(
                json.dumps({"writer_id": "test-zone"})
            )

            with mock.patch.dict(
                os.environ,
                {
                    "NOREASTER_FEED_REPO": str(feed),
                    "NOREASTER_INTEL_DIR": str(intel),
                },
            ):
                module = load_module(
                    "editor_verify_cloud_test",
                    ROOT / "scripts" / "editor_verify.py",
                )

            result = module.verify("2026-07-23")
            self.assertTrue(result["ok"])
            self.assertEqual(result["expected_count"], 1)
            self.assertEqual(result["present_count"], 1)

    def test_generator_paths_follow_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            feed = temp_path / "feed"
            intel = temp_path / "intel"
            setup_analysis = temp_path / "setup-analysis"
            with mock.patch.dict(
                os.environ,
                {
                    "NOREASTER_FEED_REPO": str(feed),
                    "NOREASTER_INTEL_DIR": str(intel),
                    "NOREASTER_SETUP_ANALYSIS_DIR": str(setup_analysis),
                },
            ):
                module = load_module(
                    "generate_writer_report_cloud_test",
                    ROOT / "scripts" / "generate_writer_report.py",
                )

            self.assertEqual(module.FEED_REPO, feed)
            self.assertEqual(module.NOREASTER, intel)
            self.assertEqual(module.HOOPER_SETUP_DIR, setup_analysis)


if __name__ == "__main__":
    unittest.main()
