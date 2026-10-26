import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import api  # noqa: E402
import web  # noqa: E402


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = cls.tmp.name
        api.init(cls.base, demo=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(cls.base))
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        data = None if body is None else (body if isinstance(body, bytes) else
                                          json.dumps(body).encode("utf-8"))
        request = urllib.request.Request(self.url + path, data=data, headers=headers or {},
                                         method="POST" if data is not None else "GET")
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_page_and_snapshot(self):
        with urllib.request.urlopen(self.url + "/") as response:
            self.assertIn("体征画", response.read().decode("utf-8"))
        snapshot = self.request("/api/snapshot")
        self.assertEqual(snapshot["safety_gate"]["level"], "green")
        self.assertGreater(len(self.request("/api/history")["daily"]), 40)

    def test_ask_returns_paste_ready_prompt_and_loose_json_is_parsed(self):
        job = self.request("/api/ask", {"query": "今天练什么"})
        self.assertIn("coach_context", job["prompt"])
        result = self.request("/api/complete", {"job_id": job["job_id"],
                                                "response": "回复如下 ```json\n{\"kind\": \"x\"}\n```"})
        self.assertFalse(result["valid"])
        self.assertTrue(any(error["code"] == "contract" for error in result["errors"]))

    def test_foreign_host_and_origin_are_refused(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "http://evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/status", headers=headers)
            self.assertEqual(caught.exception.code, 403)

    def test_upload_imports_into_workspace(self):
        csv = "date,hrv,resting_hr\n2026-08-12,47,48\n".encode("utf-8")
        result = self.request("/api/upload", csv, {"X-Filename": "daily.csv"})
        self.assertEqual(result["daily_changes"], 2)
        self.assertTrue(os.path.exists(os.path.join(self.base, "inbox", "daily.csv")))
        view = self.request("/api/provenance?date=20260812&metric=hrv_rmssd")
        self.assertEqual(view["metrics"]["hrv_rmssd"]["file"]["name"], "daily.csv")
        demo = self.request("/api/provenance?date=20260810")
        self.assertTrue(all(item["file"] is None for item in demo["metrics"].values()))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/provenance")
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
