"""Client for multimodal-inference-api 1.0.0.

The image and video payloads match the v1.0.0 routes. Signed file URLs are
checked here, then requested from INFERENCE_API_BASE_URL. A video URL may name
another host on the same port; the signature does not cover that host.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from studio.config import Settings
from studio.errors import StudioError, redact
from studio.http_client import Transport

HEX_ID = re.compile(r"^[0-9a-f]{32}$")
SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
FILE_PATH = {
    "image": re.compile(r"^/v1/files/[0-9a-f]{32}$"),
    "video": re.compile(r"^/v2/files/[0-9a-f]{32}$"),
}
KNOWN_STATUS = {"queued", "submitting", "running", "succeeded", "failed", "needs_review"}
PUBLIC_STATUS = {"queued", "running", "succeeded", "failed", "needs_review"}
IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
VIDEO_MIME = {"video/mp4", "video/webm", "video/x-matroska"}
FILE_EXTENSION = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/x-matroska": "mkv",
}
IMAGE_MODEL = "MiniMax-H3-Image"
VIDEO_MODEL = "MiniMax-H3"


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    media_type: str
    status: str
    attention_required: bool
    error: dict | None
    size: str | None
    duration: int | None
    resolution: str | None
    signed_url: str | None

    def public(self) -> dict:
        body = {
            "task_id": self.task_id,
            "media_type": self.media_type,
            "status": self.status,
            "attention_required": self.attention_required,
            "error": self.error,
            "file_ready": self.status == "succeeded" and bool(self.signed_url),
        }
        if self.media_type == "image":
            body["size"] = self.size
        else:
            body["duration"] = self.duration
            body["resolution"] = self.resolution
        return body


def public_status(value: object) -> str:
    if value == "submitting":
        return "running"
    if value in PUBLIC_STATUS:
        return str(value)
    return "needs_review"


def resolve_signed_url(base_url: str, signed_url: str, *, kind: str) -> str:
    pattern = FILE_PATH.get(kind)
    if pattern is None:
        raise StudioError("INPUT_INVALID", "文件类型不支持。", 400)
    base = urlsplit(base_url)
    if base.scheme not in {"http", "https"} or not base.hostname or base.port is None:
        raise StudioError("CONFIG_INVALID", "推理服务地址无效。", 500)
    raw = (signed_url or "").strip()
    if not raw or raw.startswith("//") or "\\" in raw:
        raise StudioError("RESULT_URL_REJECTED", "下载地址无效。", 502)
    if raw.startswith("/"):
        parts = urlsplit(raw)
        if parts.scheme or parts.netloc:
            raise StudioError("RESULT_URL_REJECTED", "下载地址无效。", 502)
    else:
        parts = urlsplit(raw)
        if parts.scheme not in {"http", "https"} or parts.hostname is None:
            raise StudioError("RESULT_URL_REJECTED", "下载地址无效。", 502)
        if parts.username or parts.password:
            raise StudioError("RESULT_URL_REJECTED", "下载地址无效。", 502)
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if port != base.port:
            raise StudioError("RESULT_URL_REJECTED", "下载地址不属于配置的推理服务。", 502)
    if parts.fragment or not pattern.fullmatch(parts.path):
        raise StudioError("RESULT_URL_REJECTED", "下载地址不属于推理服务的文件路径。", 502)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    names = [key for key, _value in pairs]
    if len(pairs) != 2 or set(names) != {"expires", "signature"} or len(set(names)) != 2:
        raise StudioError("RESULT_URL_REJECTED", "下载地址的签名参数无效。", 502)
    values = dict(pairs)
    if not values["expires"].isdigit() or int(values["expires"]) <= 0 or not SIGNATURE.fullmatch(values["signature"]):
        raise StudioError("RESULT_URL_REJECTED", "下载地址的签名参数无效。", 502)
    return urlunsplit((base.scheme, base.netloc, parts.path, parts.query, ""))


class InferenceClient:
    def __init__(self, settings: Settings, transport: Transport):
        self.settings = settings
        self.transport = transport

    def health(self) -> dict:
        if not self.settings.inference_api_key:
            return _health_view("misconfigured")
        try:
            response = self._request("GET", "/v1/health", expected={200})
            body = _object(response.json())
        except StudioError:
            return _health_view("unreachable")
        providers = body.get("providers") if isinstance(body.get("providers"), dict) else {}
        image = _provider_status(providers.get("image"))
        video = _provider_status(providers.get("video"))
        overall = body.get("status") if body.get("status") in {"ok", "degraded", "unreachable", "misconfigured"} else "degraded"
        return {"inference": overall, "providers": {"image": {"status": image}, "video": {"status": video}}}

    def create_image(self, prompt: str, size: str) -> dict:
        response = self._request(
            "POST",
            "/v1/images/generations",
            json_body={"model": IMAGE_MODEL, "prompt": prompt, "size": size},
            expected={202},
        )
        body = _object(response.json())
        task_id = _require_id(body.get("task_id"))
        return {"task_id": task_id, "status": public_status(body.get("status")), "size": size}

    def create_video(
        self,
        mode: str,
        prompt: str,
        first: tuple[str, bytes] | None,
        last: tuple[str, bytes] | None,
    ) -> dict:
        response = self._request(
            "POST",
            "/v2/video_generation",
            json_body=_video_body(mode, prompt, first, last),
            expected={200},
        )
        body = _object(response.json())
        return {"task_id": _require_id(body.get("task_id")), "status": "queued", "mode": mode}

    def get_task(self, media_type: str, task_id: str) -> TaskSnapshot:
        task_id = _require_id(task_id, missing=StudioError("NOT_FOUND", "任务不存在。", 404))
        if media_type == "image":
            response = self._request("GET", f"/v1/images/generations/{task_id}", expected={200})
            return self._image_snapshot(task_id, _object(response.json()))
        if media_type == "video":
            response = self._request("GET", f"/v2/query/video_generation/{task_id}", expected={200})
            return self._video_snapshot(task_id, _object(response.json()))
        raise StudioError("NOT_FOUND", "任务不存在。", 404)

    def read_file(self, media_type: str, task_id: str) -> tuple[str, bytes, str]:
        snapshot = self.get_task(media_type, task_id)
        if snapshot.status != "succeeded" or not snapshot.signed_url:
            raise StudioError("RESULT_NOT_READY", "结果文件还不能下载。", 409, task_id)
        mime, content = self._fetch_signed(snapshot.signed_url, media_type)
        return mime, content, f"studio_{task_id[:8]}.{FILE_EXTENSION[mime]}"

    def _image_snapshot(self, task_id: str, body: dict) -> TaskSnapshot:
        if body.get("task_id") != task_id:
            raise StudioError("UPSTREAM_INVALID", "推理服务返回了其他任务。", 502)
        status, attention, error = self._status_fields(body.get("status"), body.get("error"))
        signed = None
        if status == "succeeded":
            signed = self._first_usable_asset(body.get("assets"), task_id)
        size = body.get("size") if isinstance(body.get("size"), str) else None
        return TaskSnapshot(task_id, "image", status, attention, error, size, None, None, signed)

    def _video_snapshot(self, task_id: str, body: dict) -> TaskSnapshot:
        task = body.get("task")
        if not isinstance(task, dict) or task.get("id") != task_id:
            raise StudioError("UPSTREAM_INVALID", "推理服务返回了其他任务。", 502)
        status, attention, error = self._status_fields(task.get("status"), task.get("error"))
        signed = None
        if status == "succeeded":
            content = task.get("content")
            url = content.get("url") if isinstance(content, dict) else None
            signed = self._usable_url(url, "video", task_id)
        duration = task.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, int):
            duration = 5
        resolution = task.get("resolution") if isinstance(task.get("resolution"), str) else None
        return TaskSnapshot(task_id, "video", status, attention, error, None, duration, resolution, signed)

    def _status_fields(self, raw_status: object, raw_error: object) -> tuple[str, bool, dict | None]:
        status = public_status(raw_status)
        if raw_status not in KNOWN_STATUS:
            return "needs_review", True, {"code": "UPSTREAM_STATUS", "message": "推理服务返回了无法识别的状态。"}
        if status in {"failed", "needs_review"}:
            return status, status == "needs_review", self._error_payload(raw_error)
        return status, False, None

    def _error_payload(self, raw: object) -> dict:
        if not isinstance(raw, dict):
            return {"code": "UPSTREAM_ERROR", "message": "推理服务没有返回错误说明。"}
        code = raw.get("code") if isinstance(raw.get("code"), str) and raw.get("code") else "UPSTREAM_ERROR"
        message = raw.get("message") if isinstance(raw.get("message"), str) and raw.get("message") else "推理服务没有返回错误说明。"
        return {"code": code, "message": self._safe(message)}

    def _first_usable_asset(self, assets: object, task_id: str) -> str | None:
        if not isinstance(assets, list):
            return None
        for asset in assets:
            if isinstance(asset, dict):
                signed = self._usable_url(asset.get("url"), "image", task_id)
                if signed:
                    return signed
        return None

    def _usable_url(self, url: object, kind: str, task_id: str) -> str | None:
        if not isinstance(url, str):
            return None
        try:
            resolved = resolve_signed_url(self.settings.inference_base_url, url, kind=kind)
        except StudioError:
            return None
        if kind == "video" and urlsplit(resolved).path != f"/v2/files/{task_id}":
            return None
        return url

    def _fetch_signed(self, signed_url: str, kind: str) -> tuple[str, bytes]:
        target = resolve_signed_url(self.settings.inference_base_url, signed_url, kind=kind)
        try:
            response = self.transport.request(
                "GET",
                target,
                headers={"Accept": "*/*"},
                timeout=self.settings.file_timeout,
                allow_redirects=False,
            )
        except TimeoutError as exc:
            raise StudioError("INFERENCE_TIMEOUT", "读取生成文件超时，未自动重试。", 504) from exc
        except ConnectionError as exc:
            raise StudioError("INFERENCE_UNAVAILABLE", "推理服务暂时不可达。", 502) from exc
        if 300 <= response.status_code < 400:
            raise StudioError("UPSTREAM_REDIRECT", "推理服务返回了重定向，已拒绝跟随。", 502)
        if response.status_code != 200 or not response.content:
            raise StudioError("FILE_UNAVAILABLE", "暂时无法读取生成文件。", 502)
        mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        allowed = IMAGE_MIME if kind == "image" else VIDEO_MIME
        if mime not in allowed:
            raise StudioError("FILE_UNAVAILABLE", "生成文件类型无法识别。", 502)
        return mime, response.content

    def _request(self, method: str, path: str, *, json_body: dict | None = None, expected: set[int]):
        if not self.settings.inference_api_key:
            raise StudioError("CONFIG_INVALID", "未配置推理服务密钥。", 503)
        if not path.startswith("/") or path.startswith("//"):
            raise StudioError("CONFIG_INVALID", "推理服务地址无效。", 500)
        try:
            response = self.transport.request(
                method,
                self.settings.inference_base_url + path,
                headers={
                    "Authorization": f"Bearer {self.settings.inference_api_key}",
                    "Accept": "application/json",
                },
                json_body=json_body,
                timeout=self.settings.inference_timeout,
                allow_redirects=False,
            )
        except TimeoutError as exc:
            raise StudioError("INFERENCE_TIMEOUT", "推理服务超时，未自动重试。", 504) from exc
        except ConnectionError as exc:
            raise StudioError("INFERENCE_UNAVAILABLE", "推理服务暂时不可达。", 502) from exc
        if 300 <= response.status_code < 400:
            raise StudioError("UPSTREAM_REDIRECT", "推理服务返回了重定向，已拒绝跟随。", 502)
        if response.status_code not in expected:
            self._raise_upstream(response)
        return response

    def _raise_upstream(self, response) -> None:
        if response.status_code in {401, 403}:
            raise StudioError("INFERENCE_UNAUTHORIZED", "推理服务拒绝了 Studio 的密钥。", 502)
        message = self._upstream_message(response)
        if response.status_code == 404:
            raise StudioError("NOT_FOUND", "任务不存在。", 404)
        if response.status_code == 422:
            raise StudioError(self._upstream_code(response), message, 422)
        status = response.status_code if response.status_code in {400, 409, 413, 415} else 502
        raise StudioError("UPSTREAM_REJECTED", message, status)

    def _upstream_message(self, response) -> str:
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            return "推理服务拒绝了这次请求。"
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str) and error.get("message"):
                return self._safe(error["message"])
        return "推理服务拒绝了这次请求。"

    def _upstream_code(self, response) -> str:
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            return "UPSTREAM_REJECTED"
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, str) and code:
            return code
        return "UPSTREAM_REJECTED"

    def _safe(self, text: str) -> str:
        cleaned = redact(text, [self.settings.inference_api_key])
        cleaned = re.sub(r"https?://\S+", "[REDACTED]", cleaned)
        return re.sub(r"/v[12]/files/\S+", "[REDACTED]", cleaned)


def _video_body(mode: str, prompt: str, first: tuple[str, bytes] | None, last: tuple[str, bytes] | None) -> dict:
    content: list[dict] = [{"type": "text", "text": prompt}]
    if first is not None:
        content.append(_image_content(first, "first_frame"))
    if last is not None:
        content.append(_image_content(last, "last_frame"))
    return {
        "model": VIDEO_MODEL,
        "content": content,
        "resolution": "768P",
        "duration": 5,
        "ratio": "16:9" if mode == "t2v" else "adaptive",
    }


def _image_content(frame: tuple[str, bytes], role: str) -> dict:
    mime, raw = frame
    encoded = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}, "role": role}


def _require_id(value: object, missing: StudioError | None = None) -> str:
    if isinstance(value, str) and HEX_ID.fullmatch(value):
        return value
    if missing is not None:
        raise missing
    raise StudioError("UPSTREAM_INVALID", "推理服务没有返回有效任务编号。", 502)


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise StudioError("UPSTREAM_INVALID", "推理服务返回了无法识别的响应。", 502)
    return value


def _provider_status(value: object) -> str:
    if isinstance(value, dict) and isinstance(value.get("status"), str) and value.get("status"):
        return str(value["status"])
    return "unreachable"


def _health_view(status: str) -> dict:
    return {
        "inference": status,
        "providers": {"image": {"status": status}, "video": {"status": status}},
    }
