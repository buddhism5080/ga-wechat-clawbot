from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Sequence

from .config import AppConfig
from .ga_controller import GATurnController
from .rendering import (
    build_attachment_prompt,
    extract_file_refs,
    format_ask_user_message,
    render_abort_message,
    render_error_message,
    render_final_reply,
    render_progress_update,
    split_markdown_chunks,
)
from .types import AttachmentRef
from .util import ensure_dir, is_probably_absolute_path, portable_basename, remove_tree, safe_slug
from .wechat_client import WxClawClient

FILE_HINT = "If you need to show files to user, use [FILE:filepath] in your response."


class SessionActor:
    def __init__(self, session_key: str, config: AppConfig, client: WxClawClient) -> None:
        self.session_key = session_key
        self.config = config
        self.client = client
        self.session_dir = ensure_dir(config.storage.root / "sessions" / safe_slug(session_key))
        self.controller = GATurnController(
            config.ga.root,
            self.session_dir,
            config.ga.python,
            default_llm_no=config.ga.default_llm_no,
        )
        self.last_active = time.time()
        self._typing_stop = threading.Event()
        self._typing_thread: threading.Thread | None = None
        self._timeout_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._saw_ask_user = False
        self._saw_error = False
        self._saw_abort = False
        self._timed_out = False
        self._abort_requested_reason = ""
        self._abort_notice_pending = False
        self._progress_turn: int | None = None
        self._progress_at = 0.0
        self._last_user_visible_update_at = 0.0
        self._latest_progress_summary = ""
        self._current_user_id = ""
        self._current_context_token = ""
        self._current_inbound_paths: set[str] = set()
        self._intervention_lock = threading.Lock()
        self._pending_interventions: list[str] = []

    @property
    def is_running(self) -> bool:
        return self.controller.running is not None and self.controller.running.process.poll() is None

    def _reply(self, text: str) -> None:
        self._last_user_visible_update_at = time.time()
        for chunk in split_markdown_chunks(text):
            self.client.send_text(self._current_user_id, chunk, context_token=self._current_context_token)

    def _same_running(self, expected_running) -> bool:
        current = self.controller.running
        return current is expected_running and current is not None and current.process.poll() is None

    def _processing_ping_interval_sec(self) -> int:
        return max(1, int(self.config.wechat.heartbeat_interval_sec))

    def _maybe_send_processing_ping(self, expected_running, now: float | None = None) -> bool:
        if not self._same_running(expected_running):
            return False
        now = time.time() if now is None else now
        if now - self._last_user_visible_update_at < self._processing_ping_interval_sec():
            return False
        message = "⏳ 还在处理中，请稍等..."
        if self._latest_progress_summary:
            message += f"\n\n- 最近进展：{self._latest_progress_summary}"
        self._reply(message)
        self._last_user_visible_update_at = now
        return True

    def _start_processing_ping(self, expected_running) -> None:
        interval = self._processing_ping_interval_sec()
        if interval <= 0:
            return

        def _watch() -> None:
            while self._same_running(expected_running):
                self._maybe_send_processing_ping(expected_running)
                time.sleep(min(3, max(1, interval // 4)))

        self._heartbeat_thread = threading.Thread(target=_watch, daemon=True, name=f"wechat-heartbeat-{safe_slug(self.session_key)}")
        self._heartbeat_thread.start()

    def _maybe_timeout_run(self, expected_running, started_at: float, now: float | None = None) -> bool:
        if not self._same_running(expected_running):
            return False
        now = time.time() if now is None else now
        timeout_sec = self.config.ga.turn_timeout_sec
        if now < started_at + timeout_sec:
            return False
        self._timed_out = True
        self._saw_error = True
        self.controller.abort()
        self._reply(render_abort_message(f"超过 {timeout_sec} 秒仍未完成。", timed_out=True))
        return True

    def _start_typing(self) -> None:
        self._typing_stop = threading.Event()

        def _loop() -> None:
            try:
                ticket = self.client.get_typing_ticket(self._current_user_id, self._current_context_token)
            except Exception:
                ticket = ""
            while not self._typing_stop.is_set():
                if ticket:
                    try:
                        self.client.send_typing(self._current_user_id, ticket)
                    except Exception:
                        pass
                self._typing_stop.wait(2.0)
            if ticket:
                try:
                    self.client.send_typing(self._current_user_id, ticket, cancel=True)
                except Exception:
                    pass

        self._typing_thread = threading.Thread(target=_loop, daemon=True, name=f"wechat-typing-{safe_slug(self.session_key)}")
        self._typing_thread.start()

    def _stop_typing(self) -> None:
        self._typing_stop.set()

    def _start_timeout_watchdog(self, running) -> None:
        def _watch() -> None:
            deadline = running.started_at + self.config.ga.turn_timeout_sec
            while time.time() < deadline:
                if not self._same_running(running):
                    return
                time.sleep(1)
            self._maybe_timeout_run(running, running.started_at)

        self._timeout_thread = threading.Thread(target=_watch, daemon=True, name=f"wechat-timeout-{safe_slug(self.session_key)}")
        self._timeout_thread.start()

    def build_prompt(self, text: str, attachments: Sequence[AttachmentRef]) -> tuple[str, list[str]]:
        sections = [FILE_HINT, "", "### 用户消息", text.strip() or "用户发送了附件，请结合附件内容处理。"]
        attachment_section = build_attachment_prompt(list(attachments))
        if attachment_section:
            sections.extend(["", attachment_section, "", "如需查看附件内容，请读取上面 source 指向的本地文件。"])
        prompt = "\n".join(section for section in sections if section is not None).strip()
        image_paths = [attachment.path for attachment in attachments if attachment.kind == "image" and os.path.exists(attachment.path)]
        return prompt, image_paths

    def build_intervention_prompt(self, text: str, attachments: Sequence[AttachmentRef]) -> str:
        sections = [
            "### 用户补充消息",
            text.strip() or "用户补充发送了附件，请结合附件内容更新当前任务。",
        ]
        attachment_section = build_attachment_prompt(list(attachments))
        if attachment_section:
            sections.extend(["", attachment_section, "", "如需查看新增附件内容，请读取上面 source 指向的本地文件。"])
        return "\n".join(section for section in sections if section is not None).strip()

    def _clear_pending_interventions(self) -> None:
        with self._intervention_lock:
            self._pending_interventions.clear()

    def _request_abort(self, reason: str, notify: bool) -> None:
        self._abort_requested_reason = str(reason or "").strip() or "用户请求停止"
        self._abort_notice_pending = bool(notify)

        def _run() -> None:
            try:
                self.controller.abort()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True, name=f"wechat-abort-{safe_slug(self.session_key)}").start()

    def _steer_running_turn(self, user_id: str, context_token: str, text: str, attachments: Sequence[AttachmentRef]) -> bool:
        self._current_user_id = user_id
        if context_token:
            self._current_context_token = context_token
        self._current_inbound_paths.update(os.path.realpath(att.path) for att in attachments if att.path)
        message = self.build_intervention_prompt(text, attachments)
        with self._intervention_lock:
            self._pending_interventions.append(message)
            blocks = [
                "用户在当前任务执行期间追加了新的消息。不要开启新会话；继续当前任务，并将下面内容视为最新补充/修正。若与之前计划冲突，以最新补充为准。",
                "",
            ]
            for idx, item in enumerate(self._pending_interventions, start=1):
                blocks.extend([f"## 补充 {idx}", item, ""])
            payload = "\n".join(blocks).strip()
        if not self.controller.intervene(payload):
            self._clear_pending_interventions()
            return False
        self.client.send_text(user_id, "↪️ 已插入当前任务，会在下一轮生效。", context_token=context_token)
        self._last_user_visible_update_at = time.time()
        return True

    def submit_turn(self, user_id: str, context_token: str, text: str, attachments: Sequence[AttachmentRef]) -> None:
        self.last_active = time.time()
        if self.is_running and self._steer_running_turn(user_id, context_token, text, attachments):
            return
        if self.is_running:
            self.client.send_text(user_id, "⚠️ 当前会话还在运行中，补充消息插入失败，请稍后重试或发送 /stop。", context_token=context_token)
            self._last_user_visible_update_at = time.time()
            return
        self._clear_pending_interventions()
        self._current_user_id = user_id
        if context_token:
            self._current_context_token = context_token
        self._current_inbound_paths = {os.path.realpath(att.path) for att in attachments if att.path}
        self._saw_ask_user = False
        self._saw_error = False
        self._saw_abort = False
        self._timed_out = False
        self._abort_requested_reason = ""
        self._abort_notice_pending = False
        self._progress_turn = None
        self._progress_at = 0.0
        self._last_user_visible_update_at = time.time()
        self._latest_progress_summary = "任务已启动，正在等待模型响应"
        prompt, image_paths = self.build_prompt(text, attachments)
        self._start_typing()
        try:
            running = self.controller.start_turn(prompt, image_paths, self._on_event, self._on_exit)
        except Exception as exc:
            self._stop_typing()
            self._saw_error = True
            self._reply(f"❌ 启动 GA turn 失败：{exc}")
            return
        self._start_timeout_watchdog(running)
        self._start_processing_ping(running)

    def _send_generated_files(self, files: Sequence[str]) -> None:
        sent = set()
        candidates = []
        for raw in files:
            path = Path(raw)
            basename = portable_basename(raw) or path.name
            if path.is_absolute():
                candidates.append(path)
            elif is_probably_absolute_path(raw):
                candidates.extend([
                    path,
                    self.session_dir / "work" / basename,
                ])
            else:
                candidates.extend([
                    self.session_dir / "work" / raw,
                    self.session_dir / "work" / basename,
                    Path.cwd() / raw,
                ])
            for candidate in candidates[-3:]:
                if not candidate.exists() or not candidate.is_file():
                    continue
                resolved = os.path.realpath(candidate)
                if resolved in self._current_inbound_paths or resolved in sent:
                    continue
                self.client.send_path(self._current_user_id, resolved, context_token=self._current_context_token)
                sent.add(resolved)
                break

    def _on_event(self, payload: dict) -> None:
        self.last_active = time.time()
        self._clear_pending_interventions()
        event = payload.get("event")
        if event == "progress":
            turn = int(payload.get("turn", 0) or 0)
            now = time.time()
            throttle_sec = int(self.config.wechat.progress_interval_sec)
            if throttle_sec > 0 and self._progress_turn is not None and turn == self._progress_turn and now - self._progress_at < throttle_sec:
                return
            self._progress_turn = turn
            self._progress_at = now
            self._latest_progress_summary = str(payload.get("summary", "") or "").strip() or self._latest_progress_summary
            self._reply(render_progress_update(turn, str(payload.get("summary", "")), payload.get("tool_calls") or []))
            return
        if event == "ask_user":
            self._saw_ask_user = True
            self._reply(format_ask_user_message(payload.get("payload")))
            return
        if event == "done":
            if self._saw_ask_user:
                return
            rendered = render_final_reply(str(payload.get("raw_text", "")), generated_paths=payload.get("generated_files") or [])
            for chunk in rendered.text_chunks:
                self.client.send_text(self._current_user_id, chunk, context_token=self._current_context_token)
            if rendered.text_chunks:
                self._last_user_visible_update_at = time.time()
            self._send_generated_files(rendered.generated_files)
            return
        if event == "aborted":
            if self._timed_out:
                return
            self._saw_abort = True
            self._abort_notice_pending = False
            self._abort_requested_reason = ""
            self._reply(render_abort_message(str(payload.get("message", "") or "用户请求停止")))
            return
        if event == "error":
            if self._abort_requested_reason and not self._timed_out:
                self._saw_abort = True
                self._saw_error = True
                if self._abort_notice_pending:
                    self._reply(render_abort_message(self._abort_requested_reason))
                    self._abort_notice_pending = False
                self._abort_requested_reason = ""
                return
            self._saw_error = True
            self._reply(render_error_message(str(payload.get("message", "unknown error")), str(payload.get("traceback", ""))))

    def _on_exit(self, returncode: int) -> None:
        self._stop_typing()
        self._clear_pending_interventions()
        if self._timed_out:
            return
        if self._abort_requested_reason:
            if self._abort_notice_pending and not self._saw_abort and not self._saw_error:
                self._reply(render_abort_message(self._abort_requested_reason))
            self._abort_notice_pending = False
            self._abort_requested_reason = ""
            return
        if returncode not in (0, 130) and not self._saw_error:
            self._reply(f"❌ Worker 退出异常（code={returncode}）")

    def _llm_snapshot(self) -> tuple[dict, dict | None, str, str | int]:
        info = self.controller.list_llms()
        current = next((entry for entry in info.get("llms", []) if entry.get("current")), None)
        current_name = current.get("name", "未配置") if current else "未配置"
        current_idx = current.get("idx", info.get("llm_no", 0)) if current else info.get("llm_no", 0)
        return info, current, current_name, current_idx

    def status_text(self) -> str:
        try:
            _, _, current_name, current_idx = self._llm_snapshot()
        except Exception as exc:
            current_name = f"读取失败: {exc}"
            current_idx = "?"
        status = "🔴 运行中" if self.is_running else "🟢 空闲"
        hint = "\n提示: 可发送 `/stop` 中止当前任务。" if self.is_running else "\n提示: 可发送 `/new` 新建一个干净会话。"
        return f"会话: `{self.session_key}`\n状态: {status}\nLLM: [{current_idx}] {current_name}{hint}"

    def current_llm_text(self) -> str:
        try:
            _, _, current_name, current_idx = self._llm_snapshot()
        except Exception as exc:
            return f"❌ 读取当前模型失败：{exc}"
        return f"当前模型\n- 编号: [{current_idx}]\n- 名称: {current_name}"

    def list_llms_text(self) -> str:
        info, _, _, _ = self._llm_snapshot()
        lines = ["🤖 可用模型"]
        for entry in info.get("llms", []):
            prefix = "→" if entry.get("current") else "-"
            lines.append(f"{prefix} [{entry['idx']}] {entry['name']}")
        lines.extend(["", "切换用法：`/llm 2` 或 `/llm set 2`"])
        return "\n".join(lines)

    def switch_llm(self, llm_no: int) -> str:
        if self.is_running:
            return "⚠️ 当前任务仍在运行，请先停止后再切换模型。"
        info = self.controller.switch_llm(llm_no)
        return f"✅ 已切换到 [{info['llm_no']}] {info['name']}"

    def stop(self) -> str:
        self._clear_pending_interventions()
        if self.is_running:
            self._request_abort("用户请求停止", notify=True)
            return "⏹️ 已发送停止信号，稍后会收到中止通知。"
        return "ℹ️ 当前没有正在运行的任务。"

    def reset(self) -> str:
        self._clear_pending_interventions()
        was_running = self.is_running
        if was_running:
            self._abort_requested_reason = "当前会话已重置"
            self._abort_notice_pending = False
            self.controller.abort()
        self.controller.reset_state()
        remove_tree(self.session_dir / "work")
        ensure_dir(self.session_dir / "work")
        return "🧹 已停止当前任务并清空当前会话上下文。" if was_running else "🧹 已清空当前会话上下文。"

    def shutdown_for_restart(self, reason: str = "服务正在重启") -> None:
        self._clear_pending_interventions()
        self._stop_typing()
        if not self.is_running:
            return
        self._abort_requested_reason = str(reason or "").strip() or "服务正在重启"
        self._abort_notice_pending = False
        try:
            self.controller.abort()
        except Exception:
            pass


class SessionRegistry:
    def __init__(self, config: AppConfig, client: WxClawClient) -> None:
        self.config = config
        self.client = client
        self.sessions: dict[str, SessionActor] = {}

    def _touch(self, session: SessionActor) -> SessionActor:
        session.last_active = time.time()
        return session

    def _unique_sessions(self) -> list[SessionActor]:
        unique: list[SessionActor] = []
        seen: set[int] = set()
        for session in self.sessions.values():
            sid = id(session)
            if sid in seen:
                continue
            seen.add(sid)
            unique.append(session)
        return unique

    def find(self, session_key: str) -> SessionActor | None:
        session = self.sessions.get(session_key)
        return self._touch(session) if session is not None else None

    def bind(self, session_key: str, session: SessionActor) -> SessionActor:
        if session_key:
            self.sessions[session_key] = session
        return self._touch(session)

    def create_fresh(
        self,
        session_key_hint: str,
        previous: SessionActor | None = None,
        bind_keys: Sequence[str] = (),
    ) -> SessionActor:
        session_key = f"{safe_slug(session_key_hint or 'session', max_len=48)}-{time.time_ns():x}"
        session = SessionActor(session_key, self.config, self.client)
        if previous is not None:
            session._current_user_id = previous._current_user_id
            session._current_context_token = previous._current_context_token
            for key, existing in list(self.sessions.items()):
                if existing is previous:
                    self.sessions[key] = session
        self.sessions[session_key] = session
        for key in bind_keys:
            if key:
                self.sessions[key] = session
        return self._touch(session)

    def find_latest_for_user(self, user_id: str, running_only: bool = False) -> SessionActor | None:
        candidates = [
            session for session in self._unique_sessions()
            if session._current_user_id == user_id and (session.is_running if running_only else True)
        ]
        if not candidates:
            return None
        return self._touch(max(candidates, key=lambda session: session.last_active))

    def get(self, session_key: str) -> SessionActor:
        session = self.sessions.get(session_key)
        if session is None:
            session = SessionActor(session_key, self.config, self.client)
            self.sessions[session_key] = session
        return self._touch(session)

    def evict_idle(self) -> None:
        now = time.time()
        stale = [
            key for key, session in self.sessions.items()
            if not session.is_running and now - session.last_active > self.config.ga.session_idle_ttl_sec
        ]
        for key in stale:
            self.sessions.pop(key, None)

    def shutdown_for_restart(self, reason: str = "服务正在重启") -> None:
        for session in self._unique_sessions():
            session.shutdown_for_restart(reason)
