from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_page_keeps_image_and_video_controls(self):
        for required in (
            "图片",
            "视频",
            "1024x1024",
            "2048x2048",
            "文生视频",
            "图生视频",
            "首末帧视频",
            "5 秒视频",
            "pick-image",
            "pick-video",
            "/api/health",
            "/api/generate/image",
            "/api/generate/video",
        ):
            self.assertIn(required, self.html)
        for banned in ("Music3", "音乐生成", "MiniMax-Music", "/api/v1/tasks", "8089", "8189", "8190", "8090", "comfyui", "INFERENCE_API_KEY"):
            self.assertNotIn(banned, self.html)

    def test_history_stores_only_the_task_index(self):
        self.assertEqual(self.html.count("localStorage.setItem"), 1)
        self.assertIn("studio.v2.history", self.html)
        start = self.html.index("function persistedHistory")
        block = self.html[start : self.html.index("function ", start + 1)]
        for field in ("task_id", "media_type", "mode", "prompt", "size", "duration", "created_at"):
            self.assertIn(field, block)
        for banned in ("status", "signature", "url", "api_key", "Authorization", "expires"):
            self.assertNotIn(banned, block)

    def test_terminal_states_stop_polling_and_submission_is_not_retried(self):
        self.assertIn("const TERMINAL_STATUS = new Set(['succeeded', 'failed', 'needs_review'])", self.html)
        self.assertIn("排队中", self.html)
        self.assertIn("生成中", self.html)
        self.assertIn("已完成", self.html)
        self.assertIn("失败", self.html)
        self.assertIn("提交状态不确定", self.html)
        self.assertIn("不会自动重新提交", self.html)
        self.assertIn("setInterval(() => refreshTasks(), 5000)", self.html)
        submit = self.html[self.html.index("addEventListener('submit'") :]
        self.assertLess(submit.index("if (!body.task_id)"), submit.index("rememberTask("))
        self.assertLess(submit.index("rememberTask("), submit.index("$('prompt').value = ''"))
        self.assertEqual(submit.count("/api/generate/image"), 1)
        self.assertEqual(submit.count("/api/generate/video"), 1)
        self.assertNotIn("while (", self.html)
        self.assertIn("/api/tasks/${encodeURIComponent(record.media_type)}/${encodeURIComponent(record.task_id)}", self.html)
        self.assertIn("download=1", self.html)


if __name__ == "__main__":
    unittest.main()
