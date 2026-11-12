from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from studio.config import Settings
from studio.http_client import HttpResponse

ROOT = Path(__file__).resolve().parents[1]


def png_bytes(width: int = 320, height: int = 320, color=(20, 40, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_settings(tmp: Path, **overrides) -> Settings:
    values = dict(
        host="127.0.0.1",
        port=8088,
        base_dir=ROOT,
        database_path=tmp / "tasks.db",
        upload_dir=tmp / "uploads",
        output_dir=tmp / "outputs",
        workflow_dir=ROOT / "workflows",
        video_provider="minimax_h3_native",
        video_base_url="http://127.0.0.1:8089",
        video_api_key="test-key",
        video_timeout=5.0,
        video_poll_interval=0.05,
        video_task_timeout=30.0,
        image_provider="comfyui_h3_image",
        image_base_url="http://127.0.0.1:8190",
        image_timeout=5.0,
        image_poll_interval=0.05,
        image_task_timeout=30.0,
        image_workflow_1mp=ROOT / "workflows/image/t2i_1mp_api.json",
        image_workflow_4mp=ROOT / "workflows/image/t2i_4mp_api.json",
        idle_interval=0.05,
        max_upload_bytes=12 * 1024 * 1024,
        prompt_max_length=2000,
    )
    values.update(overrides)
    settings = Settings(**values)
    settings.ensure_dirs()
    return settings


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def request(self, method, url, *, headers=None, json_body=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "headers": headers or {}, "json": json_body, "timeout": timeout}
        )
        result = self.handler(method, url, headers or {}, json_body)
        if isinstance(result, HttpResponse):
            return result
        status, payload = result
        import json
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        content_type = "application/octet-stream" if isinstance(payload, bytes) else "application/json"
        return HttpResponse(status, {"content-type": content_type}, body)
