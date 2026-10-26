# -*- coding: utf-8 -*-
import hashlib
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import api
import importers
import store
from test_importers import CSV, GPX, make_fit


class TestProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "home")
        self.inbox = os.path.join(self.tmp.name, "in")
        os.makedirs(self.inbox)

    def put(self, name, content):
        path = os.path.join(self.inbox, name)
        with open(path, "wb") as handle:
            handle.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def test_csv_records_file_hash_per_metric(self):
        path = self.put("daily.csv", CSV)
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        importers.import_paths(self.base, [path])
        record = store.load_daily(self.base)["20260810"]
        self.assertEqual(record["_files"]["hrv_rmssd"], digest[:12])
        listed = store.load_imports(self.base)
        self.assertEqual(len(listed), 1)
        self.assertEqual((listed[0]["file"], listed[0]["sha256"], listed[0]["kind"]),
                         ("daily.csv", digest, "csv"))
        self.assertEqual(listed[0]["size"], os.path.getsize(path))
        self.assertGreater(listed[0]["counts"]["daily_changes"], 0)
        self.assertIn("imported_at", listed[0])
        view = api.provenance(self.base, "2026-08-10")
        self.assertEqual(view["date"], "20260810")
        self.assertEqual(view["metrics"]["hrv_rmssd"]["value"], 62)
        self.assertEqual(view["metrics"]["hrv_rmssd"]["source"], "import")
        self.assertEqual(view["metrics"]["hrv_rmssd"]["file"]["name"], "daily.csv")
        self.assertEqual(view["metrics"]["hrv_rmssd"]["file"]["sha256"], digest)
        self.assertEqual(list(api.provenance(self.base, "20260810", "resting_hr")["metrics"]),
                         ["resting_hr"])
        self.assertEqual(api.provenance(self.base, "20260810", "vo2max")["metrics"], {})

    def test_activities_carry_source_file(self):
        fit = self.put("run.fit", make_fit([100, 120, 140, 160]))
        gpx = self.put("walk.gpx", GPX)
        importers.import_paths(self.base, [fit, gpx])
        by_name = {item["source_file"]["name"]: item for item in store.load_activities(self.base)}
        self.assertEqual(set(by_name), {"run.fit", "walk.gpx"})
        self.assertEqual(by_name["run.fit"]["source_file"]["sha256"],
                         hashlib.sha256(open(fit, "rb").read()).hexdigest())
        view = api.provenance(self.base, by_name["run.fit"]["date"])
        self.assertEqual(view["activities"][0]["source_file"]["name"], "run.fit")
        self.assertEqual([item["kind"] for item in store.load_imports(self.base)], ["fit", "gpx"])

    def test_demo_data_has_no_file(self):
        api.init(self.base, demo=True)
        day = store.latest_date(self.base)
        view = api.provenance(self.base, day)
        self.assertTrue(view["metrics"])
        for entry in view["metrics"].values():
            self.assertEqual(entry["source"], "demo")
            self.assertIsNone(entry["file"])
        self.assertTrue(all(item["source_file"] is None for item in view["activities"]))

    def test_manual_overwrite_clears_file_and_same_value_keeps_it(self):
        path = self.put("daily.csv", CSV)
        importers.import_paths(self.base, [path])
        again = importers.import_paths(self.base, [path])
        self.assertEqual(again["daily_changes"], 0)
        self.assertEqual(len(store.load_imports(self.base)), 2)
        store.upsert_daily(self.base, [{"date": "20260810", "hrv_rmssd": 70}], "manual")
        view = api.provenance(self.base, "20260810")
        self.assertEqual(view["metrics"]["hrv_rmssd"]["source"], "manual")
        self.assertIsNone(view["metrics"]["hrv_rmssd"]["file"])
        self.assertIsNotNone(view["metrics"]["resting_hr"]["file"])

    def test_old_calls_without_file_ref_still_work(self):
        store.init(self.base)
        self.assertEqual(store.upsert_daily(self.base, [{"date": "20260810", "hrv_rmssd": 50}]), 1)
        self.assertEqual(store.add_activities(self.base, [{"date": "20260810", "start": "s",
                                                           "duration_s": 60, "type": "run"}]), 1)
        self.assertNotIn("_files", store.load_daily(self.base)["20260810"])
        self.assertNotIn("source_file", store.load_activities(self.base)[0])


if __name__ == "__main__":
    unittest.main()
