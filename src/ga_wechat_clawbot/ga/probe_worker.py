from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .worker_common import probe_llms, reset_state, switch_llm


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ga-root", required=True)
    parser.add_argument("--state-path", required=True)
    sub = parser.add_subparsers(dest="op", required=True)
    sub.add_parser("list-llms")
    sw = sub.add_parser("switch-llm")
    sw.add_argument("llm_no", type=int)
    sub.add_parser("reset-state")
    args = parser.parse_args()

    state_path = Path(args.state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    worker_log = state_path.parent / "probe_worker.log"
    logf = open(worker_log, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = logf

    if args.op == "list-llms":
        payload = {"ok": True, **probe_llms(args.ga_root, args.state_path)}
    elif args.op == "switch-llm":
        payload = {"ok": True, **switch_llm(args.ga_root, args.state_path, args.llm_no)}
    else:
        reset_state(args.state_path)
        payload = {"ok": True, "reset": True}
    sys.__stdout__.write(json.dumps(payload, ensure_ascii=False))
    sys.__stdout__.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
