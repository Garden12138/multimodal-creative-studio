from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from studio.errors import StudioError
from studio.http_client import HttpResponse
from studio.providers.base import TaskInput
from studio.providers.image.comfyui_h3_image import ComfyUIH3ImageProvider, extract_images, interpret_history
from studio.providers.video.minimax_h3_native import (
    MinimaxH3NativeProvider,
    build_video_payload,
    download_url_allowed,
    interpret_video_task,
)
from studio.workflows_image import PROMPT_MARKER, prepare_workflow
from tests.helpers import ROOT, FakeTransport, make_settings, png_bytes


def video_task(mode="t2v", prompt="a cat", first=None, last=None):
    return TaskInput("task", "video", mode, prompt, {"duration_seconds": 5}, first, last)


class VideoContractTests(unittest.TestCase):
    def test_payload_modes_match_local_contract(self):
        t2v = build_video_payload(video_task(), None, None)
        self.assertEqual(t2v["model"], "MiniMax-H3")
        self.assertEqual(t2v["resolution"], "768P")
        self.assertEqual(t2v["duration"], 5)
        self.assertEqual(t2v["ratio"], "16:9")
        self.assertEqual(t2v["content"], [{"type": "text", "text": "a cat"}])

        i2v = build_video_payload(video_task("i2v"), "data:image/png;base64,QQ==", None)
        self.assertEqual(i2v["ratio"], "adaptive")
        self.assertEqual(i2v["content"][1]["role"], "first_frame")
        self.assertEqual(i2v["content"][1]["type"], "image_url")

        flf = build_video_payload(video_task("flf"), "data:image/png;base64,QQ==", "data:image/png;base64,Qg==")
        self.assertEqual([item["role"] for item in flf["content"] if "role" in item], ["first_frame", "last_frame"])

    def test_status_mapping_never_treats_unknown_as_success(self):
        self.assertEqual(interpret_video_task({"task": {"status": "queued"}}) .normalized, "running")
        self.assertEqual(interpret_video_task({"task": {"status": "running"}}).normalized, "running")
        self.assertEqual(interpret_video_task({"task": {"status": "mystery"}}).normalized, "running")
        failed = interpret_video_task({"task": {"status": "failed", "error": {"code": "local_generation_failed", "message": "boom"}}})
        self.assertEqual(failed.normalized, "failed")
        self.assertNotEqual(failed.normalized, "succeeded")
        cancelled = interpret_video_task({"task": {"status": "cancelled"}})
        self.assertEqual(cancelled.normalized, "failed")
        missing = interpret_video_task({"task": {"status": "succeeded", "content": {}}})
        self.assertEqual(missing.error_code, "RESULT_NOT_FOUND")
        ok = interpret_video_task({"task": {"status": "succeeded", "content": {"url": "http://192.168.10.104:8089/v2/files/abc?signature=1"}}})
        self.assertEqual(ok.normalized, "succeeded")
        self.assertTrue(ok.result_url.startswith("http://192.168.10.104:8089/v2/files/"))

    def test_download_url_allowlist(self):
        base = "http://127.0.0.1:8089"
        self.assertTrue(download_url_allowed("http://127.0.0.1:8089/v2/files/abc?expires=1&signature=test", base))
        self.assertTrue(download_url_allowed("http://192.168.10.104:8089/v2/files/abc?signature=test", base))
        self.assertFalse(download_url_allowed("http://169.254.169.254/v2/files/abc", base))
        self.assertFalse(download_url_allowed("https://example.com/v2/files/abc", base))
        self.assertFalse(download_url_allowed("file:///tmp/video.mp4", base))
        self.assertFalse(download_url_allowed("http://127.0.0.1:8089/other/abc", base))

    def test_submit_and_download_do_not_leak_or_call_comfy(self):
        secret = "super-secret-key"

        def handler(method, url, headers, body):
            return 401, {"error": {"message": secret, "http_code": "401"}}

        empty = FakeTransport(handler)
        unconfigured = MinimaxH3NativeProvider(
            make_settings(Path(tempfile.mkdtemp()), video_api_key=""),
            empty,
        )
        health = unconfigured.health()
        self.assertEqual(health["status"], "misconfigured")
        self.assertEqual(empty.calls, [])
        self.assertNotIn(secret, json.dumps(health))

        transport = FakeTransport(handler)
        provider = MinimaxH3NativeProvider(
            make_settings(Path(tempfile.mkdtemp()), video_api_key=secret, video_base_url="http://127.0.0.1:8089"),
            transport,
        )
        with self.assertRaises(StudioError) as caught:
            provider.submit(video_task())
        self.assertEqual(caught.exception.code, "PROVIDER_AUTH_FAILED")
        self.assertNotIn(secret, caught.exception.message)
        self.assertNotIn("8189", transport.calls[-1]["url"])
        self.assertTrue(all("8189" not in call["url"] for call in transport.calls))
        source = (ROOT / "studio/providers/video/minimax_h3_native.py").read_text(encoding="utf-8")
        self.assertNotIn("8189", source)

    def test_download_omits_authorization_header(self):
        def handler(method, url, headers, body):
            if url.endswith("/video_generation/abc"):
                return 200, {"task": {"status": "succeeded", "content": {"url": "http://192.168.10.104:8089/v2/files/abc?signature=1"}}}
            return HttpResponse(200, {"content-type": "video/mp4"}, b"\x00\x00\x00\x18ftypisom" + b"1234")

        transport = FakeTransport(handler)
        settings = make_settings(Path(tempfile.mkdtemp()), video_base_url="http://127.0.0.1:8089")
        provider = MinimaxH3NativeProvider(settings, transport)
        assets = provider.fetch_assets("abc", video_task())
        self.assertEqual(assets[0].mime_type, "video/mp4")
        download = transport.calls[-1]
        self.assertNotIn("Authorization", download["headers"])
        self.assertIn("/v2/files/abc", download["url"])


class ImageWorkflowTests(unittest.TestCase):
    def test_real_workflows_and_prompt_replacement(self):
        for resolution, megapixels, path in (
            ("1024x1024", 1.0, ROOT / "workflows/image/t2i_1mp_api.json"),
            ("2048x2048", 4.0, ROOT / "workflows/image/t2i_4mp_api.json"),
        ):
            prepared = prepare_workflow(path, megapixels, "studio prompt", "abc123", 7)
            blob = json.dumps(prepared)
            self.assertNotIn(PROMPT_MARKER, blob)
            self.assertIn("studio prompt", blob)
            sampler = next(node for node in prepared.values() if node["class_type"] == "KSampler")
            resolution_node = next(node for node in prepared.values() if node["class_type"] == "ResolutionSelector")
            attention = next(node for node in prepared.values() if node["class_type"] == "ModelAttentionBackend")
            switch = next(node for node in prepared.values() if node["class_type"] == "ComfySwitchNode")
            self.assertEqual(sampler["inputs"]["steps"], 6)
            self.assertEqual(sampler["inputs"]["cfg"], 1.0)
            self.assertEqual(sampler["inputs"]["seed"], 7)
            self.assertEqual(float(resolution_node["inputs"]["megapixels"]), megapixels)
            self.assertEqual(attention["inputs"]["attention"], "pytorch attention")
            self.assertIs(switch["inputs"]["switch"], False)
            save = next(node for node in prepared.values() if node["class_type"] == "SaveImage")
            self.assertEqual(save["inputs"]["filename_prefix"], "studio_abc123")

    def test_history_parser_and_submit_errors(self):
        pending, _record = interpret_history("p1", {})
        self.assertEqual(pending.normalized, "running")
        failed, _record = interpret_history("p1", {"p1": {"status": {"status_str": "error", "completed": True}, "outputs": {}}})
        self.assertEqual(failed.normalized, "failed")
        empty, record = interpret_history(
            "p1",
            {"p1": {"status": {"status_str": "success", "completed": True}, "outputs": {"9": {"images": []}}}},
        )
        self.assertEqual(empty.error_code, "RESULT_NOT_FOUND")
        self.assertEqual(extract_images(record), [])
        success, record = interpret_history(
            "p1",
            {"p1": {"status": {"status_str": "success", "completed": True}, "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}}},
        )
        self.assertEqual(success.normalized, "succeeded")
        self.assertEqual(extract_images(record)[0]["filename"], "a.png")
        self.assertEqual(extract_images({"outputs": {"1": {"images": [{"filename": "../x.png", "type": "output"}]}}}), [])

        def handler(method, url, headers, body):
            if method == "POST":
                return 200, {"node_errors": {}}
            return 200, {}

        transport = FakeTransport(handler)
        settings = make_settings(Path(tempfile.mkdtemp()))
        provider = ComfyUIH3ImageProvider(settings, transport, seed_fn=lambda: 9)
        with self.assertRaises(StudioError) as caught:
            provider.submit(TaskInput("t", "image", "t2i", "lake", {"resolution": "1024x1024"}))
        self.assertEqual(caught.exception.code, "PROVIDER_REJECTED")
        posted = transport.calls[0]["json"]["prompt"]
        self.assertNotIn(PROMPT_MARKER, json.dumps(posted))
        self.assertIn("lake", json.dumps(posted))
        self.assertTrue(all("8189" not in call["url"] for call in transport.calls))

    def test_fetch_uses_view_metadata(self):
        image = png_bytes()

        def handler(method, url, headers, body):
            if "/history/" in url:
                return 200, {"pid": {"status": {"status_str": "success", "completed": True}, "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "a", "type": "output"}]}}}}
            self.assertIn("filename=out.png", url)
            self.assertIn("subfolder=a", url)
            self.assertIn("type=output", url)
            return HttpResponse(200, {"content-type": "image/png"}, image)

        transport = FakeTransport(handler)
        provider = ComfyUIH3ImageProvider(make_settings(Path(tempfile.mkdtemp())), transport)
        assets = provider.fetch_assets("pid", TaskInput("tid", "image", "t2i", "lake", {"resolution": "1024x1024"}))
        self.assertEqual(assets[0].mime_type, "image/png")
        self.assertEqual(assets[0].data, image)


if __name__ == "__main__":
    unittest.main()
