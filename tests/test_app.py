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
    def __init__(self, running=False, user_id="", last_active=0, current_context_token="", session_key="dummy-session"):
        self.calls = []
        self.is_running = running
        self._current_user_id = user_id
        self._current_context_token = current_context_token
        self.last_active = last_active
        self.session_key = session_key

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
        self._current_user_id = user_id
        if context_token:
            self._current_context_token = context_token
        self.calls.append(("submit", text, len(attachments)))


class _DummyRegistry:
    def __init__(self, sessions):
        self.sessions = dict(sessions)
        self.last_key = None
        self.created_keys = []
        self.bound_keys = []
        self.fresh_counter = 0

    def get(self, key):
        self.last_key = key
        session = self.sessions.get(key)
        if session is None:
            session = _DummySession()
            self.sessions[key] = session
            self.created_keys.append(key)
        return session

    def find(self, key):
        self.last_key = key
        return self.sessions.get(key)

    def bind(self, key, session):
        self.last_key = key
        self.sessions[key] = session
        self.bound_keys.append(key)
        return session

    def create_fresh(self, session_key_hint, previous=None, bind_keys=()):
        self.fresh_counter += 1
        key = f"fresh-{self.fresh_counter}"
        session = _DummySession(session_key=key)
        if previous is not None:
            session._current_user_id = previous._current_user_id
            session._current_context_token = previous._current_context_token
            for existing_key, existing_session in list(self.sessions.items()):
                if existing_session is previous:
                    self.sessions[existing_key] = session
        self.sessions[key] = session
        for bind_key in bind_keys:
            if bind_key:
                self.sessions[bind_key] = session
                self.bound_keys.append(bind_key)
        self.created_keys.append(key)
        return session

    def find_latest_for_user(self, user_id, running_only=False):
        candidates = []
        seen = set()
        for session in self.sessions.values():
            sid = id(session)
            if sid in seen:
                continue
            seen.add(sid)
            if session._current_user_id == user_id and (session.is_running if running_only else True):
                candidates.append(session)
        if not candidates:
            return None
        return max(candidates, key=lambda session: session.last_active)


class AppTests(unittest.TestCase):
    def _app(self, sessions=None, command_aliases=None):
        app = WeChatApp.__new__(WeChatApp)
        app.config = SimpleNamespace(wechat=SimpleNamespace(allowed_users=set(), command_aliases=command_aliases or {}))
        app.client = _DummyClient()
        base_sessions = {"ctx-1": _DummySession()} if sessions is None else sessions
        app.sessions = _DummyRegistry(base_sessions)
        return app

    def _message(self, text, context_token="ctx-1"):
        return InboundMessage(
            message_id=1,
            from_user_id="u1",
            to_user_id="bot",
            context_token=context_token,
            text=text,
            attachments=[],
            raw={},
        )

    def test_help_alias_commands(self):
        app = self._app({})
        app.handle_message(self._message("/commands"))
        self.assertEqual(app.client.sent_text[-1][2], HELP_TEXT)
        self.assertEqual(app.sessions.created_keys, [])

    def test_custom_non_slash_help_alias(self):
        app = self._app({}, command_aliases={"帮助": "/help"})
        app.handle_message(self._message("帮助"))
        self.assertEqual(app.client.sent_text[-1][2], HELP_TEXT)
        self.assertEqual(app.sessions.created_keys, [])

    def test_custom_non_slash_llm_alias_preserves_args(self):
        session = _DummySession(user_id="u1", last_active=10)
        app = self._app({"ctx-1": session}, command_aliases={"模型": "/llm"})
        app.handle_message(self._message("模型 current"))
        self.assertEqual(app.client.sent_text[-1][2], "LLM CURRENT")
        self.assertIn(("llm-current", None), session.calls)

    def test_non_slash_alias_matches_exact_token_only(self):
        app = self._app({}, command_aliases={"帮助": "/help"})
        app.handle_message(self._message("帮助我分析一下", context_token=""))
        created = app.sessions.sessions["u1"]
        self.assertIn(("submit", "帮助我分析一下", 0), created.calls)
        self.assertEqual(app.sessions.created_keys, ["u1"])

    def test_unknown_command_shows_error_and_help(self):
        app = self._app({})
        app.handle_message(self._message("/wat"))
        reply = app.client.sent_text[-1][2]
        self.assertIn("未知命令", reply)
        self.assertIn("/help", reply)
        self.assertEqual(app.sessions.created_keys, [])

    def test_llm_subcommands(self):
        session = _DummySession(user_id="u1", last_active=10)
        app = self._app({"ctx-1": session})

        app.handle_message(self._message("/llm current"))
        self.assertEqual(app.client.sent_text[-1][2], "LLM CURRENT")

        app.handle_message(self._message("/llm set 2"))
        self.assertEqual(app.client.sent_text[-1][2], "LLM SWITCH 2")
        self.assertIn(("llm-switch", 2), session.calls)

    def test_reset_alias(self):
        session = _DummySession(user_id="u1", last_active=10)
        app = self._app({"ctx-1": session})
        app.handle_message(self._message("/reset"))
        self.assertIn("已新建会话", app.client.sent_text[-1][2])
        self.assertIn(("reset", None), session.calls)
        self.assertIsNot(app.sessions.sessions["ctx-1"], session)
        self.assertIs(app.sessions.sessions["ctx-1"], app.sessions.sessions["u1"])

    def test_clear_alias_creates_fresh_session(self):
        session = _DummySession(user_id="u1", last_active=10)
        app = self._app({"ctx-1": session})
        app.handle_message(self._message("/clear"))
        self.assertIn("已新建会话", app.client.sent_text[-1][2])
        self.assertIn(("reset", None), session.calls)
        self.assertIsNot(app.sessions.sessions["ctx-1"], session)

    def test_commands_without_context_token_reuse_latest_session(self):
        session = _DummySession(user_id="u1", last_active=10, current_context_token="ctx-1")
        app = self._app({"ctx-1": session})
        app.handle_message(self._message("/status", context_token=""))
        self.assertEqual(app.client.sent_text[-1][1], "ctx-1")
        self.assertEqual(app.client.sent_text[-1][2], "STATUS")
        app.handle_message(self._message("/llm current", context_token=""))
        self.assertEqual(app.client.sent_text[-1][1], "ctx-1")
        self.assertEqual(app.client.sent_text[-1][2], "LLM CURRENT")

    def test_new_without_context_token_creates_fresh_session_from_latest_session(self):
        session = _DummySession(user_id="u1", last_active=10, current_context_token="ctx-1")
        app = self._app({"ctx-1": session, "u1": session})
        app.handle_message(self._message("/new", context_token=""))
        self.assertEqual(app.client.sent_text[-1][1], "ctx-1")
        self.assertIn("已新建会话", app.client.sent_text[-1][2])
        self.assertIn(("reset", None), session.calls)
        fresh = app.sessions.sessions["u1"]
        self.assertIs(app.sessions.sessions["ctx-1"], fresh)
        self.assertIsNot(fresh, session)
        self.assertEqual(app.sessions.created_keys, ["fresh-1"])

    def test_stop_falls_back_to_running_session_for_same_user(self):
        running_session = _DummySession(running=True, user_id="u1", last_active=10, current_context_token="ctx-1")
        idle_session = _DummySession(running=False, user_id="u1", last_active=1)
        app = self._app({"ctx-1": running_session, "u1": idle_session})
        app.handle_message(self._message("/stop", context_token=""))
        self.assertEqual(app.client.sent_text[-1][1], "ctx-1")
        self.assertEqual(app.client.sent_text[-1][2], "STOP")
        self.assertIn(("stop", None), running_session.calls)
        self.assertNotIn(("stop", None), idle_session.calls)
        self.assertEqual(app.sessions.created_keys, [])

    def test_commands_without_session_do_not_create_one(self):
        app = self._app({})
        app.handle_message(self._message("/status", context_token=""))
        self.assertIn("没有活动会话", app.client.sent_text[-1][2])
        app.handle_message(self._message("/llm", context_token=""))
        self.assertIn("还没有活动会话", app.client.sent_text[-1][2])
        app.handle_message(self._message("/stop", context_token=""))
        self.assertIn("没有正在运行的任务", app.client.sent_text[-1][2])
        self.assertEqual(app.sessions.created_keys, [])

    def test_new_without_existing_session_creates_one_immediately(self):
        app = self._app({})
        app.handle_message(self._message("/new", context_token=""))
        self.assertIn("已新建会话", app.client.sent_text[-1][2])
        self.assertEqual(app.sessions.created_keys, ["fresh-1"])
        fresh = app.sessions.sessions["u1"]
        app.handle_message(self._message("开始", context_token=""))
        self.assertIn(("submit", "开始", 0), fresh.calls)
        self.assertEqual(app.sessions.created_keys, ["fresh-1"])

    def test_message_without_context_token_reuses_running_session(self):
        running_session = _DummySession(running=True, user_id="u1", last_active=10, current_context_token="ctx-1")
        app = self._app({"ctx-1": running_session})
        app.handle_message(self._message("继续", context_token=""))
        self.assertIn(("submit", "继续", 0), running_session.calls)
        self.assertEqual(app.sessions.created_keys, [])
        self.assertEqual(app.sessions.bound_keys, ["u1"])

    def test_plain_message_creates_session_when_none_exists(self):
        app = self._app({})
        app.handle_message(self._message("开始", context_token=""))
        created = app.sessions.sessions["u1"]
        self.assertIn(("submit", "开始", 0), created.calls)
        self.assertEqual(app.sessions.created_keys, ["u1"])
        self.assertEqual(app.sessions.bound_keys, ["u1"])

    def test_message_with_context_token_reuses_user_session_and_binds_alias(self):
        user_session = _DummySession(user_id="u1", last_active=10)
        app = self._app({"u1": user_session})
        app.handle_message(self._message("你好", context_token="ctx-1"))
        self.assertIn(("submit", "你好", 0), user_session.calls)
        self.assertEqual(app.sessions.bound_keys, ["ctx-1", "u1"])
        self.assertEqual(app.sessions.created_keys, [])

    def test_second_message_with_new_context_token_reuses_existing_session(self):
        app = self._app({})
        app.handle_message(self._message("第一句", context_token="ctx-1"))
        first = app.sessions.sessions["ctx-1"]
        app.handle_message(self._message("第二句", context_token="ctx-2"))
        self.assertIs(app.sessions.sessions["ctx-2"], first)
        self.assertIs(app.sessions.sessions["u1"], first)
        self.assertEqual(app.sessions.created_keys, ["ctx-1"])
        self.assertEqual(first.calls[-1], ("submit", "第二句", 0))


if __name__ == "__main__":
    unittest.main()
