# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_coach_runtime import good_response


class McpClient:
    def __init__(self, home):
        env = dict(os.environ, SPORTS_EVAL_HOME=home, PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "scripts", "sec.py"), "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        if notify:
            return None
        return json.loads(self.proc.stdout.readline().decode("utf-8"))

    def tool(self, name, **arguments):
        reply = self.send("tools/call", {"name": name, "arguments": arguments})
        return reply["result"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()


class McpServerTest(unittest.TestCase):
    def test_stdio_end_to_end(self):
        with tempfile.TemporaryDirectory() as home:
            subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "sec.py"),
                            "--home", home, "init", "--demo"], check=True,
                           stdout=subprocess.DEVNULL)
            client = McpClient(home)
            try:
                init = client.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                                  "clientInfo": {"name": "t", "version": "0"}})
                self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
                client.send("notifications/initialized", notify=True)
                self.assertEqual(client.send("ping")["result"], {})

                tools = client.send("tools/list")["result"]["tools"]
                names = {tool["name"] for tool in tools}
                self.assertEqual(names, {"status", "import_files", "log_event", "weekly_report",
                                         "start_coach_job", "submit_coach_response",
                                         "list_pending_suggestions", "run_eval", "get_profile"})
                self.assertTrue(all(tool["inputSchema"]["type"] == "object" for tool in tools))
                self.assertNotIn("decide", names)
                self.assertNotIn("apply_plan", names)

                status = client.tool("status")
                self.assertFalse(status["isError"])
                self.assertEqual(json.loads(status["content"][0]["text"]), status["structuredContent"])

                job = client.tool("start_coach_job", query="今天该怎么练？")["structuredContent"]
                self.assertTrue(job["job_id"].startswith("job_"))
                self.assertIn("coach-recovery", job["skill_texts"])
                context = job["context"]
                response = good_response(context)
                done = client.tool("submit_coach_response", job_id=job["job_id"], response=response)
                self.assertFalse(done["isError"])
                self.assertTrue(done["structuredContent"]["valid"], done["structuredContent"]["errors"])

                bad = good_response(context)
                bad["observations"][0]["evidence"][0]["observed_value"] = 123456
                job2 = client.tool("start_coach_job", query="今天该怎么练？")["structuredContent"]
                bad["task"] = job2["task"]
                fail = client.tool("submit_coach_response", job_id=job2["job_id"], response=bad)
                self.assertFalse(fail["structuredContent"]["valid"])

                self.assertEqual(client.tool("list_pending_suggestions")["structuredContent"]["suggestions"], [])
                missing = client.send("tools/call", {"name": "decide", "arguments": {}})
                self.assertIn("error", missing)

                prompts = client.send("prompts/list")["result"]["prompts"]
                self.assertEqual({p["name"] for p in prompts}, {"coach_daily", "coach_weekly"})
                got = client.send("prompts/get", {"name": "coach_daily"})["result"]
                self.assertIn("start_coach_job", got["messages"][0]["content"]["text"])
                resources = client.send("resources/list")["result"]["resources"]
                self.assertEqual(len(resources), 8)
                read = client.send("resources/read", {"uri": resources[0]["uri"]})["result"]
                self.assertIn("name:", read["contents"][0]["text"])
                self.assertIn("error", client.send("no/such/method"))
            finally:
                client.close()

    def test_http_rejects_non_loopback(self):
        import mcp_server
        with self.assertRaises(ValueError):
            mcp_server.serve_http(mcp_server.Server(), "0.0.0.0:9")


if __name__ == "__main__":
    unittest.main()
