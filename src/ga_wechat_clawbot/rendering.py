from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .types import AttachmentRef

DEFAULT_ASK_USER_QUESTION = "请提供下一步信息："
DEFAULT_ASK_USER_INTRO = "🙋 需要你来决定下一步"
DEFAULT_ASK_USER_FOOTER = "请直接回复你的选择，或补充新的说明。"
FILE_REF_RE = re.compile(r"\[FILE:([^\]]+)\]")
BAD_FILE_REFS = {"filepath", "<filepath>", "path", "<path>", "file_path", "<file_path>", "..."}
TAG_PATS = [
    r"<thinking>.*?</thinking>",
    r"<summary>.*?</summary>",
    r"<tool_use>.*?</tool_use>",
    r"<file_content>.*?</file_content>",
]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
VOICE_EXTS = {".silk", ".wav", ".mp3", ".m4a", ".aac", ".opus", ".ogg"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


@dataclass
class RenderedReply:
    text_chunks: list[str]
    generated_files: list[str]
    ask_user: bool = False


def normalize_candidates(raw_candidates: Any) -> list[str]:
    if not isinstance(raw_candidates, (list, tuple)):
        return []
    return [str(candidate).strip() for candidate in raw_candidates if str(candidate or "").strip()]


def coerce_ask_user_data(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    question = str(data.get("question") or DEFAULT_ASK_USER_QUESTION).strip() or DEFAULT_ASK_USER_QUESTION
    return {"question": question, "candidates": normalize_candidates(data.get("candidates") or [])}


def extract_ask_user_event(exit_reason: Any) -> dict[str, Any] | None:
    payload = exit_reason
    if isinstance(exit_reason, dict) and "result" in exit_reason and "data" in exit_reason:
        if exit_reason.get("result") != "EXITED":
            return None
        payload = exit_reason.get("data")
    if not isinstance(payload, dict):
        return None
    if payload.get("status") != "INTERRUPT" or payload.get("intent") != "HUMAN_INTERVENTION":
        return None
    return coerce_ask_user_data(payload.get("data"))


def extract_ask_user_event_from_text(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except Exception:
            continue
        event = extract_ask_user_event(parsed)
        if event:
            return event
    return None


def format_ask_user_message(event: Any, intro: str = DEFAULT_ASK_USER_INTRO, footer: str = DEFAULT_ASK_USER_FOOTER) -> str:
    normalized = coerce_ask_user_data(event) or {"question": DEFAULT_ASK_USER_QUESTION, "candidates": []}
    lines = [intro, "", normalized["question"]]
    if normalized["candidates"]:
        lines.extend(["", "可选项："])
        for idx, candidate in enumerate(normalized["candidates"], start=1):
            lines.append(f"{idx}. {candidate}")
    if footer:
        lines.extend(["", footer])
    return "\n".join(lines).strip()


def _compact_text(value: Any, max_len: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _tool_path_label(path: Any) -> str:
    raw = str(path or "").strip()
    return os.path.basename(raw) or raw or "?"


def summarize_tool_args(name: str, args: dict[str, Any], max_len: int = 120) -> str:
    clean_args = {k: v for k, v in (args or {}).items() if not str(k).startswith("_")}
    if name == "ask_user":
        event = coerce_ask_user_data(clean_args)
        if not event:
            return ""
        summary = f"等待用户回复：{event['question']}"
        if event["candidates"]:
            preview = " / ".join(event["candidates"][:3])
            if len(event["candidates"]) > 3:
                preview += " / ..."
            summary += f"（选项：{preview}）"
        return summary[:max_len]
    if name == "search_files":
        pattern = _compact_text(clean_args.get("pattern") or "*", max_len=max_len)
        scope = _tool_path_label(clean_args.get("path") or ".")
        return _compact_text(f"搜索：{pattern}（范围：{scope}）", max_len=max_len)
    if name == "read_file":
        return _compact_text(f"读取文件：{_tool_path_label(clean_args.get('path'))}", max_len=max_len)
    if name == "write_file":
        return _compact_text(f"写入文件：{_tool_path_label(clean_args.get('path'))}", max_len=max_len)
    if name == "patch":
        return _compact_text(f"修改文件：{_tool_path_label(clean_args.get('path'))}", max_len=max_len)
    if name == "terminal":
        command = _compact_text(clean_args.get("command") or "(empty)", max_len=max_len)
        return f"执行命令：{command}"
    if name == "web_search":
        query = clean_args.get("query") or clean_args.get("content") or ""
        return _compact_text(f"网页搜索：{query}", max_len=max_len)
    try:
        rendered = json.dumps(clean_args, ensure_ascii=False)
    except TypeError:
        rendered = str(clean_args)
    return rendered[:max_len]


def extract_file_refs(text: str) -> list[str]:
    refs = []
    for raw in FILE_REF_RE.findall(text or ""):
        value = str(raw or "").strip()
        if value and value.lower() not in BAD_FILE_REFS:
            refs.append(value)
    seen = set()
    ordered = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


def strip_file_refs(text: str) -> str:
    return FILE_REF_RE.sub("", text or "").strip()


def route_path_kind(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VOICE_EXTS:
        return "voice"
    if ext in VIDEO_EXTS:
        return "video"
    return "file"


def _truncate_code_fences(text: str, max_lines: int = 60) -> str:
    def _repl(match: re.Match[str]) -> str:
        fence = match.group(1)
        info = match.group(2)
        body = match.group(3)
        lines = body.splitlines()
        if len(lines) <= max_lines:
            return match.group(0)
        kept = lines[:max_lines]
        kept.append(f"... ({len(lines) - max_lines} more lines)")
        joined = "\n".join(kept)
        return f"{fence}{info}\n{joined}\n{fence}"

    return re.sub(r"(```+)([^\n]*)\n([\s\S]*?)\n\1", _repl, text or "")


def clean_agent_reply(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"^\s*\*\*?LLM Running \(Turn \d+\) \.{3}\*\*?\s*$", "", cleaned, flags=re.M)
    cleaned = re.sub(r"^\s*🛠️\s*[A-Za-z_][A-Za-z0-9_]*\(.*$", "", cleaned, flags=re.M)
    for pat in TAG_PATS:
        cleaned = re.sub(pat, "", cleaned, flags=re.DOTALL)
    cleaned = strip_file_refs(cleaned)
    cleaned = cleaned.replace("</summary>", "")
    cleaned = _truncate_code_fences(cleaned)
    cleaned = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", lambda m: f"📷 {m.group(1).strip() or '图片'}", cleaned)
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.I)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() or "..."


def _chunk_lines_with_fences(lines: Sequence[str], limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0
    fence_open = False
    fence_marker = "```"

    def _flush() -> None:
        nonlocal current, cur_len
        if not current:
            return
        chunks.append("\n".join(current).strip() or "...")
        current = []
        cur_len = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        addition = len(line) + (1 if current else 0)
        if cur_len and cur_len + addition > limit:
            if fence_open:
                current.append(fence_marker)
            _flush()
            if fence_open:
                current.append(fence_marker)
                cur_len = len(fence_marker)
        current.append(line)
        cur_len += addition
        stripped = line.strip()
        if stripped.startswith("```"):
            fence_marker = stripped if set(stripped) == {'`'} else "```"
            fence_open = not fence_open
    if fence_open:
        current.append(fence_marker)
    _flush()
    return chunks or ["..."]


def split_markdown_chunks(text: str, limit: int = 1800) -> list[str]:
    body = str(text or "").strip() or "..."
    if len(body) <= limit:
        return [body]
    return _chunk_lines_with_fences(body.splitlines(), limit)


def _attachment_summary(paths: Iterable[str]) -> str:
    labels = {"image": "图片", "voice": "语音", "video": "视频", "file": "文件"}
    return "\n".join(f"- {labels.get(route_path_kind(path), '附件')}：`{os.path.basename(path)}`" for path in paths)


def render_progress_update(turn: int, summary: str, tool_calls: Sequence[dict[str, Any]] | None = None) -> str:
    lines = [f"### 处理中 · 第 {turn} 轮", f"- 摘要：{summary.strip() or '继续处理中'}"]
    previews = []
    for tc in tool_calls or []:
        name = tc.get("tool_name", "?")
        args = {k: v for k, v in (tc.get("args") or {}).items() if not str(k).startswith("_")}
        preview = summarize_tool_args(name, args, max_len=80)
        previews.append(f"`{name}`{('：' + preview) if preview and preview != '{}' else ''}")
    if previews:
        lines.append("- 工具：" + "，".join(previews[:3]))
    return "\n".join(lines).strip()


def render_abort_message(reason: str = "", timed_out: bool = False) -> str:
    title = "⚠️ 任务超时，已自动中止" if timed_out else "⏹️ 任务已中止"
    lines = [title]
    clean_reason = _compact_text(reason, max_len=180)
    if clean_reason:
        lines.extend(["", f"- 原因：{clean_reason}"])
    lines.extend(["", "你可以直接继续补充消息，或发送 `/new` 清空当前会话。"])
    return "\n".join(lines).strip()


def _traceback_excerpt(traceback_text: str, max_lines: int = 8) -> str:
    lines = [line.rstrip() for line in str(traceback_text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines]
    kept.append(f"... ({len(lines) - max_lines} more lines)")
    return "\n".join(kept)


def render_error_message(message: str, traceback_text: str = "") -> str:
    lines = ["❌ 运行失败", "", f"- 错误：{_compact_text(message or 'unknown error', max_len=240)}"]
    excerpt = _traceback_excerpt(traceback_text)
    if excerpt:
        lines.extend(["", "```text", excerpt, "```"])
    lines.extend(["", "可发送 `/status` 查看当前状态，或发送 `/new` 清空当前会话后重试。"])
    return "\n".join(lines).strip()


def render_final_reply(raw_text: str, generated_paths: Sequence[str] | None = None, limit: int = 1800) -> RenderedReply:
    ask_user_event = extract_ask_user_event_from_text(raw_text)
    files = list(generated_paths or extract_file_refs(raw_text))
    if ask_user_event:
        return RenderedReply(split_markdown_chunks(format_ask_user_message(ask_user_event), limit=limit), files, ask_user=True)
    body = clean_agent_reply(raw_text)
    if files:
        body = f"{body}\n\n---\n**附件**\n{_attachment_summary(files)}" if body else f"**附件**\n{_attachment_summary(files)}"
    return RenderedReply(split_markdown_chunks(body, limit=limit), files, ask_user=False)


def build_attachment_prompt(attachments: Sequence[AttachmentRef]) -> str:
    if not attachments:
        return ""
    lines = ["### 微信附件"]
    for attachment in attachments:
        lines.append(f"- type={attachment.kind} name={attachment.name} source={attachment.path}")
        if attachment.transcript:
            lines.append(f"  transcript={attachment.transcript}")
    return "\n".join(lines)
