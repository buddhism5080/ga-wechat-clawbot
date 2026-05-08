from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .util import atomic_write_json, atomic_write_text, ensure_dir, hidden_windows_subprocess_kwargs, remove_tree


@dataclass
class RunningTurn:
    process: subprocess.Popen[str]
    started_at: float
    request_dir: Path
    stdout_thread: threading.Thread
    wait_thread: threading.Thread


class GATurnController:
    _root_registry_lock = threading.Lock()
    _workspace_registry_lock = threading.Lock()
    _active_by_root: dict[str, "GATurnController"] = {}
    _workspace_locks: dict[str, threading.Lock] = {}

    def __init__(self, ga_root: str | os.PathLike[str], session_dir: str | os.PathLike[str], python_executable: str = sys.executable, default_llm_no: int = 0) -> None:
        self.ga_root = str(Path(ga_root).resolve())
        self.session_dir = ensure_dir(session_dir)
        self.python_executable = python_executable
        self.default_llm_no = int(default_llm_no)
        self.work_dir = ensure_dir(Path(self.ga_root) / "temp")
        self.state_path = self.session_dir / "ga_state.json"
        self.requests_dir = ensure_dir(self.session_dir / "requests")
        self.ipc_dir = ensure_dir(self.session_dir / "ipc")
        self.running: RunningTurn | None = None
        self._lock = threading.Lock()
        self._src_root = Path(__file__).resolve().parents[2] / "src"
        self._ensure_default_state()

    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        py_path = str(self._src_root)
        env["PYTHONPATH"] = py_path if not env.get("PYTHONPATH") else py_path + os.pathsep + env["PYTHONPATH"]
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    def _ensure_default_state(self) -> None:
        if self.state_path.exists():
            return
        atomic_write_json(self.state_path, {"llm_no": self.default_llm_no})

    def _claim_active_root(self) -> None:
        with self._root_registry_lock:
            owner = self._active_by_root.get(self.ga_root)
            if owner is not None and owner is not self:
                running = getattr(owner, "running", None)
                if running is not None and running.process.poll() is None:
                    raise RuntimeError("another turn already running for this ga_root")
            self._active_by_root[self.ga_root] = self

    def _workspace_lock(self) -> threading.Lock:
        with self._workspace_registry_lock:
            lock = self._workspace_locks.get(self.ga_root)
            if lock is None:
                lock = threading.Lock()
                self._workspace_locks[self.ga_root] = lock
            return lock

    def _release_active_root(self, process: subprocess.Popen[str] | None = None) -> None:
        with self._root_registry_lock:
            owner = self._active_by_root.get(self.ga_root)
            if owner is self:
                current = getattr(self, "running", None)
                if process is None or current is None or current.process is process or process.poll() is not None:
                    self._active_by_root.pop(self.ga_root, None)

    def _run_probe(self, *args: str) -> dict:
        cmd = [
            self.python_executable,
            "-m",
            "ga_wechat_clawbot.ga.probe_worker",
            "--ga-root",
            self.ga_root,
            "--state-path",
            str(self.state_path),
            *args,
        ]
        self._ensure_default_state()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._base_env(),
            cwd=self.ga_root,
            **hidden_windows_subprocess_kwargs(),
        )
        if proc.returncode != 0:
            log_path = self.state_path.parent / "probe_worker.log"
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"probe failed: {proc.returncode}; see {log_path}")
        return json.loads(proc.stdout or "{}")

    def list_llms(self) -> dict:
        return self._run_probe("list-llms")

    def switch_llm(self, llm_no: int) -> dict:
        return self._run_probe("switch-llm", str(llm_no))

    def reset_state(self) -> None:
        self._run_probe("reset-state")

    def reset_work_dir(self) -> None:
        workspace_lock = self._workspace_lock()
        acquired = workspace_lock.acquire(timeout=2)
        if not acquired:
            return
        try:
            remove_tree(self.work_dir)
            ensure_dir(self.work_dir)
        finally:
            workspace_lock.release()

    def start_turn(
        self,
        prompt: str,
        images: Sequence[str],
        on_event: Callable[[dict], None],
        on_exit: Callable[[int], None],
    ) -> RunningTurn:
        with self._lock:
            if self.running is not None and self.running.process.poll() is None:
                raise RuntimeError("turn already running")
            workspace_lock = self._workspace_lock()
            if not workspace_lock.acquire(blocking=False):
                raise RuntimeError("another turn already running for this ga_root")
            self._claim_active_root()
            try:
                request_id = uuid.uuid4().hex[:12]
                request_dir = ensure_dir(self.requests_dir / request_id)
                prompt_file = request_dir / "prompt.txt"
                images_file = request_dir / "images.json"
                prompt_file.write_text(prompt, "utf-8")
                images_file.write_text(json.dumps(list(images), ensure_ascii=False), "utf-8")
                cmd = [
                    self.python_executable,
                    "-m",
                    "ga_wechat_clawbot.ga.turn_worker",
                    "--ga-root",
                    self.ga_root,
                    "--session-dir",
                    str(self.session_dir),
                    "--work-dir",
                    str(self.work_dir),
                    "--state-path",
                    str(self.state_path),
                    "--prompt-file",
                    str(prompt_file),
                    "--images-file",
                    str(images_file),
                ]
                self._ensure_default_state()
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=self.ga_root,
                    env=self._base_env(),
                    **hidden_windows_subprocess_kwargs(),
                )
            except Exception:
                self._release_active_root()
                workspace_lock.release()
                raise

            def _stdout_reader() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        on_event({"event": "error", "message": f"bad worker json: {line[:200]}"})
                        continue
                    on_event(payload)

            def _waiter() -> None:
                rc = process.wait()
                on_exit(rc)
                with self._lock:
                    if self.running and self.running.process is process:
                        self.running = None
                self._release_active_root(process)
                workspace_lock.release()

            stdout_thread = threading.Thread(target=_stdout_reader, daemon=True, name=f"ga-turn-out-{request_id}")
            wait_thread = threading.Thread(target=_waiter, daemon=True, name=f"ga-turn-wait-{request_id}")
            stdout_thread.start()
            wait_thread.start()
            self.running = RunningTurn(process=process, started_at=time.time(), request_dir=request_dir, stdout_thread=stdout_thread, wait_thread=wait_thread)
            return self.running

    def intervene(self, prompt: str) -> bool:
        text = str(prompt or "").strip()
        if not text:
            return False
        with self._lock:
            running = self.running
        if running is None or running.process.poll() is not None:
            return False
        atomic_write_text(self.ipc_dir / "_intervene", text + "\n")
        return True

    def abort(self, grace_sec: int = 5) -> None:
        with self._lock:
            running = self.running
        if running is None or running.process.poll() is not None:
            return
        running.process.terminate()
        try:
            running.process.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            running.process.kill()
