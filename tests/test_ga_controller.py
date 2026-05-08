import io
import json
import os
import sys
import tempfile
import threading
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
    def test_controller_uses_shared_ga_root_temp_without_mirroring_session_dir(self):
        with tempfile.TemporaryDirectory() as ga_tmp:
            ga_root = Path(ga_tmp)
            (ga_root / "temp").mkdir()
            (ga_root / "memory").mkdir()
            (ga_root / "memory" / "memory_management_sop.md").write_text("sop", "utf-8")
            (ga_root / "ga.py").write_text("print('ga')\n", "utf-8")
            with tempfile.TemporaryDirectory() as tmp:
                session_dir = Path(tmp)
                controller = GATurnController(ga_root, session_dir)
                self.assertEqual(controller.work_dir, ga_root / "temp")
                self.assertFalse((session_dir / "memory").exists())
                self.assertFalse((session_dir / "ga.py").exists())

    def test_list_llms_uses_probe_worker_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GATurnController("/tmp/GenericAgent", tmp)
            completed = type("Completed", (), {"returncode": 0, "stdout": '{"ok": true, "llms": [{"idx": 0, "name": "m", "current": true}]}', "stderr": ""})
            with patch("ga_wechat_clawbot.ga_controller.hidden_windows_subprocess_kwargs", return_value={"creationflags": 123, "startupinfo": "si"}):
                with patch("subprocess.run", return_value=completed) as run_mock:
                    info = controller.list_llms()
            self.assertTrue(info["ok"])
            self.assertEqual(info["llms"][0]["name"], "m")
            self.assertEqual(run_mock.call_args.kwargs["encoding"], "utf-8")
            self.assertEqual(run_mock.call_args.kwargs["errors"], "replace")
            self.assertEqual(run_mock.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")
            self.assertEqual(run_mock.call_args.kwargs["cwd"], controller.ga_root)
            self.assertEqual(run_mock.call_args.kwargs["creationflags"], 123)
            self.assertEqual(run_mock.call_args.kwargs["startupinfo"], "si")

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
            with patch("ga_wechat_clawbot.ga_controller.hidden_windows_subprocess_kwargs", return_value={"creationflags": 123, "startupinfo": "si"}):
                with patch("subprocess.Popen", return_value=fake) as popen_mock:
                    running = controller.start_turn("hello", ["/tmp/a.png"], events.append, exits.append)
                    running.stdout_thread.join(1)
                    running.wait_thread.join(1)
            self.assertEqual(events[0]["event"], "progress")
            self.assertEqual(events[-1]["event"], "done")
            self.assertEqual(exits, [0])
            self.assertEqual((running.request_dir / "prompt.txt").read_text("utf-8"), "hello")
            self.assertEqual(json.loads((running.request_dir / "images.json").read_text("utf-8")), ["/tmp/a.png"])
            self.assertEqual(popen_mock.call_args.kwargs["cwd"], controller.ga_root)
            self.assertIn("--work-dir", popen_mock.call_args.args[0])
            self.assertEqual(popen_mock.call_args.kwargs["encoding"], "utf-8")
            self.assertEqual(popen_mock.call_args.kwargs["errors"], "replace")
            self.assertEqual(popen_mock.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")
            self.assertEqual(popen_mock.call_args.kwargs["creationflags"], 123)
            self.assertEqual(popen_mock.call_args.kwargs["startupinfo"], "si")

    def test_start_turn_rejects_parallel_runs_for_same_ga_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared_root = Path(tmp) / "ga-root"
            shared_root.mkdir()
            controller_a = GATurnController(shared_root, Path(tmp) / "session-a")
            controller_b = GATurnController(shared_root, Path(tmp) / "session-b")

            class _BlockingProcess(_FakeProcess):
                def __init__(self):
                    super().__init__("")
                    self._release = threading.Event()
                def wait(self, timeout=None):
                    self._release.wait(timeout)
                    self.returncode = 0 if not self.terminated else 130
                    return self.returncode
                def release(self):
                    self._release.set()

            fake = _BlockingProcess()
            with patch("subprocess.Popen", return_value=fake):
                running = controller_a.start_turn("hello", [], lambda payload: None, lambda rc: None)
                with self.assertRaises(RuntimeError):
                    controller_b.start_turn("world", [], lambda payload: None, lambda rc: None)
                fake.release()
                running.wait_thread.join(1)

    def test_start_turn_releases_root_lock_when_setup_fails_before_popen(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared_root = Path(tmp) / "ga-root"
            shared_root.mkdir()
            controller_a = GATurnController(shared_root, Path(tmp) / "session-a")
            controller_b = GATurnController(shared_root, Path(tmp) / "session-b")
            real_write_text = Path.write_text
            calls = {"count": 0}

            def flaky_write(path_obj, content, *args, **kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise OSError("boom")
                return real_write_text(path_obj, content, *args, **kwargs)

            with patch("pathlib.Path.write_text", new=flaky_write):
                with self.assertRaises(OSError):
                    controller_a.start_turn("hello", [], lambda payload: None, lambda rc: None)
            self.assertNotIn(controller_a.ga_root, controller_a._active_by_root)
            fake = _FakeProcess("")
            with patch("subprocess.Popen", return_value=fake):
                running = controller_b.start_turn("world", [], lambda payload: None, lambda rc: None)
                running.stdout_thread.join(1)
                running.wait_thread.join(1)

    def test_intervene_writes_ipc_file_for_running_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GATurnController("/tmp/GenericAgent", tmp)
            controller.running = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            self.assertTrue(controller.intervene("hello steer"))
            self.assertEqual((Path(tmp) / "ipc" / "_intervene").read_text("utf-8"), "hello steer\n")


if __name__ == "__main__":
    unittest.main()
