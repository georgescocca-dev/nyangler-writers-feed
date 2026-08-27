from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HARVEST = load_module("fleet_harvest_test", SCRIPTS / "fleet_harvest.py")
GENERATOR = load_module(
    "generate_writer_report_fleet_test",
    SCRIPTS / "generate_writer_report.py",
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def ready_row(**overrides) -> dict:
    row = {
        "id": "row-1",
        "status": "ready",
        "area": "montauk",
        "port": "Montauk",
        "desk": "inshore",
        "headline": "Bass still on the boulders at North Bar",
        "text": "Keepers on bucktails at the North Bar on the outgoing.",
        "boat": "Miss Montauk",
        "source_url": "https://www.facebook.com/missmontauk/posts/123",
        "created_at": "2026-08-25T14:00:00Z",
        "photo_url": "https://cdn.example.com/catch.jpg",
    }
    row.update(overrides)
    return row


class FleetMappingTests(unittest.TestCase):
    def test_area_slug_maps_onto_writer_id(self):
        self.assertEqual(HARVEST.map_location_token("montauk"), "montauk")
        self.assertEqual(
            HARVEST.map_location_token("cape-may-delaware-bay"),
            "cape-may-delaware-bay",
        )
        self.assertEqual(HARVEST.map_location_token("jones-inlet"), "jones-inlet")
        self.assertEqual(HARVEST.map_location_token("Debs/Jones"), "jones-inlet")
        self.assertEqual(HARVEST.map_location_token("Captree"), "fire-island")
        self.assertEqual(
            HARVEST.map_location_token("stellwagen-bank"),
            "ma-offshore-stellwagen",
        )

    def test_port_and_desk_fill_in_when_area_is_empty(self):
        self.assertEqual(
            HARVEST.resolve_writer_ids({"port": "Freeport"}),
            {"jones-inlet"},
        )
        self.assertEqual(
            HARVEST.resolve_writer_ids({"desk": "hudson-canyon"}),
            {"hudson-canyon"},
        )

    def test_unclassified_and_broadcast_rows_map_to_nobody(self):
        self.assertEqual(HARVEST.resolve_writer_ids({}), set())
        self.assertEqual(HARVEST.resolve_writer_ids({"area": "unclassified"}), set())
        self.assertEqual(HARVEST.resolve_writer_ids({"area": "offshore"}), set())
        self.assertEqual(HARVEST.resolve_writer_ids({"desk": "inshore"}), set())
        self.assertEqual(HARVEST.resolve_writer_ids({"area": "south-shore"}), set())

    def test_conflicting_area_and_port_are_skipped(self):
        self.assertEqual(
            HARVEST.resolve_writer_ids(
                {"area": "montauk", "port": "cape-may"}
            ),
            set(),
        )

    def test_wrong_zone_is_not_fed_to_another_writer(self):
        montauk = ready_row()
        cape_may = ready_row(area="cape-may-delaware-bay", port="Cape May")
        self.assertTrue(HARVEST.row_belongs_to_writer(montauk, "montauk"))
        self.assertFalse(HARVEST.row_belongs_to_writer(montauk, "jones-inlet"))
        self.assertTrue(
            HARVEST.row_belongs_to_writer(cape_may, "cape-may-delaware-bay")
        )
        self.assertFalse(HARVEST.row_belongs_to_writer(cape_may, "montauk"))


class FleetFilterTests(unittest.TestCase):
    def test_skips_held_and_stale_for_reporters(self):
        self.assertFalse(
            HARVEST.is_ready_row(ready_row(status="held"), now=NOW)
        )
        self.assertFalse(
            HARVEST.is_ready_row(
                ready_row(status="stale-for-reporters"), now=NOW
            )
        )
        self.assertTrue(HARVEST.is_ready_row(ready_row(), now=NOW))

    def test_skips_rows_older_than_seven_days(self):
        stale = ready_row(created_at="2026-08-01T12:00:00Z")
        self.assertFalse(HARVEST.is_ready_row(stale, now=NOW))
        fresh = ready_row(
            created_at=(NOW - timedelta(days=6)).isoformat().replace("+00:00", "Z")
        )
        self.assertTrue(HARVEST.is_ready_row(fresh, now=NOW))

    def test_load_keeps_only_this_writers_ready_text(self):
        rows = [
            ready_row(),
            ready_row(
                id="row-2",
                area="jones-inlet",
                port="Freeport",
                headline="Fluke in the Jones cut",
            ),
            ready_row(id="row-3", status="held"),
            ready_row(
                id="row-4",
                area="unclassified",
                port="",
                desk="",
                headline="Mystery bite",
            ),
            ready_row(
                id="row-5",
                area="montauk",
                headline="",
                text="",
                caption="",
                boat="",
            ),
        ]
        loaded = HARVEST.load_fleet_harvest_for_writer(
            "montauk", now=NOW, rows=rows
        )
        self.assertEqual(len(loaded), 1)
        self.assertEqual(
            loaded[0]["headline"], "Bass still on the boulders at North Bar"
        )
        self.assertEqual(loaded[0]["source_kind"], "fleet_harvest")
        self.assertNotIn("url", loaded[0])
        self.assertNotIn("source_url", loaded[0])
        self.assertNotIn("photo_url", loaded[0])
        self.assertNotIn("facebook", json.dumps(loaded[0]).lower())


class FleetFallbackTests(unittest.TestCase):
    def test_missing_credentials_return_empty_and_log(self):
        env = {k: v for k, v in os.environ.items() if "SUPABASE" not in k and k != "SERVICE_ROLE"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            HARVEST, "_load_dotenv_value", return_value=""
        ):
            self.assertEqual(HARVEST.credentials_missing_reason(), "SUPABASE_URL / SUPABASE_SERVICE_ROLE")
            buf = io.StringIO()
            with mock.patch.object(HARVEST.sys, "stderr", buf):
                HARVEST.log_credential_miss()
                rows = HARVEST.load_fleet_harvest_for_writer("montauk")
        self.assertEqual(rows, [])
        self.assertIn("fleet_harvest=skip", buf.getvalue())

    def test_fetch_error_returns_empty_without_raising(self):
        with mock.patch.object(
            HARVEST,
            "_rest_get",
            side_effect=OSError("network down"),
        ):
            buf = io.StringIO()
            with mock.patch.object(HARVEST.sys, "stderr", buf):
                rows = HARVEST.fetch_ready_fishing_reports(
                    now=NOW,
                    url="https://bcdlbzyvbpolxdpdthls.supabase.co",
                    service_role="secret",
                )
        self.assertEqual(rows, [])
        self.assertIn("falling back to jsonl", buf.getvalue())


class FleetPromptTests(unittest.TestCase):
    def writer(self) -> dict:
        return {
            "id": "montauk",
            "name": "Test Montauk Writer",
            "role": "Zone Writer",
            "domain": "inshore",
            "zone_name": "Montauk",
            "area": "Montauk Point",
            "beat_species": ["striped bass"],
            "landmarks": ["North Bar"],
            "voice": "direct",
            "mood": "measured",
            "style_tags": [],
            "system_prompt": "Use supplied evidence only.",
        }

    def test_fleet_headlines_reach_the_prompt_blob(self):
        fleet = HARVEST.load_fleet_harvest_for_writer(
            "montauk", now=NOW, rows=[ready_row()]
        )
        system, user = GENERATOR.build_prompt(
            self.writer(),
            [],
            {},
            [],
            fleet_harvest=fleet,
        )
        payload = json.loads(user)
        blob = payload["fleet_harvest_DO_NOT_CITE"]
        self.assertEqual(len(blob), 1)
        self.assertEqual(
            blob[0]["headline"], "Bass still on the boulders at North Bar"
        )
        self.assertIn("Bass still on the boulders at North Bar", user)
        self.assertIn("FLEET HARVEST IS BACKGROUND INTEL", system)
        self.assertIn("Do not copy a fleet post as the finished column", user)
        self.assertNotIn("facebook.com", user.lower())
        self.assertNotIn("facebook.com", system.lower())
        self.assertNotIn("https://cdn.example.com/catch.jpg", user)

    def test_forum_jsonl_still_lands_in_its_own_prompt_key(self):
        _, user = GENERATOR.build_prompt(
            self.writer(),
            [
                {
                    "date": "2026-08-20",
                    "author": "forum-user",
                    "title": "Old forum post",
                    "text": "Seasonal guess from last year.",
                    "thread_id": 123,
                }
            ],
            {},
            [],
            fleet_harvest=[],
        )
        payload = json.loads(user)
        self.assertEqual(
            payload["background_forum_chatter_DO_NOT_CITE"][0]["title"],
            "Old forum post",
        )
        self.assertEqual(payload["fleet_harvest_DO_NOT_CITE"], [])

    def test_emit_report_stays_zone_writer_output(self):
        report = {
            "headline": "North Bar bass hold the outgoing",
            "subhead": "The Point fleet is working bucktails on the outgoing at North Bar.",
            "dateline": "MONTAUK, NY — August 27",
            "body_markdown": "x" * 1000,
            "tags": ["striped-bass", "montauk", "bucktails"],
        }
        writer = {
            "id": "montauk",
            "name": "Test Montauk Writer",
            "role": "Zone Writer",
            "zone_slug": "montauk",
            "zone_name": "Montauk",
        }
        with tempfile.TemporaryDirectory() as temp:
            reports_dir = Path(temp) / "reports"
            with mock.patch.object(GENERATOR, "REPORTS_DIR", reports_dir):
                with mock.patch.dict(os.environ, {"NOREASTER_WRITE_UNIFIED_DB": "0"}):
                    publication = GENERATOR.emit_report(writer, report, NOW)
        self.assertEqual(publication["writer_id"], "montauk")
        self.assertEqual(publication["writer_name"], "Test Montauk Writer")
        self.assertIn("headline", publication)
        self.assertIn("body_markdown", publication)
        self.assertNotIn("source_url", publication)
        self.assertNotEqual(publication["writer_name"], "Miss Montauk")
        self.assertTrue(publication["id"].startswith("2026-08-27-montauk-"))


class GeneratorFallbackTests(unittest.TestCase):
    def test_generator_load_fleet_harvest_swallows_errors(self):
        with mock.patch.object(
            GENERATOR.fleet_harvest,
            "credentials_missing_reason",
            return_value=None,
        ), mock.patch.object(
            GENERATOR.fleet_harvest,
            "load_fleet_harvest_for_writer",
            side_effect=RuntimeError("boom"),
        ):
            buf = io.StringIO()
            with mock.patch.object(GENERATOR.sys, "stderr", buf):
                rows = GENERATOR.load_fleet_harvest("montauk")
        self.assertEqual(rows, [])
        self.assertIn("using jsonl forum intel", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
