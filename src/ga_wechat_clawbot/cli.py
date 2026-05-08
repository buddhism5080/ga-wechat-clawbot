from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

from .app import WeChatApp
from .config import load_config


OPTIONAL_IMPORTS = {
    "requests": "requests",
    "Crypto.Cipher": "pycryptodome",
    "qrcode": "qrcode",
    "PIL.Image": "pillow",
}


def _check_import(path: str) -> bool:
    try:
        return importlib.util.find_spec(path) is not None
    except ModuleNotFoundError:
        return False


def doctor(config_path: str) -> int:
    cfg = load_config(config_path)
    print(f"Config: {cfg.config_path}")
    print(f"GA root: {cfg.ga.root}")
    print(f"GA python: {cfg.ga.python}")
    print(f"State root: {cfg.storage.root}")
    ok = True
    if not cfg.ga.root.exists():
        print("[FAIL] GA root does not exist")
        ok = False
    if not (cfg.ga.root / "agentmain.py").exists():
        print("[FAIL] agentmain.py not found under GA root")
        ok = False
    if not ((cfg.ga.root / "mykey.py").exists() or (cfg.ga.root / "mykey.json").exists()):
        print("[WARN] mykey.py / mykey.json missing under GA root")
    for module_name, package_name in OPTIONAL_IMPORTS.items():
        present = _check_import(module_name)
        print(f"[{ 'OK' if present else 'MISS' }] optional dependency {package_name}")
        if not present:
            ok = False
    if cfg.wechat.token_file.exists():
        print(f"[OK] token file exists: {cfg.wechat.token_file}")
    else:
        print(f"[INFO] token file not found yet: {cfg.wechat.token_file} (QR login will create it)")
    return 0 if ok else 1


def serve(config_path: str) -> int:
    cfg = load_config(config_path)
    for env_key in ("HTTPS_PROXY", "https_proxy"):
        os.environ.pop(env_key, None)
    app = WeChatApp(cfg)
    app.run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ga-wechat-clawbot")
    parser.add_argument("--config", default="config.toml", help="Path to config TOML")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("serve")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor(args.config)
    if args.command == "serve":
        return serve(args.config)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
