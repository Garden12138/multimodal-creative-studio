from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from tests.helpers import png_bytes


class UpstreamState:
    def __init__(self):
        self.video_base = ""
        self.image_base = ""
        self.api_key = "test-key"
        self.video_payloads = []
        self.image_prompts = []
        self.video_behavior = "ok"
        self.image_behavior = "ok"
        self.paths = []


def _video_handler(state: UpstreamState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            return

        def _json(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            return self.headers.get("Authorization") == f"Bearer {state.api_key}"

        def do_GET(self):
            state.paths.append(("GET", self.path))
            parsed = urlparse(self.path)
            if parsed.path == "/v2/health":
                if not self._authorized():
                    self._json(401, {"type": "error", "error": {"type": "authorized_error", "message": "Invalid or missing API key", "http_code": "401"}})
                    return
                self._json(200, {"status": "ok"})
                return
            if parsed.path.startswith("/v2/query/video_generation/"):
                if not self._authorized():
                    self._json(401, {"type": "error", "error": {"message": "Invalid or missing API key", "http_code": "401"}})
                    return
                task_id = parsed.path.rsplit("/", 1)[-1]
                content = {}
                if state.video_behavior != "no_url":
                    content = {"url": f"{state.video_base}/v2/files/{task_id}?expires=1&signature=test"}
                self._json(200, {"task": {"id": task_id, "model": "MiniMax-H3", "status": "succeeded", "content": content, "resolution": "608x352", "duration": 5}})
                return
            if parsed.path.startswith("/v2/files/"):
                body = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(404, {"type": "error", "error": {"message": "missing", "http_code": "404"}})

        def do_POST(self):
            state.paths.append(("POST", self.path))
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if urlparse(self.path).path != "/v2/video_generation":
                self._json(404, {"type": "error", "error": {"message": "missing", "http_code": "404"}})
                return
            if not self._authorized():
                self._json(401, {"type": "error", "error": {"message": self.headers.get("Authorization", ""), "http_code": "401"}})
                return
            state.video_payloads.append(json.loads(raw))
            task_id = uuid.uuid4().hex
            self._json(200, {"task_id": task_id})

    return Handler


def _image_handler(state: UpstreamState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            return

        def _json(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            state.paths.append(("GET", self.path.split("?", 1)[0]))
            path = urlparse(self.path).path
            if path == "/system_stats":
                self._json(200, {"system": {"comfyui_version": "0.45.0"}})
                return
            if path.startswith("/history/"):
                prompt_id = path.rsplit("/", 1)[-1]
                if state.image_behavior == "error":
                    record = {"status": {"status_str": "error", "completed": True, "messages": [["execution_error", {}]]}, "outputs": {}}
                elif state.image_behavior == "empty":
                    record = {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": {}}
                else:
                    record = {
                        "status": {"status_str": "success", "completed": True, "messages": [["execution_success", {}]]},
                        "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
                    }
                self._json(200, {prompt_id: record})
                return
            if path == "/view":
                body = png_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(404, {"error": "missing"})

        def do_POST(self):
            state.paths.append(("POST", self.path))
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if urlparse(self.path).path != "/prompt":
                self._json(404, {"error": "missing"})
                return
            body = json.loads(raw)
            state.image_prompts.append(body.get("prompt"))
            if "prompt_id" not in body and state.image_behavior == "no_id":
                self._json(200, {})
                return
            self._json(200, {"prompt_id": uuid.uuid4().hex})

    return Handler


class UpstreamMock:
    def __init__(self):
        self.state = UpstreamState()
        self.video = ThreadingHTTPServer(("127.0.0.1", 0), _video_handler(self.state))
        self.image = ThreadingHTTPServer(("127.0.0.1", 0), _image_handler(self.state))
        self.state.video_base = f"http://127.0.0.1:{self.video.server_address[1]}"
        self.state.image_base = f"http://127.0.0.1:{self.image.server_address[1]}"
        self.threads = []

    def __enter__(self):
        for server in (self.video, self.image):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def __exit__(self, *_args):
        self.video.shutdown()
        self.image.shutdown()
        self.video.server_close()
        self.image.server_close()
