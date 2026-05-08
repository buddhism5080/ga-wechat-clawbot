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

    def handle_command(self, session, message, text: str) -> None:
        parts = text.split()
        raw_op = parts[0] if parts else ""
        op = self._normalize_command_name(raw_op)
        args = parts[1:]
        if op == "/help":
            self.reply(message.from_user_id, message.context_token, HELP_TEXT)
        elif op == "/status":
            self.reply(message.from_user_id, message.context_token, session.status_text())
        elif op == "/stop":
            self.reply(message.from_user_id, message.context_token, session.stop())
        elif op == "/new":
            self.reply(message.from_user_id, message.context_token, session.reset())
        elif op == "/llm":
            self.reply(message.from_user_id, message.context_token, self._handle_llm_command(session, args))
        else:
            self.reply(message.from_user_id, message.context_token, f"⚠️ 未知命令：{raw_op}\n\n{HELP_TEXT}")

    def handle_message(self, message) -> None:
        if not self.is_allowed(message.from_user_id):
            print(f"[WeChatApp] unauthorized user={message.from_user_id}")
            return
        session = self.sessions.get(self.session_key(message))
        text = (message.text or "").strip()
        if text.startswith("/"):
            self.handle_command(session, message, text)
            return
        if not text and not message.attachments:
            return
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
