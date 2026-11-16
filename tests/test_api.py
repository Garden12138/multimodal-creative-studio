from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from studio.app_factory import create_app
from studio.config import Settings
from tests.helpers import (
    ASSET_ID,
    SIGNATURE,
    TASK_ID,
    make_settings,
    png_bytes,
    route_transport,
    signed_image_path,
    signed_video_url,
)


def image_body(size="1024x1024", status="succeeded", signature=SIGNATURE):
    assets = []
    if status == "succeeded":
        assets.append(
            {
                "asset_id": ASSET_ID,
                "mime_type": "image/png",
                "url": signed_image_path(signature=signature),
                "sha256": "d" * 64,
                "size_bytes": 8,
            }
        )
    return {
        "task_id": TASK_ID,
        "model": "MiniMax-H3-Image",
        "status": status,
        "size": size,
        "assets": assets,
    }


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transport = None
        self.settings = make_settings(Path(self.tmp.name))

    def client_for(self, routes):
        self.transport = route_transport(routes)
        app = create_app(self.settings, self.transport)
        return TestClient(app)

    def test_image_generation_proxies_preview_and_download_without_saving_a_copy(self):
        png = png_bytes()
        routes = {
            ("POST", "/v1/images/generations"): (202, image_body("2048x2048", "queued")),
            ("GET", f"/v1/images/generations/{TASK_ID}"): (200, image_body("2048x2048")),
            ("GET", f"/v1/files/{ASSET_ID}"): (200, png, {"content-type": "image/png"}),
        }
        with self.client_for(routes) as client:
            created = client.post("/api/generate/image", json={"prompt": "  lake sunrise  ", "size": "2048x2048"})
            self.assertEqual(created.status_code, 202)
            self.assertEqual(created.json()["task_id"], TASK_ID)
            self.assertEqual(created.json()["status"], "queued")
            self.assertEqual(self.transport.calls[0]["json"]["prompt"], "lake sunrise")
            self.assertEqual(self.transport.calls[0]["json"]["size"], "2048x2048")
            self.assertEqual(len(self.transport.calls), 1)

            preview = client.get(f"/api/tasks/image/{TASK_ID}/file")
            download = client.get(f"/api/tasks/image/{TASK_ID}/file?download=1")

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.content, png)
        self.assertEqual(preview.headers["content-type"], "image/png")
        self.assertIn("inline", preview.headers["content-disposition"])
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertNotIn("test-key", preview.text)
        query_paths = [urlsplit(call["url"]).path for call in self.transport.calls]
        self.assertEqual(
            query_paths,
            [
                "/v1/images/generations",
                f"/v1/images/generations/{TASK_ID}",
                f"/v1/files/{ASSET_ID}",
                f"/v1/images/generations/{TASK_ID}",
                f"/v1/files/{ASSET_ID}",
            ],
        )
        self.assertFalse(any(path.is_file() for path in Path(self.tmp.name).rglob("*")))

    def test_video_modes_each_create_one_upstream_task(self):
        created_payloads = []

        def create(call):
            created_payloads.append(call["json"])
            return (200, {"task_id": TASK_ID})

        routes = {("POST", "/v2/video_generation"): create}
        image = png_bytes()
        with self.client_for(routes) as client:
            text = client.post("/api/generate/video", data={"mode": "t2v", "prompt": "a cat walking"})
            reference = client.post(
                "/api/generate/video",
                data={"mode": "i2v", "prompt": "turn"},
                files={"first_frame": ("first.png", image, "image/png")},
            )
            frames = client.post(
                "/api/generate/video",
                data={"mode": "flf", "prompt": "between"},
                files={
                    "first_frame": ("first.png", image, "image/png"),
                    "last_frame": ("last.png", png_bytes(360, 360), "image/png"),
                },
            )
        self.assertEqual([text.status_code, reference.status_code, frames.status_code], [200, 200, 200])
        self.assertEqual(len(created_payloads), 3)
        self.assertEqual(created_payloads[0]["ratio"], "16:9")
        self.assertEqual(len(created_payloads[0]["content"]), 1)
        self.assertEqual(created_payloads[1]["content"][1]["role"], "first_frame")
        self.assertEqual(created_payloads[2]["content"][1]["role"], "first_frame")
        self.assertEqual(created_payloads[2]["content"][2]["role"], "last_frame")
        self.assertTrue(all(item.json()["task_id"] == TASK_ID for item in (text, reference, frames)))

    def test_invalid_input_does_not_call_inference(self):
        routes = {}
        image = png_bytes(100, 100)
        with self.client_for(routes) as client:
            rejected = [
                client.post("/api/generate/image", json={"prompt": "   ", "size": "1024x1024"}),
                client.post("/api/generate/image", json={"prompt": "lake", "size": "512x512"}),
                client.post("/api/generate/image", json={"prompt": "lake", "size": "1024x1024", "resolution": "1024x1024"}),
                client.post("/api/generate/video", data={"mode": "t2v", "prompt": "cat"}, files={"first_frame": ("a.png", png_bytes(), "image/png")}),
                client.post("/api/generate/video", data={"mode": "i2v", "prompt": "move"}),
                client.post(
                    "/api/generate/video",
                    data={"mode": "flf", "prompt": "move"},
                    files={"first_frame": ("a.png", png_bytes(), "image/png")},
                ),
                client.post(
                    "/api/generate/video",
                    data={"mode": "i2v", "prompt": "move"},
                    files={"first_frame": ("tiny.png", image, "image/png")},
                ),
                client.post("/api/generate/video", data={"mode": "nope", "prompt": "move"}),
                client.post("/api/generate/image", json={"prompt": "x" * 4001, "size": "1024x1024"}),
                client.post("/api/generate/video", data={"mode": "t2v", "prompt": "x" * 2001}),
            ]
        self.assertTrue(all(item.status_code == 422 for item in rejected))
        self.assertTrue(all(item.json()["error"]["code"] == "INPUT_INVALID" for item in rejected))
        self.assertEqual(self.transport.calls, [])

    def test_task_query_hides_signed_urls_and_reports_needs_review(self):
        signed = signed_video_url("192.168.10.104:8090")
        routes = {
            ("GET", f"/v2/query/video_generation/{TASK_ID}"): (
                200,
                {
                    "task": {
                        "id": TASK_ID,
                        "model": "MiniMax-H3",
                        "status": "needs_review",
                        "created_at": 1,
                        "updated_at": 2,
                        "content": {},
                        "resolution": "608x352",
                        "requested_resolution": "768P",
                        "duration": 5,
                        "ratio": "16:9",
                        "task_type": "generation",
                        "modality": "video",
                        "resolution_source": "verified_workflow_baseline",
                        "attention_required": True,
                        "error": {"code": "submission_uncertain", "message": f"uncertain {signed}"},
                    }
                },
            )
        }
        with self.client_for(routes) as client:
            response = client.get(f"/api/tasks/video/{TASK_ID}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "needs_review")
        self.assertTrue(body["attention_required"])
        self.assertNotIn(signed, response.text)
        self.assertNotIn("test-key", response.text)
        self.assertNotIn("signature", response.text)

    def test_foreign_file_url_is_not_requested(self):
        routes = {
            ("GET", f"/v2/query/video_generation/{TASK_ID}"): (
                200,
                {
                    "task": {
                        "id": TASK_ID,
                        "model": "MiniMax-H3",
                        "status": "succeeded",
                        "created_at": 1,
                        "updated_at": 2,
                        "content": {"url": signed_video_url("192.168.10.104:8189")},
                        "resolution": "608x352",
                        "requested_resolution": "768P",
                        "duration": 5,
                        "ratio": "16:9",
                        "task_type": "generation",
                        "modality": "video",
                        "resolution_source": "verified_workflow_baseline",
                    }
                },
            )
        }
        with self.client_for(routes) as client:
            response = client.get(f"/api/tasks/video/{TASK_ID}/file")
        self.assertEqual(response.status_code, 409)
        self.assertEqual([urlsplit(call["url"]).path for call in self.transport.calls], [f"/v2/query/video_generation/{TASK_ID}"])
        self.assertFalse(any("8189" in call["url"] for call in self.transport.calls))

    def test_health_uses_inference_status_and_drops_upstream_addresses(self):
        routes = {
            ("GET", "/v1/health"): (
                200,
                {
                    "service": "multimodal-inference-api",
                    "status": "degraded",
                    "providers": {
                        "image": {"provider": "comfyui_h3_image", "status": "ok", "base_url": "http://127.0.0.1:8190"},
                        "video": {"provider": "comfyui_h3_video", "status": "degraded", "base_url": "http://127.0.0.1:8189"},
                    },
                    "reserved_providers": {"music": {"status": "not_implemented"}},
                },
            )
        }
        with self.client_for(routes) as client:
            response = client.get("/api/health")
            page = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "web": "ok",
                "inference": "degraded",
                "providers": {"image": {"status": "ok"}, "video": {"status": "degraded"}},
            },
        )
        self.assertNotIn("8190", response.text)
        self.assertNotIn("8189", response.text)
        self.assertNotIn("8089", response.text)
        self.assertNotIn("comfyui", response.text.lower())
        self.assertNotIn("test-key", response.text)
        self.assertNotIn("test-key", page.text)
        self.assertNotIn("INFERENCE_API_KEY", page.text)

    def test_upstream_error_redacts_the_api_key_and_is_not_retried(self):
        routes = {
            ("POST", "/v1/images/generations"): (
                422,
                {"error": {"code": "INPUT_INVALID", "message": "rejected test-key", "type": "unprocessable_entity_error"}},
            )
        }
        with self.client_for(routes) as client:
            response = client.post("/api/generate/image", json={"prompt": "lake", "size": "1024x1024"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("test-key", response.text)
        self.assertIn("[REDACTED]", response.text)
        self.assertEqual(len(self.transport.calls), 1)

    def test_missing_api_key_does_not_call_inference(self):
        self.settings = make_settings(Path(self.tmp.name), inference_api_key="")
        with self.client_for({}) as client:
            health = client.get("/api/health")
            created = client.post("/api/generate/image", json={"prompt": "lake", "size": "1024x1024"})
        self.assertEqual(health.json()["inference"], "misconfigured")
        self.assertEqual(created.status_code, 503)
        self.assertEqual(self.transport.calls, [])

    def test_retired_routes_are_absent(self):
        with self.client_for({}) as client:
            self.assertEqual(client.get("/api/v1/tasks").status_code, 404)
            self.assertEqual(client.post("/api/v1/tasks", data={"prompt": "lake"}).status_code, 404)
            self.assertEqual(client.post("/api/tasks", data={"mode": "t2v", "prompt": "lake"}).status_code, 404)
            self.assertEqual(client.get("/api/tasks").status_code, 404)
            self.assertEqual(client.get(f"/api/tasks/{TASK_ID}/video").status_code, 404)

    def test_settings_read_only_the_inference_api_environment(self):
        names = [
            "INFERENCE_API_BASE_URL",
            "INFERENCE_API_KEY",
            "INFERENCE_API_TIMEOUT_SECONDS",
            "VIDEO_COMFYUI_BASE_URL",
            "IMAGE_COMFYUI_BASE_URL",
            "VIDEO_NATIVE_BASE_URL",
            "VIDEO_NATIVE_API_KEY",
            "COMFY_URL",
            "STUDIO_DATABASE_PATH",
        ]
        previous = {name: os.environ.get(name) for name in names}
        try:
            os.environ["INFERENCE_API_BASE_URL"] = "http://127.0.0.1:8090/"
            os.environ["INFERENCE_API_KEY"] = "env-key"
            os.environ["INFERENCE_API_TIMEOUT_SECONDS"] = "12"
            os.environ["VIDEO_COMFYUI_BASE_URL"] = "http://127.0.0.1:8189"
            os.environ["IMAGE_COMFYUI_BASE_URL"] = "http://127.0.0.1:8190"
            os.environ["VIDEO_NATIVE_BASE_URL"] = "http://127.0.0.1:8089"
            os.environ["VIDEO_NATIVE_API_KEY"] = "native-secret"
            os.environ["COMFY_URL"] = "http://127.0.0.1:8189"
            os.environ["STUDIO_DATABASE_PATH"] = "./tasks.db"
            settings = Settings.from_env()
            with self.assertRaises(RuntimeError):
                os.environ["INFERENCE_API_BASE_URL"] = "http://127.0.0.1:8090/v1"
                Settings.from_env()
            os.environ["INFERENCE_API_BASE_URL"] = "http://user:pass@127.0.0.1:8090"
            with self.assertRaises(RuntimeError):
                Settings.from_env()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.assertEqual(settings.inference_base_url, "http://127.0.0.1:8090")
        self.assertEqual(settings.inference_api_key, "env-key")
        self.assertEqual(settings.inference_timeout, 12.0)
        rendered = str(settings)
        self.assertNotIn("8189", rendered)
        self.assertNotIn("8190", rendered)
        self.assertNotIn("8089", rendered)
        self.assertNotIn("native-secret", rendered)

    def test_studio_package_no_longer_references_retired_backends(self):
        banned = ("8189", "8190", "8089", "comfyui", "sqlite", "providerregistry", "video_native", "comfy_url", "tasks.db")
        root = Path(__file__).resolve().parents[1] / "studio"
        offenders = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for word in banned:
                if word in text:
                    offenders.append(f"{path.name}:{word}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
