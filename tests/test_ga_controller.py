import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ga_wechat_clawbot.ga_controller import GATurnController


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = io.StringIO(lines)
        self.stderr = io.StringIO("")
        self.returncode = None
        self.terminated = False

    def wait(self, timeout=None):
        self.returncode = 0 if not self.terminated else 130
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 130

    def kill(self):
        self.returncode = -9


class ControllerTests(unittest.TestCase):
    def test_list_llms_uses_probe_worker_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GATurnController("/tmp/GenericAgent", tmp)
            completed = type("Completed", (), {"returncode": 0, "stdout": '{"ok": true, "llms": [{"idx": 0, "name": "m", "current": true}]}', "stderr": ""})
            with patch("subprocess.run", return_value=completed) as run_mock:
                info = controller.list_llms()
            self.assertTrue(info["ok"])
            self.assertEqual(info["llms"][0]["name"], "m")
            self.assertEqual(run_mock.call_args.kwargs["encoding"], "utf-8")
            self.assertEqual(run_mock.call_args.kwargs["errors"], "replace")
            self.assertEqual(run_mock.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")

    def test_start_turn_writes_request_files_and_parses_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GATurnController("/tmp/GenericAgent", tmp)
            lines = '\n'.join([
                json.dumps({"event": "progress", "turn": 1, "summary": "s"}),
                json.dumps({"event": "done", "raw_text": "ok", "generated_files": []}),
                "",
            ])
            fake = _FakeProcess(lines)
            events = []
            exits = []
            with patch("subprocess.Popen", return_value=fake) as popen_mock:
                running = controller.start_turn("hello", ["/tmp/a.png"], events.append, exits.append)
                running.stdout_thread.join(1)
                running.wait_thread.join(1)
            self.assertEqual(events[0]["event"], "progress")
            self.assertEqual(events[-1]["event"], "done")
            self.assertEqual(exits, [0])
            self.assertEqual((running.request_dir / "prompt.txt").read_text("utf-8"), "hello")
            self.assertEqual(json.loads((running.request_dir / "images.json").read_text("utf-8")), ["/tmp/a.png"])
            self.assertEqual(popen_mock.call_args.kwargs["encoding"], "utf-8")
            self.assertEqual(popen_mock.call_args.kwargs["errors"], "replace")
            self.assertEqual(popen_mock.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")


if __name__ == "__main__":
    unittest.main()
