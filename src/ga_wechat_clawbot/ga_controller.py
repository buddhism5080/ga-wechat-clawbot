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

from .util import atomic_write_json, ensure_dir


@dataclass
class RunningTurn:
    process: subprocess.Popen[str]
    started_at: float
    request_dir: Path
    stdout_thread: threading.Thread
    wait_thread: threading.Thread


class GATurnController:
    def __init__(self, ga_root: str | os.PathLike[str], session_dir: str | os.PathLike[str], python_executable: str = sys.executable, default_llm_no: int = 0) -> None:
        self.ga_root = str(Path(ga_root).resolve())
        self.session_dir = ensure_dir(session_dir)
        self.python_executable = python_executable
        self.default_llm_no = int(default_llm_no)
        self.state_path = self.session_dir / "ga_state.json"
        self.requests_dir = ensure_dir(self.session_dir / "requests")
        self.running: RunningTurn | None = None
        self._lock = threading.Lock()
        self._src_root = Path(__file__).resolve().parents[2] / "src"
        self._ensure_default_state()

    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        py_path = str(self._src_root)
        env["PYTHONPATH"] = py_path if not env.get("PYTHONPATH") else py_path + os.pathsep + env["PYTHONPATH"]
        return env

    def _ensure_default_state(self) -> None:
        if self.state_path.exists():
            return
        atomic_write_json(self.state_path, {"llm_no": self.default_llm_no})

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
        proc = subprocess.run(cmd, capture_output=True, text=True, env=self._base_env(), cwd=str(self.session_dir))
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
                bufsize=1,
                cwd=str(self.session_dir),
                env=self._base_env(),
            )

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

            stdout_thread = threading.Thread(target=_stdout_reader, daemon=True, name=f"ga-turn-out-{request_id}")
            wait_thread = threading.Thread(target=_waiter, daemon=True, name=f"ga-turn-wait-{request_id}")
            stdout_thread.start()
            wait_thread.start()
            self.running = RunningTurn(process=process, started_at=time.time(), request_dir=request_dir, stdout_thread=stdout_thread, wait_thread=wait_thread)
            return self.running

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
