from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from studio.errors import StudioError
from studio.http_client import HttpResponse, RequestsTransport
from studio.inference_client import InferenceClient, resolve_signed_url
from tests.helpers import (
    ASSET_ID,
    EXPIRES,
    SIGNATURE,
    TASK_ID,
    FakeTransport,
    make_settings,
    png_bytes,
    signed_image_path,
    signed_video_url,
)


def image_succeeded(signature: str = SIGNATURE):
    return {
        "task_id": TASK_ID,
        "model": "MiniMax-H3-Image",
        "status": "succeeded",
        "size": "1024x1024",
        "assets": [
            {
                "asset_id": ASSET_ID,
                "mime_type": "image/png",
                "url": signed_image_path(signature=signature),
                "sha256": "d" * 64,
                "size_bytes": 8,
            }
        ],
    }


def video_task(status: str, url: str | None = None, error: dict | None = None):
    task = {
        "id": TASK_ID,
        "model": "MiniMax-H3",
        "status": status,
        "created_at": 0,
        "updated_at": 0,
        "content": {"url": url} if url else {},
        "resolution": "608x352",
        "requested_resolution": "768P",
        "duration": 5,
        "ratio": "16:9",
        "task_type": "generation",
        "modality": "video",
        "resolution_source": "verified_workflow_baseline",
    }
    if error:
        task["error"] = error
    if status == "needs_review":
        task["attention_required"] = True
    return {"task": task}


class InferenceClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = make_settings(Path(self.tmp.name))

    def test_create_image_posts_exact_v1_contract_once(self):
        transport = FakeTransport(
            lambda _call: (
                202,
                {
                    "task_id": TASK_ID,
                    "model": "MiniMax-H3-Image",
                    "status": "queued",
                    "size": "2048x2048",
                },
            )
        )
        created = InferenceClient(self.settings, transport).create_image(
            "A cinematic mountain lake at sunrise", "2048x2048"
        )

        self.assertEqual(created["task_id"], TASK_ID)
        self.assertEqual(created["status"], "queued")
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "http://127.0.0.1:8090/v1/images/generations")
        self.assertEqual(
            call["json"],
            {
                "model": "MiniMax-H3-Image",
                "prompt": "A cinematic mountain lake at sunrise",
                "size": "2048x2048",
            },
        )
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        self.assertFalse(call["allow_redirects"])
        self.assertNotIn("test-key", call["url"])

    def test_create_image_does_not_retry_after_upstream_failure(self):
        transport = FakeTransport(lambda _call: (500, {"error": {"code": "PROVIDER_TIMEOUT", "message": "boom"}}))
        with self.assertRaises(StudioError):
            InferenceClient(self.settings, transport).create_image("lake", "1024x1024")
        self.assertEqual(len(transport.calls), 1)

    def test_create_video_posts_t2v_i2v_and_flf_without_retry(self):
        frames = {"first": ("image/png", png_bytes()), "last": ("image/png", png_bytes(330, 330))}
        seen = []

        def handler(call):
            seen.append(call["json"])
            return (200, {"task_id": TASK_ID})

        client = InferenceClient(self.settings, FakeTransport(handler))
        text = client.create_video("t2v", "A cinematic scene", None, None)
        reference = client.create_video("i2v", "The subject turns", frames["first"], None)
        both = client.create_video("flf", "Move to the last frame", frames["first"], frames["last"])

        self.assertEqual([text["task_id"], reference["task_id"], both["task_id"]], [TASK_ID, TASK_ID, TASK_ID])
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0]["content"], [{"type": "text", "text": "A cinematic scene"}])
        self.assertEqual(seen[0]["ratio"], "16:9")
        self.assertEqual(seen[1]["ratio"], "adaptive")
        self.assertEqual(seen[2]["ratio"], "adaptive")
        for payload in seen:
            self.assertEqual(payload["model"], "MiniMax-H3")
            self.assertEqual(payload["resolution"], "768P")
            self.assertEqual(payload["duration"], 5)
            self.assertNotIn("callback_url", payload)
        self.assertEqual(seen[1]["content"][1]["role"], "first_frame")
        self.assertEqual(seen[2]["content"][2]["role"], "last_frame")
        self.assertTrue(seen[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_image_submitting_is_running_and_public_view_hides_urls(self):
        transport = FakeTransport(
            lambda _call: (
                200,
                {
                    "task_id": TASK_ID,
                    "model": "MiniMax-H3-Image",
                    "status": "submitting",
                    "size": "1024x1024",
                    "assets": [],
                },
            )
        )
        public = InferenceClient(self.settings, transport).get_task("image", TASK_ID).public()
        self.assertEqual(public["status"], "running")
        self.assertFalse(public["file_ready"])
        encoded = json.dumps(public)
        self.assertNotIn("signature", encoded)
        self.assertNotIn("url", encoded)

    def test_succeeded_video_keeps_signed_url_off_the_public_view(self):
        signed = signed_video_url("192.168.10.104:8090")
        transport = FakeTransport(lambda _call: (200, video_task("succeeded", signed)))
        snapshot = InferenceClient(self.settings, transport).get_task("video", TASK_ID)
        public = snapshot.public()
        self.assertEqual(public["status"], "succeeded")
        self.assertTrue(public["file_ready"])
        self.assertEqual(public["duration"], 5)
        self.assertEqual(public["resolution"], "608x352")
        encoded = json.dumps(public)
        self.assertNotIn("signature", encoded)
        self.assertNotIn("192.168.10.104", encoded)
        self.assertEqual(snapshot.signed_url, signed)

    def test_needs_review_stays_unresolved_and_does_not_create_another_task(self):
        transport = FakeTransport(
            lambda _call: (
                200,
                video_task(
                    "needs_review",
                    error={"code": "submission_uncertain", "message": "Submission result is uncertain"},
                ),
            )
        )
        public = InferenceClient(self.settings, transport).get_task("video", TASK_ID).public()
        self.assertEqual(public["status"], "needs_review")
        self.assertTrue(public["attention_required"])
        self.assertEqual(public["error"]["code"], "submission_uncertain")
        self.assertFalse(public["file_ready"])
        self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_unknown_upstream_status_is_not_reported_as_success(self):
        transport = FakeTransport(lambda _call: (200, video_task("mystery")))
        public = InferenceClient(self.settings, transport).get_task("video", TASK_ID).public()
        self.assertNotEqual(public["status"], "succeeded")
        self.assertEqual(public["status"], "needs_review")
        self.assertTrue(public["attention_required"])
        self.assertFalse(public["file_ready"])

    def test_lan_signed_url_is_rewritten_to_the_configured_origin(self):
        signed = signed_video_url("192.168.10.104:8090")
        resolved = resolve_signed_url(self.settings.inference_base_url, signed, kind="video")
        self.assertEqual(
            resolved,
            f"http://127.0.0.1:8090/v2/files/{TASK_ID}?expires={EXPIRES}&signature={SIGNATURE}",
        )

    def test_relative_image_url_stays_on_the_configured_origin(self):
        resolved = resolve_signed_url(self.settings.inference_base_url, signed_image_path(), kind="image")
        self.assertEqual(
            resolved,
            f"http://127.0.0.1:8090/v1/files/{ASSET_ID}?expires={EXPIRES}&signature={SIGNATURE}",
        )

    def test_signed_url_from_another_port_or_host_is_rejected_before_a_request(self):
        calls = []
        client = InferenceClient(self.settings, FakeTransport(lambda call: calls.append(call["url"])))
        for signed in (
            signed_video_url("192.168.10.104:8189"),
            signed_video_url("127.0.0.1:8089"),
            signed_video_url("127.0.0.1:8190"),
            "http://example.test/v2/files/" + TASK_ID + f"?expires={EXPIRES}&signature={SIGNATURE}",
            "http://192.168.10.104:8090/secret",
            "//192.168.10.104:8090/v2/files/" + TASK_ID,
            signed_image_path(),
        ):
            with self.subTest(signed=signed):
                with self.assertRaises(StudioError):
                    resolve_signed_url(self.settings.inference_base_url, signed, kind="video")
        self.assertEqual(calls, [])

    def test_redirect_from_file_endpoint_is_not_followed(self):
        signed = signed_video_url("192.168.10.104:8090")
        urls = []

        def handler(call):
            urls.append(call["url"])
            if call["url"].endswith(f"/v2/query/video_generation/{TASK_ID}"):
                return (200, video_task("succeeded", signed))
            if call["url"].startswith(f"http://127.0.0.1:8090/v2/files/{TASK_ID}"):
                self.assertNotIn("Authorization", call["headers"])
                self.assertFalse(call["allow_redirects"])
                return HttpResponse(302, {"location": "http://192.168.10.104:8189/secret"}, b"")
            raise AssertionError(call["url"])

        with self.assertRaises(StudioError) as caught:
            InferenceClient(self.settings, FakeTransport(handler)).read_file("video", TASK_ID)
        self.assertEqual(caught.exception.code, "UPSTREAM_REDIRECT")
        self.assertEqual(len(urls), 2)
        self.assertFalse(any("8189" in url or url.endswith("/secret") for url in urls))

    def test_file_read_uses_the_latest_signature(self):
        signatures = ["ab" * 32, "cd" * 32]
        seen = []
        state = {"index": 0}

        def handler(call):
            path = urlsplit(call["url"]).path
            if path == f"/v1/images/generations/{TASK_ID}":
                signature = signatures[state["index"]]
                state["index"] += 1
                return (200, image_succeeded(signature))
            if path == f"/v1/files/{ASSET_ID}":
                seen.append(urlsplit(call["url"]).query)
                return (200, png_bytes(), {"content-type": "image/png"})
            raise AssertionError(call["url"])

        client = InferenceClient(self.settings, FakeTransport(handler))
        first = client.read_file("image", TASK_ID)
        second = client.read_file("image", TASK_ID)
        self.assertEqual(first[0], "image/png")
        self.assertEqual(second[1][:8], b"\x89PNG\r\n\x1a\n")
        self.assertIn(signatures[0], seen[0])
        self.assertIn(signatures[1], seen[1])

    def test_requests_transport_rewrites_lan_host_and_refuses_redirects(self):
        png = png_bytes()
        behavior = {"redirect": False}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                behavior["paths"].append(self.path)
                behavior["hosts"].append(self.headers.get("Host"))
                behavior["authorization"].append(self.headers.get("Authorization"))
                if self.path.startswith("/v2/query/"):
                    port = self.server.server_address[1]
                    payload = video_task("succeeded", signed_video_url(f"192.168.10.104:{port}"))
                    data = json.dumps(payload).encode()
                    self._send(200, data, "application/json")
                    return
                if self.path.startswith(f"/v2/files/{TASK_ID}"):
                    if behavior["redirect"]:
                        self.send_response(302)
                        self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/secret")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self._send(200, b"video-bytes", "video/mp4")
                    return
                if self.path.startswith("/secret"):
                    self._send(200, b"secret", "text/plain")
                    return
                self.send_response(404)
                self.end_headers()

            def _send(self, status, data, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, _format, *_args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        settings = make_settings(Path(self.tmp.name), inference_base_url=f"http://127.0.0.1:{port}")
        client = InferenceClient(settings, RequestsTransport())

        behavior["paths"] = []
        behavior["hosts"] = []
        behavior["authorization"] = []
        behavior["redirect"] = False
        mime, content, _filename = client.read_file("video", TASK_ID)
        self.assertEqual((mime, content), ("video/mp4", b"video-bytes"))
        self.assertEqual(behavior["hosts"][0], f"127.0.0.1:{port}")
        self.assertTrue(behavior["paths"][1].startswith(f"/v2/files/{TASK_ID}?"))
        self.assertIn(f"signature={SIGNATURE}", behavior["paths"][1])
        self.assertEqual(behavior["authorization"][0], "Bearer test-key")
        self.assertIsNone(behavior["authorization"][1])
        self.assertNotIn("192.168.10.104", " ".join(behavior["hosts"]))

        behavior["paths"] = []
        behavior["hosts"] = []
        behavior["authorization"] = []
        behavior["redirect"] = True
        with self.assertRaises(StudioError) as caught:
            client.read_file("video", TASK_ID)
        self.assertEqual(caught.exception.code, "UPSTREAM_REDIRECT")
        self.assertFalse(any(path.startswith("/secret") for path in behavior["paths"]))


if __name__ == "__main__":
    unittest.main()
