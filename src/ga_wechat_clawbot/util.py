from __future__ import annotations

import json
import ntpath
import os
import shutil
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any


def expand_path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def ensure_dir(path: str | os.PathLike[str]) -> Path:
    p = expand_path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return default


def atomic_write_json(path: str | os.PathLike[str], payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def safe_slug(value: str, max_len: int = 120) -> str:
    cleaned = []
    for ch in str(value or ""):
        cleaned.append(ch if ch.isalnum() or ch in "._-" else "_")
    out = "".join(cleaned).strip("._") or "session"
    return out[:max_len]


def portable_basename(path: str | os.PathLike[str]) -> str:
    raw = str(path or "").rstrip("/\\")
    if not raw:
        return ""
    return ntpath.basename(raw) or os.path.basename(raw) or Path(raw).name


def is_probably_absolute_path(path: str | os.PathLike[str]) -> bool:
    raw = str(path or "")
    if not raw:
        return False
    if Path(raw).is_absolute():
        return True
    return PureWindowsPath(raw).is_absolute()


def remove_tree(path: str | os.PathLike[str]) -> None:
    p = Path(path)
    if p.exists():
        shutil.rmtree(p)
