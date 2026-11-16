from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from studio.config import Settings
from studio.http_client import HttpResponse

ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "a" * 32
ASSET_ID = "b" * 32
SIGNATURE = "c" * 64
EXPIRES = "1893456000"


def png_bytes(width: int = 320, height: int = 320, color=(20, 40, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def signed_image_path(asset_id: str = ASSET_ID, signature: str = SIGNATURE) -> str:
    return f"/v1/files/{asset_id}?expires={EXPIRES}&signature={signature}"


def signed_video_url(base_host: str, task_id: str = TASK_ID, signature: str = SIGNATURE) -> str:
    return f"http://{base_host}/v2/files/{task_id}?expires={EXPIRES}&signature={signature}"


def make_settings(tmp: Path, **overrides) -> Settings:
    values = dict(
        host="127.0.0.1",
        port=8088,
        base_dir=tmp,
        inference_base_url="http://127.0.0.1:8090",
        inference_api_key="test-key",
        inference_timeout=5.0,
        file_timeout=5.0,
        max_upload_bytes=12 * 1024 * 1024,
    )
    values.update(overrides)
    return Settings(**values)


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def request(
        self,
        method,
        url,
        *,
        headers=None,
        json_body=None,
        data=None,
        files=None,
        timeout=None,
        allow_redirects=True,
    ):
        call = {
            "method": method,
            "url": url,
            "headers": headers or {},
            "json": json_body,
            "data": data,
            "files": files,
            "timeout": timeout,
            "allow_redirects": allow_redirects,
        }
        self.calls.append(call)
        result = self.handler(call)
        if isinstance(result, HttpResponse):
            return result
        extra = {}
        if len(result) == 3:
            status, payload, extra = result
        else:
            status, payload = result
        if isinstance(payload, bytes):
            body = payload
            content_type = extra.get("content-type", "application/octet-stream")
        else:
            body = json.dumps(payload).encode()
            content_type = extra.get("content-type", "application/json")
        response_headers = {"content-type": content_type}
        response_headers.update({str(key).lower(): value for key, value in extra.items()})
        return HttpResponse(status, response_headers, body)


def route_transport(routes):
    def handler(call):
        path = urlsplit(call["url"]).path
        key = (call["method"], path)
        if key not in routes:
            raise AssertionError(f"unexpected upstream call {call['method']} {call['url']}")
        result = routes[key]
        if callable(result):
            return result(call)
        return result

    return FakeTransport(handler)
