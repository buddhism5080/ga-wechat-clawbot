import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ga_wechat_clawbot.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_expands_paths_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.toml"
            ga_root = tmp_path / "ga"
            ga_root.mkdir()
            cfg_path.write_text(
                f"""
[ga]
root = "{ga_root}"
python = "python3"

[wechat]
allowed_users = ["u1", "u2"]
media_dir = "{tmp_path / 'media'}"

[storage]
root = "{tmp_path / 'state'}"
""".strip(),
                "utf-8",
            )
            cfg = load_config(cfg_path)
            self.assertEqual(cfg.ga.root, ga_root.resolve())
            self.assertEqual(cfg.wechat.allowed_users, {"u1", "u2"})
            self.assertTrue(cfg.wechat.media_dir.exists())
            self.assertTrue(cfg.storage.root.exists())
            self.assertTrue(cfg.storage.log_dir.exists())


if __name__ == "__main__":
    unittest.main()
