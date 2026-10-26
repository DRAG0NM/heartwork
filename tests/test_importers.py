# -*- coding: utf-8 -*-
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import importers
import store

FIT_START = 1786363200 - importers.FIT_EPOCH  # 2026-08-10 12:00:00 UTC (synthetic)


def _definition(local, gnum, fields, dev=None):
    header = 0x40 | local | (0x20 if dev else 0)
    body = struct.pack("<BBHB", 0, 0, gnum, len(fields))
    body += b"".join(struct.pack("<BBB", *field) for field in fields)
    if dev:
        body += struct.pack("<B", len(dev)) + b"".join(struct.pack("<BBB", *field) for field in dev)
    return bytes([header]) + body


def make_fit(hrs, step=10):
    """Minimal synthetic FIT: records (one via compressed timestamp), developer field, session."""
    out = _definition(0, 20, [(253, 4, 0x86), (3, 1, 2)], dev=[(0, 2, 0)])
    out += _definition(3, 20, [(3, 1, 2)])
    ts = FIT_START
    for index, hr in enumerate(hrs):
        ts = FIT_START + index * step
        if index == 2:
            out += bytes([0x80 | (3 << 5) | (ts & 0x1F), hr])
        else:
            out += bytes([0]) + struct.pack("<IB", ts, hr) + b"\x01\x02"
    out += _definition(2, 18, [(253, 4, 0x86), (2, 4, 0x86), (5, 1, 0), (7, 4, 0x86),
                               (8, 4, 0x86), (9, 4, 0x86), (16, 1, 2)])
    out += bytes([2]) + struct.pack("<IIBIIIB", ts, FIT_START, 1, 60000, 55000, 15000, 150)
    header = struct.pack("<BBHI4sH", 14, 0x10, 2000, len(out), b".FIT", 0)
    return header + out + b"\x00\x00"


GPX = """<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
<trk><type>running</type><trkseg>
<trkpt lat="0.0000" lon="0.0000"><time>2026-08-11T12:00:00Z</time><extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>120</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
<trkpt lat="0.0000" lon="0.0045"><time>2026-08-11T12:02:00Z</time><extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>160</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
<trkpt lat="0.0000" lon="0.0090"><time>2026-08-11T12:04:00Z</time><extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>180</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
</trkseg></trk></gpx>"""

TCX = """<?xml version="1.0"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
<Activities><Activity Sport="Biking"><Id>2026-08-12T12:00:00Z</Id>
<Lap StartTime="2026-08-12T12:00:00Z"><TotalTimeSeconds>600</TotalTimeSeconds><DistanceMeters>3000</DistanceMeters>
<Track>
<Trackpoint><Time>2026-08-12T12:00:00Z</Time><DistanceMeters>0</DistanceMeters><HeartRateBpm><Value>110</Value></HeartRateBpm></Trackpoint>
<Trackpoint><Time>2026-08-12T12:05:00Z</Time><DistanceMeters>1500</DistanceMeters><HeartRateBpm><Value>130</Value></HeartRateBpm></Trackpoint>
<Trackpoint><Time>2026-08-12T12:10:00Z</Time><DistanceMeters>3000</DistanceMeters><HeartRateBpm><Value>150</Value></HeartRateBpm></Trackpoint>
</Track></Lap></Activity></Activities></TrainingCenterDatabase>"""

CSV = """日期,HRV(ms),静息心率,睡眠时长,体重,备注
2026-08-10,62,50,420,70.2,ok
20260811,58,52,400,,x
2026/8/12,,53,,70.0,y
"""

APPLE = """<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" unit="count/min" startDate="2026-08-10 09:00:00 +0800" endDate="2026-08-10 09:00:00 +0800" value="50"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" unit="count/min" startDate="2026-08-10 10:00:00 +0800" endDate="2026-08-10 10:00:00 +0800" value="54"/>
 <Record type="HKQuantityTypeIdentifierBodyMass" unit="kg" startDate="2026-08-10 07:30:00 +0800" endDate="2026-08-10 07:30:00 +0800" value="70.5"/>
 <Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" unit="ms" startDate="2026-08-10 07:30:00 +0800" endDate="2026-08-10 07:30:00 +0800" value="45"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" startDate="2026-08-09 22:30:00 +0800" endDate="2026-08-10 07:30:00 +0800" value="HKCategoryValueSleepAnalysisInBed"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" startDate="2026-08-09 23:00:00 +0800" endDate="2026-08-10 03:00:00 +0800" value="HKCategoryValueSleepAnalysisAsleepCore"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" startDate="2026-08-09 23:30:00 +0800" endDate="2026-08-10 02:00:00 +0800" value="HKCategoryValueSleepAnalysisAsleepDeep"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" startDate="2026-08-10 03:00:00 +0800" endDate="2026-08-10 03:30:00 +0800" value="HKCategoryValueSleepAnalysisAwake"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" startDate="2026-08-10 03:30:00 +0800" endDate="2026-08-10 07:00:00 +0800" value="HKCategoryValueSleepAnalysisAsleepREM"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" totalDistance="5" totalDistanceUnit="km" startDate="2026-08-10 18:00:00 +0800" endDate="2026-08-10 18:30:00 +0800">
  <WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" average="148" unit="count/min"/>
 </Workout>
</HealthData>
"""


class TestImporters(unittest.TestCase):
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

    def set_hr_max(self, value):
        store.init(self.base)
        profile = store.load_profile(self.base)
        profile["hr_max"] = value
        store.write_json(os.path.join(self.base, "profile.json"), profile)

    def test_fit_session_zones_and_developer_fields(self):
        self.set_hr_max(190)
        path = self.put("run.fit", make_fit([100, 120, 140, 160, 180, 180]))
        result = importers.import_paths(self.base, [path])
        self.assertEqual((result["files"], result["activities"]), (1, 1))
        item = store.load_activities(self.base)[0]
        self.assertEqual(item["date"], "20260810")
        self.assertEqual(item["type"], "running")
        self.assertEqual((item["duration_s"], item["distance_m"], item["avg_hr"]), (55.0, 150.0, 150))
        self.assertEqual(item["hr_zone_minutes"], [0.2] * 5)
        self.assertEqual(item["source"], "device")
        self.assertIn("+", item["start"][10:])

    def test_fit_without_hr_max_has_no_zones_and_reimport_is_idempotent(self):
        path = self.put("run.fit", make_fit([100, 120, 140, 160, 180, 180]))
        importers.import_paths(self.base, [path])
        again = importers.import_paths(self.base, [path])
        self.assertEqual(again["activities"], 0)
        self.assertNotIn("hr_zone_minutes", store.load_activities(self.base)[0])

    def test_gpx_distance_and_zones(self):
        self.set_hr_max(200)
        importers.import_paths(self.base, [self.put("a.gpx", GPX)])
        item = store.load_activities(self.base)[0]
        self.assertEqual(item["duration_s"], 240.0)
        self.assertAlmostEqual(item["distance_m"], 1001, delta=5)
        self.assertEqual(item["type"], "running")
        self.assertEqual(item["avg_hr"], 153)
        self.assertEqual(item["hr_zone_minutes"], [0, 1.0, 0, 1.0, 0])  # gaps over 60 s are capped

    def test_tcx(self):
        importers.import_paths(self.base, [self.put("b.tcx", TCX)])
        item = store.load_activities(self.base)[0]
        self.assertEqual((item["type"], item["duration_s"], item["distance_m"], item["avg_hr"]),
                         ("biking", 600.0, 3000.0, 130))

    def test_csv_aliases_dates_and_source(self):
        result = importers.import_paths(self.base, [self.put("daily.csv", CSV)])
        daily = store.load_daily(self.base)
        self.assertEqual(sorted(daily), ["20260810", "20260811", "20260812"])
        self.assertEqual(daily["20260810"]["hrv_rmssd"], 62)
        self.assertEqual(daily["20260810"]["resting_hr"], 50)
        self.assertEqual(daily["20260810"]["sleep_duration_min"], 420)
        self.assertEqual(daily["20260812"]["weight_kg"], 70.0)
        self.assertEqual(daily["20260810"]["_sources"]["hrv_rmssd"], "import")
        self.assertEqual(result["daily_changes"], 9)

    def test_apple_health_xml_and_zip(self):
        result = importers.import_paths(self.base, [self.put("export.xml", APPLE)])
        daily = store.load_daily(self.base)["20260810"]
        self.assertEqual(daily["resting_hr"], 52.0)
        self.assertEqual(daily["weight_kg"], 70.5)
        self.assertEqual(daily["sleep_duration_min"], 450.0)
        self.assertNotIn("hrv_rmssd", daily)
        self.assertEqual(daily["_sources"]["resting_hr"], "device")
        self.assertEqual(len(store.load_sleep_intervals(self.base)), 1)
        item = store.load_activities(self.base)[0]
        self.assertEqual((item["type"], item["distance_m"], item["duration_s"], item["avg_hr"]),
                         ("running", 5000.0, 1800.0, 148))
        self.assertTrue(any("SDNN" in entry["reason"] for entry in result["skipped"]))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("apple_health_export/export.xml", APPLE)
        other = os.path.join(self.tmp.name, "other")
        importers.import_paths(other, [self.put("export.zip", buffer.getvalue())])
        self.assertEqual(store.load_daily(other)["20260810"]["sleep_duration_min"], 450.0)

    def test_directory_recursion_and_skips(self):
        sub = os.path.join(self.inbox, "nested")
        os.makedirs(sub)
        self.put("notes.txt", "hello")
        self.put("bad.csv", "foo,bar\n1,2\n")
        self.put("broken.fit", b"not a fit file")
        with open(os.path.join(sub, "c.gpx"), "w", encoding="utf-8") as handle:
            handle.write(GPX)
        result = importers.import_paths(self.base, [self.inbox, os.path.join(self.tmp.name, "missing")])
        self.assertEqual((result["files"], result["activities"]), (1, 1))
        reasons = {os.path.basename(item["path"]): item["reason"] for item in result["skipped"]}
        self.assertEqual(set(reasons), {"notes.txt", "bad.csv", "broken.fit", "missing"})
        self.assertEqual(reasons["missing"], "路径不存在")


if __name__ == "__main__":
    unittest.main()
