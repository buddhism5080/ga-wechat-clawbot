from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from .worker_common import run_turn

STOP = False


def _handle_signal(signum, frame):  # pragma: no cover - signal behavior
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ga-root", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--images-file", required=True)
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    worker_log = session_dir / "logs" / "turn_worker.log"
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    logf = open(worker_log, "a", encoding="utf-8", buffering=1)
    orig_stdout = sys.__stdout__
    sys.stdout = sys.stderr = logf

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    prompt = Path(args.prompt_file).read_text("utf-8")
    images = json.loads(Path(args.images_file).read_text("utf-8"))

    def emit(payload: dict) -> None:
        orig_stdout.write(json.dumps(payload) + "\n")
        orig_stdout.flush()

    return run_turn(
        ga_root=args.ga_root,
        session_dir=args.session_dir,
        state_path=args.state_path,
        prompt=prompt,
        images=images,
        emit=emit,
        stop_requested=lambda: STOP,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
