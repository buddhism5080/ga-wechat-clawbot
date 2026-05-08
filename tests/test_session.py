import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ga_wechat_clawbot.config import AppConfig, GAConfig, StorageConfig, WeChatConfig
from ga_wechat_clawbot.session import SessionActor
from ga_wechat_clawbot.types import AttachmentRef


class _DummyClient:
    def __init__(self):
        self.sent_text = []
        self.sent_path = []

    def send_text(self, user_id, text, context_token=""):
        self.sent_text.append((user_id, context_token, text))

    def send_path(self, user_id, path, context_token=""):
        self.sent_path.append((user_id, context_token, path))

    def get_typing_ticket(self, user_id, context_token=""):
        return ""

    def send_typing(self, user_id, typing_ticket="", cancel=False):
        return None


class _FakeController:
    def __init__(self):
        self.running = None
        self.started = None
        self.interventions = []
        self.abort_calls = 0

    def start_turn(self, prompt, images, on_event, on_exit):
        self.started = (prompt, images)
        process = type("P", (), {"poll": lambda self: None})()
        self.running = type("Running", (), {"process": process, "started_at": 0.0})()
        return self.running

    def intervene(self, prompt):
        if self.running is None or self.running.process.poll() is not None:
            return False
        self.interventions.append(prompt)
        return True

    def list_llms(self):
        return {"llms": [{"idx": 0, "name": "gpt", "current": True}], "llm_no": 0}

    def switch_llm(self, llm_no):
        return {"llm_no": llm_no, "name": f"model-{llm_no}"}

    def abort(self):
        self.abort_calls += 1
        self.running = None

    def reset_state(self):
        return None


class SessionTests(unittest.TestCase):
    def _config(self, tmp):
        return AppConfig(
            ga=GAConfig(root=Path("/tmp/GenericAgent"), python="python3", default_llm_no=0, turn_timeout_sec=900, session_idle_ttl_sec=3600),
            wechat=WeChatConfig(allowed_users=set(), token_file=Path("~/.wxbot/token.json"), media_dir=Path(tmp) / "media", voice_encoder_cmd="", progress_interval_sec=12, progress_turn_stride=2, heartbeat_interval_sec=60),
            storage=StorageConfig(root=Path(tmp) / "state", log_dir=Path(tmp) / "logs"),
            config_path=Path(tmp) / "config.toml",
        )

    def test_build_prompt_and_submit_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            session.controller = _FakeController()
            image_path = Path(tmp) / "img.png"
            image_path.write_bytes(b"png")
            attachments = [AttachmentRef(kind="image", path=str(image_path), name="img.png"), AttachmentRef(kind="voice", path="/tmp/a.silk", name="a.silk", transcript="你好")]
            session.submit_turn("u1", "ctx-token", "分析一下", attachments)
            prompt, images = session.controller.started
            self.assertIn("### 用户消息", prompt)
            self.assertIn("transcript=你好", prompt)
            self.assertEqual(images, [str(image_path)])

    def test_submit_turn_without_context_token_preserves_existing_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            session.controller = _FakeController()
            session._current_context_token = "ctx-token"
            session.submit_turn("u1", "", "继续", [])
            self.assertEqual(session._current_context_token, "ctx-token")

    def test_busy_session_steers_running_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            controller.running = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            session.controller = controller
            session._current_context_token = "ctx-token"
            session.submit_turn("u1", "", "hello", [])
            self.assertEqual(session._current_context_token, "ctx-token")
            self.assertIn("已插入当前任务", client.sent_text[-1][2])
            self.assertIn("hello", controller.interventions[-1])

    def test_pending_interventions_reset_after_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            controller.running = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            session.controller = controller
            session.submit_turn("u1", "ctx-token", "first", [])
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            session._on_event({"event": "progress", "turn": 1, "summary": "step-1", "tool_calls": []})
            session.submit_turn("u1", "ctx-token", "second", [])
            self.assertIn("second", controller.interventions[-1])
            self.assertNotIn("first", controller.interventions[-1])

    def test_aborted_event_replies_with_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            session._on_event({"event": "aborted", "message": "用户请求停止"})
            self.assertIn("任务已中止", client.sent_text[-1][2])

    def test_progress_event_throttles_turn_zero_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            session._on_event({"event": "progress", "turn": 0, "summary": "step-1", "tool_calls": []})
            session._on_event({"event": "progress", "turn": 0, "summary": "step-2", "tool_calls": []})
            self.assertEqual(len(client.sent_text), 1)

    def test_progress_event_does_not_hide_new_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            session._on_event({"event": "progress", "turn": 1, "summary": "step-1", "tool_calls": []})
            session._on_event({"event": "progress", "turn": 2, "summary": "step-2", "tool_calls": []})
            self.assertEqual(len(client.sent_text), 2)
            self.assertIn("第 2 轮", client.sent_text[-1][2])

    def test_progress_event_can_disable_throttling(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            cfg.wechat.progress_interval_sec = 0
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            session._on_event({"event": "progress", "turn": 1, "summary": "step-1", "tool_calls": []})
            session._on_event({"event": "progress", "turn": 1, "summary": "step-2", "tool_calls": []})
            self.assertEqual(len(client.sent_text), 2)

    def test_processing_ping_sends_keepalive_after_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            running = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            controller.running = running
            session.controller = controller
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            session._latest_progress_summary = "准备检查 thunderbird-agent 状态"
            session._last_user_visible_update_at = 0.0
            self.assertTrue(session._maybe_send_processing_ping(running, now=60.0))
            self.assertIn("还在处理中", client.sent_text[-1][2])
            self.assertIn("thunderbird-agent", client.sent_text[-1][2])

    def test_processing_ping_uses_configured_heartbeat_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            cfg.wechat.heartbeat_interval_sec = 75
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            running = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            controller.running = running
            session.controller = controller
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            session._last_user_visible_update_at = 0.0
            self.assertFalse(session._maybe_send_processing_ping(running, now=74.0))
            self.assertTrue(session._maybe_send_processing_ping(running, now=75.0))

    def test_processing_ping_ignores_stale_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            stale = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            current = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            controller.running = current
            session.controller = controller
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            session._last_user_visible_update_at = 0.0
            self.assertFalse(session._maybe_send_processing_ping(stale, now=25.0))
            self.assertEqual(client.sent_text, [])

    def test_timeout_helper_ignores_stale_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            stale = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            current = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            controller.running = current
            session.controller = controller
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            self.assertFalse(session._maybe_timeout_run(stale, started_at=0.0, now=2000.0))
            self.assertEqual(controller.abort_calls, 0)
            self.assertEqual(client.sent_text, [])

    def test_send_generated_files_accepts_windows_style_file_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            work_file = session.session_dir / "work" / "结果.png"
            work_file.parent.mkdir(parents=True, exist_ok=True)
            work_file.write_bytes(b"png")
            session._send_generated_files([r"C:\\temp\\结果.png"])
            self.assertEqual(client.sent_path[-1][2], str(work_file.resolve()))

    def test_switch_llm_rejected_while_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            controller.running = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            session.controller = controller
            text = session.switch_llm(2)
            self.assertIn("请先停止", text)

    def test_stop_exit_does_not_show_worker_error_after_user_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            controller.running = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            session.controller = controller
            session._current_user_id = "u1"
            session._current_context_token = "ctx-token"
            stop_text = session.stop()
            self.assertIn("已发送停止信号", stop_text)
            session._on_exit(1)
            self.assertFalse(any("Worker 退出异常" in text for _, _, text in client.sent_text))
            self.assertTrue(any("任务已中止" in text for _, _, text in client.sent_text))

    def test_shutdown_for_restart_aborts_running_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            client = _DummyClient()
            session = SessionActor("ctx-1", cfg, client)
            controller = _FakeController()
            controller.running = type("Running", (), {"process": type("P", (), {"poll": lambda self: None})()})()
            session.controller = controller
            session.shutdown_for_restart()
            self.assertEqual(controller.abort_calls, 1)
            self.assertEqual(client.sent_text, [])


if __name__ == "__main__":
    unittest.main()
