import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ga_wechat_clawbot.rendering import (
    build_attachment_prompt,
    clean_agent_reply,
    extract_file_refs,
    format_ask_user_message,
    render_abort_message,
    render_error_message,
    render_final_reply,
    render_progress_update,
    route_path_kind,
    split_markdown_chunks,
)
from ga_wechat_clawbot.types import AttachmentRef


class RenderingTests(unittest.TestCase):
    def test_clean_agent_reply_strips_hidden_tags(self):
        raw = """**LLM Running (Turn 1) ...**
<thinking>secret</thinking>
# 标题
正文
<tool_use>{}</tool_use>
[FILE:/tmp/out.txt]
"""
        cleaned = clean_agent_reply(raw)
        self.assertIn("# 标题", cleaned)
        self.assertNotIn("secret", cleaned)
        self.assertNotIn("[FILE:", cleaned)

    def test_clean_agent_reply_truncates_language_code_fence(self):
        raw = "```python\n" + "\n".join(f"print({i})" for i in range(80)) + "\n```"
        cleaned = clean_agent_reply(raw)
        self.assertIn("... (20 more lines)", cleaned)

    def test_extract_file_refs_deduplicates(self):
        refs = extract_file_refs("[FILE:/tmp/a]\n[FILE:/tmp/a]\n[FILE:filepath]\n[FILE:./b.png]")
        self.assertEqual(refs, ["/tmp/a", "./b.png"])

    def test_format_ask_user_message(self):
        text = format_ask_user_message({"question": "下一步？", "candidates": ["继续", "回滚"]})
        self.assertIn("需要你来决定下一步", text)
        self.assertIn("1. 继续", text)

    def test_render_final_reply_ask_user(self):
        raw = str({"status": "INTERRUPT", "intent": "HUMAN_INTERVENTION", "data": {"question": "选环境", "candidates": ["测试", "生产"]}})
        rendered = render_final_reply(raw)
        self.assertTrue(rendered.ask_user)
        self.assertIn("选环境", rendered.text_chunks[0])

    def test_render_final_reply_summarizes_windows_generated_file_name(self):
        rendered = render_final_reply("done", generated_paths=[r"C:\\temp\\结果.png"])
        self.assertIn("结果.png", rendered.text_chunks[0])
        self.assertNotIn(r"C:\\temp\\结果.png", rendered.text_chunks[0])

    def test_render_progress_update(self):
        msg = render_progress_update(3, "正在读取文件", [{"tool_name": "ask_user", "args": {"question": "选环境", "candidates": ["测试"]}}])
        self.assertIn("第 3 轮", msg)
        self.assertIn("等待用户回复", msg)

    def test_render_progress_update_summarizes_common_tools(self):
        msg = render_progress_update(
            4,
            "处理中",
            [
                {"tool_name": "search_files", "args": {"pattern": "ask_user", "path": "/tmp/repo"}},
                {"tool_name": "terminal", "args": {"command": "pytest tests/test_app.py -v"}},
            ],
        )
        self.assertIn("搜索", msg)
        self.assertIn("pytest tests/test_app.py -v", msg)

    def test_render_progress_update_hides_raw_file_read_args(self):
        msg = render_progress_update(
            1,
            "准备检查 thunderbird-agent 状态",
            [{"tool_name": "file_read", "args": {"path": "../memory/thunderbird_agent.md", "start": 1, "count": 240, "keyword": "status"}}],
        )
        self.assertIn("读取文件", msg)
        self.assertIn("thunderbird_agent.md", msg)
        self.assertNotIn("../memory/thunderbird_agent.md", msg)
        self.assertNotIn('"start": 1', msg)

    def test_render_abort_message(self):
        msg = render_abort_message("用户请求停止")
        self.assertIn("任务已中止", msg)
        self.assertIn("用户请求停止", msg)

    def test_render_error_message_includes_traceback_excerpt(self):
        msg = render_error_message("ValueError: bad", "Traceback\nline1\nline2\nline3\nline4\n")
        self.assertIn("运行失败", msg)
        self.assertIn("ValueError: bad", msg)
        self.assertIn("Traceback", msg)

    def test_split_markdown_chunks_keeps_fences_balanced(self):
        text = "```python\n" + "\n".join(f"print({i})" for i in range(100)) + "\n```"
        chunks = split_markdown_chunks(text, limit=200)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk.count("```") % 2, 0)

    def test_attachment_prompt_and_routing(self):
        prompt = build_attachment_prompt([AttachmentRef(kind="voice", path="/tmp/a.silk", name="a.silk", transcript="你好")])
        self.assertIn("transcript=你好", prompt)
        self.assertEqual(route_path_kind("a.png"), "image")
        self.assertEqual(route_path_kind("a.silk"), "voice")
        self.assertEqual(route_path_kind("a.mp4"), "video")
        self.assertEqual(route_path_kind("a.txt"), "file")


if __name__ == "__main__":
    unittest.main()
