from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app_factory import create_app
from studio.database import connect, get_task
from studio.providers.base import ProviderAsset, ProviderSubmission, ProviderTaskStatus
from studio.providers.registry import ProviderRegistry
from tests.helpers import make_settings, png_bytes
from tests.upstream_mock import UpstreamMock


class ScriptedProvider:
    def __init__(self, provider_id, media_type, mode, behavior="success"):
        self.provider_id = provider_id
        self.supported_media_types = (media_type,)
        self.supported_modes = (mode,)
        self.behavior = behavior
        self.submits = 0

    def health(self):
        payload = {"provider": self.provider_id, "status": "ok"}
        if self.provider_id == "comfyui_h3_image":
            payload["workflows"] = {"t2i_1mp": True, "t2i_4mp": True}
        return payload

    def submit(self, task):
        self.submits += 1
        if self.behavior == "submit_timeout":
            from studio.errors import StudioError
            raise StudioError("PROVIDER_TIMEOUT", "视频服务提交超时，未自动重试。", 504, task.task_id)
        return ProviderSubmission("provider-task-1", "queued")

    def poll(self, _provider_task_id):
        if self.behavior == "running":
            return ProviderTaskStatus("running", "running")
        if self.behavior == "unknown":
            return ProviderTaskStatus("mystery", "not-a-status")
        if self.behavior == "fail":
            return ProviderTaskStatus("failed", "failed", "PROVIDER_REJECTED", "生成失败。")
        return ProviderTaskStatus("succeeded", "succeeded")

    def fetch_assets(self, _provider_task_id, task):
        if task.media_type == "image":
            return [ProviderAsset("image", "image/png", png_bytes(), f"{task.task_id}.png")]
        return [ProviderAsset("video", "video/mp4", b"video-bytes", f"{task.task_id}.mp4")]


def wait_for(settings, task_id, timeout=5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        with connect(settings) as conn:
            last = get_task(conn, task_id)
        if last and last.state in {"succeeded", "failed"}:
            return last
        time.sleep(0.05)
    return last


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = ScriptedProvider("minimax_h3_native", "video", "t2v")
        self.image = ScriptedProvider("comfyui_h3_image", "image", "t2i")
        self.settings = make_settings(Path(self.tmp.name))
        self.app = create_app(self.settings, ProviderRegistry([self.video, self.image]), start_workers=True)

    def test_validation_legacy_and_v1_share_one_task_table(self):
        with TestClient(self.app) as client:
            rejected = client.post(
                "/api/v1/tasks",
                data={"media_type": "video", "mode": "t2v", "prompt": "cat"},
                files={"first_frame": ("a.png", png_bytes(), "image/png")},
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(rejected.json()["error"]["code"], "INPUT_INVALID")
            self.assertNotIn(str(self.settings.output_dir), rejected.text)

            missing = client.post("/api/tasks", data={"mode": "i2v", "prompt": "move"})
            self.assertEqual(missing.status_code, 400)
            self.assertIn("detail", missing.json())

            image_frames = client.post(
                "/api/v1/tasks",
                data={"media_type": "image", "mode": "t2i", "prompt": "lake", "resolution": "1024x1024"},
                files={"first_frame": ("a.png", png_bytes(), "image/png")},
            )
            self.assertEqual(image_frames.status_code, 400)

            bad_resolution = client.post("/api/v1/tasks", data={"media_type": "image", "mode": "t2i", "prompt": "lake", "resolution": "512x512"})
            self.assertEqual(bad_resolution.status_code, 400)

            tiny = client.post(
                "/api/tasks",
                data={"mode": "i2v", "prompt": "move"},
                files={"first_frame": ("tiny.png", png_bytes(100, 100), "image/png")},
            )
            self.assertEqual(tiny.status_code, 400)

            created = client.post("/api/tasks", data={"mode": "t2v", "prompt": "a cat walking"})
            self.assertEqual(created.status_code, 200)
            body = created.json()
            self.assertIn("id", body)
            self.assertEqual(body["state"], "queued")
            task = wait_for(self.settings, body["id"])
            self.assertEqual(task.state, "succeeded")
            self.assertEqual(task.provider_task_id, "provider-task-1")
            self.assertEqual(self.video.submits, 1)

            listed = client.get("/api/tasks")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()[0]["id"], body["id"])
            self.assertIn("state", listed.json()[0])
            v1 = client.get(f"/api/v1/tasks/{body['id']}")
            self.assertEqual(v1.status_code, 200)
            self.assertEqual(v1.json()["task_id"], body["id"])
            self.assertEqual(v1.json()["provider"], "minimax_h3_native")
            asset = v1.json()["assets"][0]
            content = client.get(asset["url"])
            self.assertEqual(content.status_code, 200)
            self.assertEqual(content.headers["content-type"], "video/mp4")
            self.assertEqual(content.content, b"video-bytes")
            legacy_video = client.get(f"/api/tasks/{body['id']}/video")
            self.assertEqual(legacy_video.status_code, 200)
            self.assertEqual(legacy_video.content, b"video-bytes")
            self.assertEqual(client.get("/api/v1/tasks/missing").status_code, 404)

            image = client.post("/api/v1/tasks", data={"media_type": "image", "mode": "t2i", "prompt": "lake", "resolution": "2048x2048"})
            self.assertEqual(image.status_code, 202)
            image_task = wait_for(self.settings, image.json()["task_id"])
            self.assertEqual(image_task.state, "succeeded")
            image_body = client.get(f"/api/v1/tasks/{image_task.id}").json()
            self.assertEqual(image_body["params"]["resolution"], "2048x2048")
            preview = client.get(image_body["assets"][0]["url"])
            self.assertEqual(preview.headers["content-type"], "image/png")
            download = client.get(image_body["assets"][0]["url"] + "?download=1")
            self.assertIn("attachment", download.headers["content-disposition"])
            legacy_items = client.get("/api/tasks").json()
            self.assertTrue(all(item["mode"] != "t2i" for item in legacy_items))
            health = client.get("/api/health").json()
            self.assertEqual(health["web"], "ok")
            self.assertEqual(health["database"], "ok")
            self.assertEqual(health["providers"]["video"]["provider"], "minimax_h3_native")
            self.assertEqual(health["providers"]["image"]["workflows"]["t2i_4mp"], True)

    def test_unknown_provider_status_and_timeout_do_not_succeed(self):
        self.video.behavior = "unknown"
        with TestClient(self.app) as client:
            created = client.post("/api/v1/tasks", data={"media_type": "video", "mode": "t2v", "prompt": "unknown"})
            task = wait_for(self.settings, created.json()["task_id"])
        self.assertEqual(task.state, "failed")
        self.assertEqual(task.error_code, "INTERNAL_ERROR")
        self.assertEqual(self.video.submits, 1)

    def test_timeout_does_not_resubmit(self):
        self.video.behavior = "running"
        self.settings = make_settings(
            Path(self.tmp.name) / "timeout",
            video_task_timeout=0.2,
            video_poll_interval=0.05,
            idle_interval=0.05,
        )
        self.app = create_app(self.settings, ProviderRegistry([self.video, self.image]), start_workers=True)
        with TestClient(self.app) as client:
            created = client.post("/api/v1/tasks", data={"media_type": "video", "mode": "t2v", "prompt": "wait"})
            task = wait_for(self.settings, created.json()["task_id"], timeout=4)
        self.assertEqual(task.state, "failed")
        self.assertEqual(task.error_code, "PROVIDER_TIMEOUT")
        self.assertEqual(self.video.submits, 1)


class LiveAdapterTests(unittest.TestCase):
    def test_mock_upstream_image_and_video_round_trip(self):
        with UpstreamMock() as upstream, tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(
                Path(tmp),
                video_base_url=upstream.state.video_base,
                image_base_url=upstream.state.image_base,
                video_api_key="test-key",
            )
            app = create_app(settings, start_workers=True)
            with TestClient(app) as client:
                health = client.get("/api/health")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json()["providers"]["video"]["status"], "ok")
                self.assertEqual(health.json()["providers"]["image"]["status"], "ok")
                self.assertNotIn("test-key", health.text)
                image = client.post("/api/v1/tasks", data={"media_type": "image", "mode": "t2i", "prompt": "mountain lake", "resolution": "1024x1024"})
                image_row = wait_for(settings, image.json()["task_id"])
                self.assertEqual(image_row.state, "succeeded", image_row.error)
                prompt = upstream.state.image_prompts[0]
                self.assertNotIn("__H3_IMAGE_STUDIO_PROMPT__", __import__("json").dumps(prompt))
                self.assertEqual(
                    float(next(node["inputs"]["megapixels"] for node in prompt.values() if node["class_type"] == "ResolutionSelector")),
                    1.0,
                )
                self.assertEqual(next(node["inputs"]["steps"] for node in prompt.values() if node["class_type"] == "KSampler"), 6)
                large = client.post("/api/v1/tasks", data={"media_type": "image", "mode": "t2i", "prompt": "wide lake", "resolution": "2048x2048"})
                large_row = wait_for(settings, large.json()["task_id"])
                self.assertEqual(large_row.state, "succeeded", large_row.error)
                self.assertEqual(
                    float(next(node["inputs"]["megapixels"] for node in upstream.state.image_prompts[1].values() if node["class_type"] == "ResolutionSelector")),
                    4.0,
                )
                video = client.post("/api/v1/tasks", data={"media_type": "video", "mode": "t2v", "prompt": "a cat"})
                video_row = wait_for(settings, video.json()["task_id"])
                self.assertEqual(video_row.state, "succeeded", video_row.error)
                payload = upstream.state.video_payloads[0]
                self.assertEqual(payload["model"], "MiniMax-H3")
                self.assertEqual(payload["content"], [{"type": "text", "text": "a cat"}])
                self.assertTrue(all("8189" not in path for _method, path in upstream.state.paths))
                i2v = client.post(
                    "/api/v1/tasks",
                    data={"media_type": "video", "mode": "i2v", "prompt": "smile"},
                    files={"first_frame": ("first.png", png_bytes(), "image/png")},
                )
                i2v_row = wait_for(settings, i2v.json()["task_id"])
                self.assertEqual(i2v_row.state, "succeeded", i2v_row.error)
                self.assertEqual(upstream.state.video_payloads[1]["content"][1]["role"], "first_frame")
                flf = client.post(
                    "/api/v1/tasks",
                    data={"media_type": "video", "mode": "flf", "prompt": "transition"},
                    files={
                        "first_frame": ("first.png", png_bytes(), "image/png"),
                        "last_frame": ("last.png", png_bytes(360, 360, (1, 2, 3)), "image/png"),
                    },
                )
                flf_row = wait_for(settings, flf.json()["task_id"])
                self.assertEqual(flf_row.state, "succeeded", flf_row.error)
                roles = [item.get("role") for item in upstream.state.video_payloads[2]["content"]]
                self.assertEqual(roles, [None, "first_frame", "last_frame"])
                history = client.get("/api/v1/tasks?limit=30").json()["items"]
                self.assertGreaterEqual(len(history), 5)
            with TestClient(create_app(settings, start_workers=False)) as restarted:
                again = restarted.get(f"/api/v1/tasks/{image_row.id}")
                self.assertEqual(again.status_code, 200)
                self.assertEqual(again.json()["status"], "succeeded")
                content = restarted.get(again.json()["assets"][0]["url"])
                self.assertEqual(content.status_code, 200)
                self.assertGreater(len(content.content), 32)

            upstream.state.video_behavior = "no_url"
            with TestClient(create_app(settings, start_workers=True)) as client:
                created = client.post("/api/v1/tasks", data={"media_type": "video", "mode": "t2v", "prompt": "missing result"})
                missing = wait_for(settings, created.json()["task_id"])
            self.assertEqual(missing.state, "failed")
            self.assertEqual(missing.error_code, "RESULT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
