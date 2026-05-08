from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .config import AppConfig
from .session import SessionRegistry
from .util import hidden_windows_subprocess_kwargs
from .wechat_client import WxClawClient

HELP_TEXT = """📖 微信前端命令
/help 或 /commands - 显示帮助
/status 或 /state - 查看当前会话状态
/llm - 查看模型列表
/llm current - 查看当前模型
/llm N 或 /llm set N - 切换到第 N 个模型
/stop 或 /abort - 停止当前任务
/restart 或 /reboot - 安全重启当前机器人进程
/new 或 /reset 或 /clear - 新建一个干净会话

会话默认复用同一用户的当前 session；收到新的 `context_token` 时会绑定回该 session。发送 `/new` 可显式切到新的 session。也可在配置里为这些命令增加非 `/` 开头别名。`/restart` 会先回复确认，再由外部 helper 安全拉起新进程。""".strip()

LLM_USAGE = "用法：/llm、/llm current、/llm N、/llm set N"
COMMAND_ALIASES = {
    "/commands": "/help",
    "/state": "/status",
    "/abort": "/stop",
    "/reboot": "/restart",
    "/reset": "/new",
    "/clear": "/new",
    "/model": "/llm",
}
SUPPORTED_COMMANDS = {"/help", "/status", "/stop", "/restart", "/new", "/llm"}


class WeChatApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = WxClawClient(
            token_file=config.wechat.token_file,
            media_dir=config.wechat.media_dir,
            voice_encoder_cmd=config.wechat.voice_encoder_cmd,
        )
        self.sessions = SessionRegistry(config, self.client)
        self._shutdown_requested = False

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

    @classmethod
    def _normalize_command_text(cls, text: str) -> str:
        parts = str(text or "").strip().split()
        if not parts:
            return ""
        head = parts[0].strip()
        if not head.startswith("/"):
            head = "/" + head.lstrip("/")
        head = cls._normalize_command_name(head)
        return " ".join([head, *parts[1:]]).strip()

    def _command_aliases(self) -> dict[str, str]:
        aliases = {alias.lower(): target for alias, target in COMMAND_ALIASES.items()}
        custom = getattr(self.config.wechat, "command_aliases", {}) or {}
        for raw_alias, raw_target in custom.items():
            alias = str(raw_alias or "").strip().lower()
            target = self._normalize_command_text(str(raw_target or ""))
            if alias and target:
                aliases[alias] = target
        return aliases

    def _expand_command_text(self, text: str) -> str | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        lowered = raw.lower()
        aliases = self._command_aliases()
        for alias in sorted(aliases, key=len, reverse=True):
            if lowered == alias or lowered.startswith(alias + " "):
                remainder = raw[len(alias):].lstrip()
                target = aliases[alias]
                return f"{target} {remainder}".strip() if remainder else target
        if raw.startswith("/"):
            return raw
        return None

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

    @staticmethod
    def _format_shell_command(parts: list[str]) -> str:
        if os.name == "nt":
            return subprocess.list2cmdline([str(part) for part in parts])
        return " ".join(shlex.quote(str(part)) for part in parts)

    def _restart_command_text(self) -> str:
        configured = str(getattr(self.config.wechat, "restart_command", "") or "").strip()
        if configured:
            return configured
        config_path = Path(getattr(self.config, "config_path", "config.toml")).resolve()
        return self._format_shell_command([
            sys.executable,
            "-m",
            "ga_wechat_clawbot.cli",
            "--config",
            str(config_path),
            "serve",
        ])

    def _launch_restart_helper(self, restart_command: str) -> None:
        config_path = Path(getattr(self.config, "config_path", "config.toml")).resolve()
        workdir = str(config_path.parent)
        log_dir = Path(getattr(self.config.storage, "log_dir", config_path.parent)).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        helper_log = log_dir / "restart_helper.log"
        cmd = [
            sys.executable,
            "-m",
            "ga_wechat_clawbot.restart_helper",
            "--parent-pid",
            str(os.getpid()),
            "--command",
            restart_command,
            "--workdir",
            workdir,
            "--log-file",
            str(helper_log),
        ]
        with helper_log.open("a", encoding="utf-8") as log_handle:
            subprocess.Popen(
                cmd,
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
                **hidden_windows_subprocess_kwargs(),
            )

    def _reply_restart(self, message, text: str) -> None:
        session = self._resolve_command_session(message, "/restart")
        if session is not None:
            self._reply_for_session(message, session, text)
            return
        self.reply(message.from_user_id, message.context_token, text)

    def _request_safe_restart(self, message) -> None:
        if getattr(self, "_shutdown_requested", False):
            self._reply_restart(message, "♻️ 安全重启已在进行中，请稍候。")
            return
        restart_command = self._restart_command_text()
        try:
            self._launch_restart_helper(restart_command)
        except Exception as exc:
            self._reply_restart(message, f"❌ 启动安全重启 helper 失败：{exc}")
            return
        self._shutdown_requested = True
        self._reply_restart(message, "♻️ 已启动安全重启。当前进程会先退出，再由外部 helper 拉起新进程。")

    def _bind_message_session(self, message, session):
        if message.context_token:
            session = self.sessions.bind(message.context_token, session)
        if message.from_user_id:
            session = self.sessions.bind(message.from_user_id, session)
        return session

    def _create_new_session(self, message, previous=None):
        route_context = message.context_token or getattr(previous, "_current_context_token", "")
        route_user = message.from_user_id or getattr(previous, "_current_user_id", "")
        if previous is not None:
            previous.reset()
        hint = route_user or route_context or self.session_key(message)
        session = self.sessions.create_fresh(
            hint,
            previous=previous,
            bind_keys=[route_context, route_user],
        )
        session._current_user_id = route_user
        session._current_context_token = route_context
        return session

    @staticmethod
    def _new_session_reply_text(previous, session) -> str:
        old_key = getattr(previous, "session_key", "") if previous is not None else ""
        if previous is None:
            return f"🆕 已新建会话：`{session.session_key}`\n后续普通消息会进入这个新会话。"
        if old_key and old_key != session.session_key:
            return f"🆕 已新建会话：`{session.session_key}`\n上一会话：`{old_key}`\n后续普通消息会进入这个新会话。"
        return f"🆕 已新建会话：`{session.session_key}`\n后续普通消息会进入这个新会话。"

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
            return self._bind_message_session(message, self.sessions.get(message.context_token))
        if message.from_user_id:
            running = self.sessions.find_latest_for_user(message.from_user_id, running_only=True)
            if running is not None:
                return self._bind_message_session(message, running)
            direct = self.sessions.find(message.from_user_id)
            if direct is not None:
                return self._bind_message_session(message, direct)
            return self._bind_message_session(message, self.sessions.get(message.from_user_id))
        return self.sessions.get(f"msg-{message.message_id}")

    def _resolve_command_session(self, message, op: str):
        direct = self.sessions.find(message.context_token) if message.context_token else None
        if op == "/stop":
            if direct is not None and direct.is_running:
                return self._bind_message_session(message, direct)
            fallback = self.sessions.find_latest_for_user(message.from_user_id, running_only=True) if message.from_user_id else None
            return self._bind_message_session(message, fallback) if fallback is not None else direct
        if op in {"/status", "/restart", "/new", "/llm"}:
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
        if op not in SUPPORTED_COMMANDS - {"/help"}:
            self.reply(message.from_user_id, message.context_token, f"⚠️ 未知命令：{raw_op}\n\n{HELP_TEXT}")
            return
        if op == "/new":
            previous = self._resolve_command_session(message, op)
            session = self._create_new_session(message, previous)
            self._reply_for_session(message, session, self._new_session_reply_text(previous, session))
            return
        if op == "/restart":
            self._request_safe_restart(message)
            return
        session = self._resolve_command_session(message, op)
        if session is None:
            self.reply(message.from_user_id, message.context_token, self._missing_session_reply(op))
            return
        if op == "/status":
            self._reply_for_session(message, session, session.status_text())
        elif op == "/stop":
            self._reply_for_session(message, session, session.stop())
        elif op == "/llm":
            self._reply_for_session(message, session, self._handle_llm_command(session, args))
        else:
            self.reply(message.from_user_id, message.context_token, f"⚠️ 未知命令：{raw_op}\n\n{HELP_TEXT}")

    def handle_message(self, message) -> None:
        if not self.is_allowed(message.from_user_id):
            print(f"[WeChatApp] unauthorized user={message.from_user_id}")
            return
        text = (message.text or "").strip()
        command_text = self._expand_command_text(text)
        if command_text is not None:
            self.handle_command(message, command_text)
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
                    if self._shutdown_requested:
                        self.sessions.shutdown_for_restart()
                        print("[WeChatApp] restart requested, exiting current process")
                        return
            except KeyboardInterrupt:
                print("[WeChatApp] exiting")
                return
            except Exception as exc:
                print(f"[WeChatApp] loop error: {exc}")
                time.sleep(5)
