import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ga_wechat_clawbot.app import HELP_TEXT, WeChatApp
from ga_wechat_clawbot.types import InboundMessage


class _DummyClient:
    def __init__(self):
        self.sent_text = []

    def send_text(self, user_id, text, context_token=""):
        self.sent_text.append((user_id, context_token, text))


class _DummySession:
    def __init__(self):
        self.calls = []

    def status_text(self):
        self.calls.append(("status", None))
        return "STATUS"

    def stop(self):
        self.calls.append(("stop", None))
        return "STOP"

    def reset(self):
        self.calls.append(("reset", None))
        return "RESET"

    def list_llms_text(self):
        self.calls.append(("llm-list", None))
        return "LLM LIST"

    def current_llm_text(self):
        self.calls.append(("llm-current", None))
        return "LLM CURRENT"

    def switch_llm(self, llm_no):
        self.calls.append(("llm-switch", llm_no))
        return f"LLM SWITCH {llm_no}"

    def submit_turn(self, user_id, context_token, text, attachments):
        self.calls.append(("submit", text, len(attachments)))


class _DummyRegistry:
    def __init__(self, session):
        self.session = session
        self.last_key = None

    def get(self, key):
        self.last_key = key
        return self.session


class AppTests(unittest.TestCase):
    def _app(self, session=None):
        app = WeChatApp.__new__(WeChatApp)
        app.config = SimpleNamespace(wechat=SimpleNamespace(allowed_users=set()))
        app.client = _DummyClient()
        app.sessions = _DummyRegistry(session or _DummySession())
        return app

    def _message(self, text):
        return InboundMessage(
            message_id=1,
            from_user_id="u1",
            to_user_id="bot",
            context_token="ctx-1",
            text=text,
            attachments=[],
            raw={},
        )

    def test_help_alias_commands(self):
        app = self._app()
        app.handle_message(self._message("/commands"))
        self.assertEqual(app.client.sent_text[-1][2], HELP_TEXT)

    def test_unknown_command_shows_error_and_help(self):
        app = self._app()
        app.handle_message(self._message("/wat"))
        reply = app.client.sent_text[-1][2]
        self.assertIn("未知命令", reply)
        self.assertIn("/help", reply)

    def test_llm_subcommands(self):
        session = _DummySession()
        app = self._app(session)

        app.handle_message(self._message("/llm current"))
        self.assertEqual(app.client.sent_text[-1][2], "LLM CURRENT")

        app.handle_message(self._message("/llm set 2"))
        self.assertEqual(app.client.sent_text[-1][2], "LLM SWITCH 2")
        self.assertIn(("llm-switch", 2), session.calls)

    def test_reset_alias(self):
        session = _DummySession()
        app = self._app(session)
        app.handle_message(self._message("/reset"))
        self.assertEqual(app.client.sent_text[-1][2], "RESET")
        self.assertIn(("reset", None), session.calls)


if __name__ == "__main__":
    unittest.main()
