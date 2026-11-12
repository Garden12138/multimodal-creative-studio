from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendSmokeTests(unittest.TestCase):
    def test_composer_exposes_image_and_video_controls(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        for required in (
            "图片",
            "视频",
            "1024x1024",
            "2048x2048",
            "文生视频",
            "图生视频",
            "首末帧视频",
            "5 秒视频",
            "/api/v1/tasks",
            "/api/health",
            "pick-image",
            "pick-video",
        ):
            self.assertIn(required, html)
        self.assertNotIn("Music3", html)
        self.assertNotIn("音乐生成", html)
        self.assertNotIn("MiniMax-Music", html)


if __name__ == "__main__":
    unittest.main()
