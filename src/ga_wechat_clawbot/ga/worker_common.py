from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from ..rendering import extract_ask_user_event, extract_file_refs
from ..util import atomic_write_json, ensure_dir, read_json


@dataclass
class GARuntime:
    agentmain: Any
    ga: Any
    agent_loop: Any
    llmcore: Any


def bootstrap_ga(ga_root: str | os.PathLike[str]) -> GARuntime:
    ga_root = str(Path(ga_root).resolve())
    if ga_root not in sys.path:
        sys.path.insert(0, ga_root)
    os.chdir(ga_root)
    import agent_loop  # type: ignore
    import agentmain  # type: ignore
    import ga  # type: ignore
    import llmcore  # type: ignore

    return GARuntime(agentmain=agentmain, ga=ga, agent_loop=agent_loop, llmcore=llmcore)


def load_state(state_path: str | os.PathLike[str]) -> dict[str, Any]:
    return read_json(state_path, default={}) or {}


def save_state(state_path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    atomic_write_json(state_path, payload)


def probe_llms(ga_root: str | os.PathLike[str], state_path: str | os.PathLike[str]) -> dict[str, Any]:
    runtime = bootstrap_ga(ga_root)
    state = load_state(state_path)
    agent = runtime.agentmain.GeneraticAgent()
    desired = int(state.get("llm_no", agent.llm_no) or 0)
    if desired != agent.llm_no:
        agent.next_llm(desired)
    llms = [
        {"idx": idx, "name": name, "current": current}
        for idx, name, current in agent.list_llms()
    ]
    return {"llm_no": agent.llm_no, "llms": llms}


def switch_llm(ga_root: str | os.PathLike[str], state_path: str | os.PathLike[str], llm_no: int) -> dict[str, Any]:
    runtime = bootstrap_ga(ga_root)
    state = load_state(state_path)
    agent = runtime.agentmain.GeneraticAgent()
    target = int(llm_no)
    agent.next_llm(target)
    state["llm_no"] = agent.llm_no
    save_state(state_path, state)
    return {"llm_no": agent.llm_no, "name": agent.get_llm_name()}


def reset_state(state_path: str | os.PathLike[str]) -> None:
    path = Path(state_path)
    if path.exists():
        path.unlink()


def _mime_for_path(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _image_block_from_path(path: str, max_bytes: int = 5 * 1024 * 1024) -> dict[str, Any] | None:
    try:
        if not os.path.isfile(path):
            return None
        mime = _mime_for_path(path)
        if not mime.startswith("image/"):
            return None
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as handle:
            data = base64.b64encode(handle.read()).decode()
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
    except Exception:
        return None


def apply_saved_state(agent: Any, state: dict[str, Any]) -> dict[str, Any]:
    desired = int(state.get("llm_no", agent.llm_no) or 0)
    if desired != agent.llm_no:
        agent.next_llm(desired)
    backend_history = state.get("backend_history")
    if isinstance(backend_history, list):
        agent.llmclient.backend.history = backend_history
    history_info = state.get("history_info")
    if isinstance(history_info, list):
        agent.history = list(history_info)
    return state.get("working") if isinstance(state.get("working"), dict) else {}


def run_turn(
    ga_root: str | os.PathLike[str],
    session_dir: str | os.PathLike[str],
    state_path: str | os.PathLike[str],
    prompt: str,
    images: Sequence[str],
    emit: Callable[[dict[str, Any]], None],
    stop_requested: Callable[[], bool],
) -> int:
    runtime = bootstrap_ga(ga_root)
    session_dir = Path(session_dir)
    work_dir = ensure_dir(session_dir / "work")
    log_dir = ensure_dir(session_dir / "logs")
    state = load_state(state_path)
    agent = runtime.agentmain.GeneraticAgent()
    agent.verbose = False
    agent.inc_out = False
    agent.peer_hint = False
    agent.task_dir = str(ensure_dir(session_dir / "ipc"))
    agent.log_path = str(log_dir / "model_responses.txt")
    previous_working = apply_saved_state(agent, state)

    raw_query = prompt
    rquery = runtime.ga.smart_format(raw_query.replace("\n", " "), max_str_len=200)
    agent.history.append(f"[USER]: {rquery}")
    sys_prompt = runtime.agentmain.get_system_prompt() + getattr(agent.llmclient.backend, "extra_sys_prompt", "")
    handler = runtime.ga.GenericAgentHandler(agent, agent.history, str(work_dir))
    if previous_working.get("key_info"):
        key_info = previous_working.get("key_info", "")
        key_info = runtime.ga.re.sub(r"\n\[SYSTEM\] 此为.*?工作记忆[。\n]*", "", key_info)
        handler.working["key_info"] = key_info
        passed_sessions = int(previous_working.get("passed_sessions", 0) or 0) + 1
        handler.working["passed_sessions"] = passed_sessions
        handler.working["key_info"] += f"\n[SYSTEM] 此为 {passed_sessions} 个对话前设置的key_info，若已在新任务，先更新或清除工作记忆。\n"
    agent.handler = handler
    if not hasattr(agent, "_turn_end_hooks"):
        agent._turn_end_hooks = {}

    result: dict[str, Any] = {"ask_user": None, "raw_text": "", "summary": ""}
    hook_key = f"standalone_wechat_{os.getpid()}_{id(agent)}"

    def _hook(ctx: dict[str, Any]) -> None:
        exit_reason = ctx.get("exit_reason")
        if exit_reason:
            result["ask_user"] = extract_ask_user_event(exit_reason)
            response = ctx.get("response")
            result["raw_text"] = getattr(response, "content", "") if response is not None else ""
            result["summary"] = ctx.get("summary", "")
            return
        summary = str(ctx.get("summary", "") or "").strip()
        if not summary:
            return
        tool_calls = list(ctx.get("tool_calls") or [])
        emit({"event": "progress", "turn": int(ctx.get("turn", 0) or 0), "summary": summary, "tool_calls": tool_calls})

    agent._turn_end_hooks[hook_key] = _hook
    initial_user_content = None
    try:
        native_cls = runtime.llmcore.NativeToolClient
    except Exception:
        native_cls = None
    if native_cls is not None and isinstance(agent.llmclient, native_cls):
        blocks = [{"type": "text", "text": prompt}]
        for path in images:
            block = _image_block_from_path(path)
            if block:
                blocks.append(block)
        if len(blocks) > 1:
            initial_user_content = blocks

    generator = runtime.agent_loop.agent_runner_loop(
        agent.llmclient,
        sys_prompt,
        raw_query,
        handler,
        runtime.agentmain.TOOLS_SCHEMA,
        max_turns=70,
        verbose=False,
        initial_user_content=initial_user_content,
    )
    full_resp = ""
    aborted = False
    try:
        for chunk in generator:
            if stop_requested():
                agent.abort()
                aborted = True
            if agent.stop_sig:
                aborted = True
                break
            full_resp += chunk
    except Exception as exc:
        emit({"event": "error", "message": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
        return 1
    finally:
        agent._turn_end_hooks.pop(hook_key, None)

    if aborted:
        emit({"event": "aborted", "message": "task aborted"})
        return 130

    raw_text = result["raw_text"] or full_resp
    payload = {
        "llm_no": agent.llm_no,
        "history_info": list(agent.history or []),
        "backend_history": getattr(agent.llmclient.backend, "history", []),
        "working": {
            "key_info": handler.working.get("key_info", ""),
            "passed_sessions": int(handler.working.get("passed_sessions", 0) or 0),
        },
    }
    save_state(state_path, payload)
    if result["ask_user"]:
        emit({"event": "ask_user", "payload": result["ask_user"]})
    emit({"event": "done", "raw_text": raw_text, "generated_files": extract_file_refs(raw_text), "llm_no": agent.llm_no})
    return 0
