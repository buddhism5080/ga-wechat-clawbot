from __future__ import annotations

import argparse
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .util import hidden_windows_subprocess_kwargs


def _log_line(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def _parent_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_parent_exit(parent_pid: int, wait_timeout_sec: float, log_path: Path) -> None:
    deadline = time.time() + max(0.0, float(wait_timeout_sec))
    while time.time() < deadline:
        if not _parent_alive(parent_pid):
            return
        time.sleep(0.25)
    _log_line(log_path, f"parent pid {parent_pid} still alive after timeout; continuing with restart command")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ga-wechat-clawbot-restart-helper")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--wait-timeout-sec", type=float, default=30.0)
    args = parser.parse_args(argv)

    log_path = Path(args.log_file).expanduser().resolve()
    workdir = Path(args.workdir).expanduser().resolve()
    _log_line(log_path, f"restart helper armed for parent pid={args.parent_pid}; command={args.command}")
    _wait_for_parent_exit(args.parent_pid, args.wait_timeout_sec, log_path)
    try:
        with log_path.open("a", encoding="utf-8") as log_handle:
            child = subprocess.Popen(
                args.command,
                shell=True,
                cwd=str(workdir),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                **hidden_windows_subprocess_kwargs(),
            )
        _log_line(log_path, f"restart command launched with pid={child.pid}")
        return 0
    except Exception as exc:
        _log_line(log_path, f"restart command failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
