from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import ensure_dir, expand_path


@dataclass
class GAConfig:
    root: Path
    python: str = sys.executable
    default_llm_no: int = 0
    turn_timeout_sec: int = 900
    session_idle_ttl_sec: int = 43200


@dataclass
class WeChatConfig:
    allowed_users: set[str] = field(default_factory=set)
    token_file: Path = Path("~/.wxbot/token.json")
    media_dir: Path = Path("./state/media")
    voice_encoder_cmd: str = ""
    progress_interval_sec: int = 12
    progress_turn_stride: int = 2
    command_aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class StorageConfig:
    root: Path
    log_dir: Path


@dataclass
class AppConfig:
    ga: GAConfig
    wechat: WeChatConfig
    storage: StorageConfig
    config_path: Path


def _allowed_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        value = [value]
    return {str(item).strip() for item in value if str(item).strip()}


def _string_mapping(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if not isinstance(value, dict):
        raise TypeError("command_aliases must be a TOML table/object")
    mapped = {}
    for key, raw in value.items():
        alias = str(key).strip()
        target = str(raw).strip()
        if alias and target:
            mapped[alias] = target
    return mapped


def load_config(path: str | Path) -> AppConfig:
    config_path = expand_path(path)
    raw = tomllib.loads(config_path.read_text("utf-8"))
    ga_raw = raw.get("ga") or {}
    wechat_raw = raw.get("wechat") or {}
    storage_raw = raw.get("storage") or {}

    ga = GAConfig(
        root=expand_path(ga_raw["root"]),
        python=str(ga_raw.get("python") or sys.executable),
        default_llm_no=int(ga_raw.get("default_llm_no", 0) or 0),
        turn_timeout_sec=max(60, int(ga_raw.get("turn_timeout_sec", 900) or 900)),
        session_idle_ttl_sec=max(300, int(ga_raw.get("session_idle_ttl_sec", 43200) or 43200)),
    )

    storage_root = ensure_dir(storage_raw.get("root") or config_path.parent / "state")
    log_dir = ensure_dir(storage_raw.get("log_dir") or storage_root / "logs")

    wechat = WeChatConfig(
        allowed_users=_allowed_set(wechat_raw.get("allowed_users")),
        token_file=expand_path(wechat_raw.get("token_file") or "~/.wxbot/token.json"),
        media_dir=ensure_dir(wechat_raw.get("media_dir") or storage_root / "media"),
        voice_encoder_cmd=str(wechat_raw.get("voice_encoder_cmd", "") or "").strip(),
        progress_interval_sec=max(3, int(wechat_raw.get("progress_interval_sec", 12) or 12)),
        progress_turn_stride=max(1, int(wechat_raw.get("progress_turn_stride", 2) or 2)),
        command_aliases=_string_mapping(wechat_raw.get("command_aliases")),
    )

    storage = StorageConfig(root=storage_root, log_dir=log_dir)
    return AppConfig(ga=ga, wechat=wechat, storage=storage, config_path=config_path)
