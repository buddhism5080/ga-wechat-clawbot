import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ga_wechat_clawbot.wechat_client import WxClawClient


class WeChatClientTests(unittest.TestCase):
    def test_format_voice_encoder_command_supports_quoted_placeholders(self):
        client = WxClawClient.__new__(WxClawClient)
        client.voice_encoder_cmd = "pilk -i {input_q} -o {output_q}"
        cmd = client._format_voice_encoder_cmd(r"C:\\Users\\bot user\\输入 文件.wav", r"C:\\Temp\\输出 文件.silk")
        self.assertRegex(cmd, r"-i\s+['\"]?.*输入 文件\.wav['\"]?")
        self.assertRegex(cmd, r"-o\s+['\"]?.*输出 文件\.silk['\"]?")


if __name__ == "__main__":
    unittest.main()
