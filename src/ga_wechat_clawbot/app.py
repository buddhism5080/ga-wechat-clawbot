from __future__ import annotations

import time

from .config import AppConfig
from .session import SessionRegistry
from .wechat_client import WxClawClient

HELP_TEXT = """📖 微信前端命令
/help 或 /commands - 显示帮助
/status 或 /state - 查看当前会话状态
/llm - 查看模型列表
/llm current - 查看当前模型
/llm N 或 /llm set N - 切换到第 N 个模型
/stop 或 /abort - 停止当前任务
/new 或 /reset - 清空当前会话上下文

会话按 context_token 强隔离。""".strip()

LLM_USAGE = "用法：/llm、/llm current、/llm N、/llm set N"
COMMAND_ALIASES = {
    "/commands": "/help",
    "/state": "/status",
    "/abort": "/stop",
    "/reset": "/new",
    "/clear": "/new",
    "/model": "/llm",
}


class WeChatApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = WxClawClient(
            token_file=config.wechat.token_file,
            media_dir=config.wechat.media_dir,
            voice_encoder_cmd=config.wechat.voice_encoder_cmd,
        )
        self.sessions = SessionRegistry(config, self.client)

    def ensure_login(self) -> None:
        if not self.client.token:
            self.client.login_qr()

    def is_allowed(self, user_id: str) -> bool:
        allowed = self.config.wechat.allowed_users
        return not allowed or "*" in allowed or user_id in allowed

    @staticmethod
    def session_key(message) -> str:
        return message.context_token or message.from_user_id or f"msg-{message.message_id}"

    @staticmethod
    def _normalize_command_name(op: str) -> str:
        return COMMAND_ALIASES.get(op.lower(), op.lower())

    def reply(self, user_id: str, context_token: str, text: str) -> None:
        self.client.send_text(user_id, text, context_token=context_token)

    def _reply_for_session(self, message, session, text: str) -> None:
        context_token = message.context_token or getattr(session, "_current_context_token", "")
        self.reply(message.from_user_id, context_token, text)

    def _handle_llm_command(self, session, args: list[str]) -> str:
        if not args or args[0].lower() in {"list", "ls"}:
            return session.list_llms_text()
        head = args[0].lower()
        if head in {"current", "now"}:
            if len(args) != 1:
                return LLM_USAGE
            return session.current_llm_text()
        if head in {"set", "use", "switch"}:
            if len(args) != 2:
                return LLM_USAGE
            target = args[1]
        else:
            if len(args) != 1:
                return LLM_USAGE
            target = args[0]
        try:
            llm_no = int(target)
        except ValueError:
            return LLM_USAGE
        return session.switch_llm(llm_no)

    def _bind_message_session(self, message, session):
        if message.context_token:
            return self.sessions.bind(message.context_token, session)
        if message.from_user_id:
            return self.sessions.bind(message.from_user_id, session)
        return session

    def _resolve_message_session(self, message):
        if message.context_token:
            direct = self.sessions.find(message.context_token)
            if direct is not None:
                return direct
            running = self.sessions.find_latest_for_user(message.from_user_id, running_only=True) if message.from_user_id else None
            if running is not None:
                return self._bind_message_session(message, running)
            user_session = self.sessions.find(message.from_user_id) if message.from_user_id else None
            if user_session is not None:
                return self._bind_message_session(message, user_session)
            return self.sessions.get(message.context_token)
        if message.from_user_id:
            running = self.sessions.find_latest_for_user(message.from_user_id, running_only=True)
            if running is not None:
                return running
            direct = self.sessions.find(message.from_user_id)
            if direct is not None:
                return direct
            return self.sessions.get(message.from_user_id)
        return self.sessions.get(f"msg-{message.message_id}")

    def _resolve_command_session(self, message, op: str):
        direct = self.sessions.find(message.context_token) if message.context_token else None
        if op == "/stop":
            if direct is not None and direct.is_running:
                return self._bind_message_session(message, direct)
            fallback = self.sessions.find_latest_for_user(message.from_user_id, running_only=True) if message.from_user_id else None
            return self._bind_message_session(message, fallback) if fallback is not None else direct
        if op in {"/status", "/new", "/llm"}:
            fallback = self.sessions.find_latest_for_user(message.from_user_id, running_only=False) if message.from_user_id else None
            target = direct or fallback
            return self._bind_message_session(message, target) if target is not None else None
        return direct

    def _missing_session_reply(self, op: str) -> str:
        if op == "/stop":
            return "ℹ️ 当前没有正在运行的任务。"
        if op == "/new":
            return "ℹ️ 当前没有可清空的会话。先发送一条普通消息开始。"
        if op == "/status":
            return "ℹ️ 当前没有活动会话。先发送一条普通消息开始。"
        if op == "/llm":
            return "ℹ️ 当前还没有活动会话。先发送一条普通消息开始，再使用 /llm。"
        return "ℹ️ 当前没有可用会话。"

    def handle_command(self, message, text: str) -> None:
        parts = text.split()
        raw_op = parts[0] if parts else ""
        op = self._normalize_command_name(raw_op)
        args = parts[1:]
        if op == "/help":
            self.reply(message.from_user_id, message.context_token, HELP_TEXT)
            return
        session = self._resolve_command_session(message, op)
        if session is None:
            self.reply(message.from_user_id, message.context_token, self._missing_session_reply(op))
            return
        if op == "/status":
            self._reply_for_session(message, session, session.status_text())
        elif op == "/stop":
            self._reply_for_session(message, session, session.stop())
        elif op == "/new":
            self._reply_for_session(message, session, session.reset())
        elif op == "/llm":
            self._reply_for_session(message, session, self._handle_llm_command(session, args))
        else:
            self.reply(message.from_user_id, message.context_token, f"⚠️ 未知命令：{raw_op}\n\n{HELP_TEXT}")

    def handle_message(self, message) -> None:
        if not self.is_allowed(message.from_user_id):
            print(f"[WeChatApp] unauthorized user={message.from_user_id}")
            return
        text = (message.text or "").strip()
        if text.startswith("/"):
            self.handle_command(message, text)
            return
        if not text and not message.attachments:
            return
        session = self._resolve_message_session(message)
        session.submit_turn(message.from_user_id, message.context_token, text, message.attachments)

    def run_forever(self) -> None:
        self.ensure_login()
        print(f"[WeChatApp] started bot_id={self.client.bot_id}")
        while True:
            try:
                self.sessions.evict_idle()
                for message in self.client.iter_user_messages(timeout=30):
                    print(
                        f"[WeChatApp] recv user={message.from_user_id} ctx={message.context_token[:12]} "
                        f"text={message.text[:80]!r} attachments={len(message.attachments)}"
                    )
                    self.handle_message(message)
            except KeyboardInterrupt:
                print("[WeChatApp] exiting")
                return
            except Exception as exc:
                print(f"[WeChatApp] loop error: {exc}")
                time.sleep(5)
