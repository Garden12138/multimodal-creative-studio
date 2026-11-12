from __future__ import annotations

import base64
import ipaddress
import logging
import re
from urllib.parse import quote, urlparse

from studio.config import Settings
from studio.errors import StudioError
from studio.http_client import Transport
from studio.providers.base import ProviderAsset, ProviderSubmission, ProviderTaskStatus, TaskInput
from studio.storage import resolve_under

logger = logging.getLogger("studio.video")

VIDEO_MODEL = "MiniMax-H3"
IN_PROGRESS = {"queued", "running"}
TERMINAL_FAILURE = {"failed", "cancelled"}


def build_video_payload(task: TaskInput, first_data_url: str | None, last_data_url: str | None) -> dict:
    content = [{"type": "text", "text": task.prompt}]
    if task.mode in {"i2v", "flf"}:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": first_data_url},
                "role": "first_frame",
            }
        )
    if task.mode == "flf":
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": last_data_url},
                "role": "last_frame",
            }
        )
    return {
        "model": VIDEO_MODEL,
        "content": content,
        "resolution": "768P",
        "duration": 5,
        "ratio": "16:9" if task.mode == "t2v" else "adaptive",
    }


def interpret_video_task(payload: dict) -> ProviderTaskStatus:
    if not isinstance(payload, dict) or not isinstance(payload.get("task"), dict):
        return ProviderTaskStatus(
            "invalid",
            "failed",
            "PROVIDER_REJECTED",
            "视频服务返回了无法识别的任务结构。",
        )
    task = payload["task"]
    status = task.get("status")
    if not isinstance(status, str) or not status:
        return ProviderTaskStatus("unknown", "running")
    if status in IN_PROGRESS:
        return ProviderTaskStatus(status, "running")
    if status == "succeeded":
        content = task.get("content") if isinstance(task.get("content"), dict) else {}
        url = content.get("url") if isinstance(content, dict) else None
        if not isinstance(url, str) or not url.strip():
            return ProviderTaskStatus(
                status,
                "failed",
                "RESULT_NOT_FOUND",
                "视频任务已完成，但没有结果地址。",
            )
        return ProviderTaskStatus(status, "succeeded", result_url=url)
    if status in TERMINAL_FAILURE:
        message = "视频任务已取消。" if status == "cancelled" else "视频生成失败。"
        return ProviderTaskStatus(status, "failed", "PROVIDER_REJECTED", message)
    return ProviderTaskStatus(status, "running")


def download_url_allowed(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    base = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    host = parsed.hostname
    if not host or not parsed.path.startswith("/v2/files/"):
        return False
    if base.hostname and host == base.hostname:
        return True
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or (address.is_private and not address.is_link_local)


def _safe_id(value: str) -> str:
    if not value or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", value):
        raise StudioError("PROVIDER_REJECTED", "视频服务返回了无效的任务编号。", 502)
    return value


class MinimaxH3NativeProvider:
    provider_id = "minimax_h3_native"
    supported_media_types = ("video",)
    supported_modes = ("t2v", "i2v", "flf")

    def __init__(self, settings: Settings, transport: Transport):
        self.settings = settings
        self.transport = transport

    def _timeout(self) -> tuple[float, float]:
        return (5, self.settings.video_timeout)

    def _auth_headers(self, json_body: bool) -> dict[str, str]:
        key = self.settings.video_api_key
        if not key:
            raise StudioError("PROVIDER_AUTH_FAILED", "视频服务认证失败，请检查服务端密钥配置。", 503)
        headers = {"Authorization": f"Bearer {key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _map_http_error(self, status_code: int, task_id: str | None = None) -> StudioError:
        if status_code in {401, 403}:
            return StudioError("PROVIDER_AUTH_FAILED", "视频服务认证失败，请检查服务端密钥配置。", 503, task_id)
        if status_code == 404:
            return StudioError("RESULT_NOT_FOUND", "上游视频任务不存在。", 502, task_id)
        if status_code in {400, 409, 413, 415, 422}:
            return StudioError("PROVIDER_REJECTED", "视频服务拒绝了这次请求。", 502, task_id)
        if status_code in {502, 503}:
            return StudioError("PROVIDER_UNAVAILABLE", "视频服务暂时不可用。", 503, task_id)
        return StudioError("PROVIDER_UNAVAILABLE", "视频服务暂时不可用。", 503, task_id)

    def health(self) -> dict:
        if not self.settings.video_api_key:
            return {"provider": self.provider_id, "status": "misconfigured"}
        try:
            response = self.transport.request(
                "GET",
                f"{self.settings.video_base_url}/v2/health",
                headers=self._auth_headers(json_body=False),
                timeout=(3, 5),
            )
        except TimeoutError:
            return {"provider": self.provider_id, "status": "unreachable"}
        except ConnectionError:
            return {"provider": self.provider_id, "status": "unreachable"}
        except StudioError:
            return {"provider": self.provider_id, "status": "misconfigured"}
        if response.status_code in {401, 403}:
            return {"provider": self.provider_id, "status": "misconfigured"}
        if 200 <= response.status_code < 300:
            return {"provider": self.provider_id, "status": "ok"}
        return {"provider": self.provider_id, "status": "degraded"}

    def _data_url(self, stored: str | None) -> str:
        if not stored:
            raise StudioError("INPUT_INVALID", "缺少参考图片。", 400)
        path = resolve_under(self.settings.upload_dir, stored)
        if not path.is_file():
            raise StudioError("INPUT_INVALID", "参考图片不存在。", 400)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def submit(self, task: TaskInput) -> ProviderSubmission:
        first = self._data_url(task.first_image) if task.mode in {"i2v", "flf"} else None
        last = self._data_url(task.last_image) if task.mode == "flf" else None
        payload = build_video_payload(task, first, last)
        try:
            response = self.transport.request(
                "POST",
                f"{self.settings.video_base_url}/v2/video_generation",
                headers=self._auth_headers(json_body=True),
                json_body=payload,
                timeout=self._timeout(),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "视频服务提交超时，未自动重试。", 504, task.task_id) from exc
        except ConnectionError as exc:
            raise StudioError("PROVIDER_UNAVAILABLE", "视频服务不可达，未自动重试。", 503, task.task_id) from exc
        if response.status_code >= 400:
            logger.info("video submit rejected status=%s", response.status_code)
            raise self._map_http_error(response.status_code, task.task_id)
        try:
            body = response.json()
        except Exception as exc:
            raise StudioError("PROVIDER_REJECTED", "视频服务返回了无法解析的提交结果。", 502, task.task_id) from exc
        provider_task_id = body.get("task_id") if isinstance(body, dict) else None
        if not isinstance(provider_task_id, str):
            raise StudioError("PROVIDER_REJECTED", "视频服务没有返回任务编号。", 502, task.task_id)
        return ProviderSubmission(_safe_id(provider_task_id), "queued")

    def poll(self, provider_task_id: str) -> ProviderTaskStatus:
        safe_task_id = _safe_id(provider_task_id)
        try:
            response = self.transport.request(
                "GET",
                f"{self.settings.video_base_url}/v2/query/video_generation/{quote(safe_task_id)}",
                headers=self._auth_headers(json_body=False),
                timeout=self._timeout(),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "查询视频任务超时。", 504) from exc
        except ConnectionError as exc:
            raise StudioError("PROVIDER_UNAVAILABLE", "视频服务不可达。", 503) from exc
        if response.status_code >= 400:
            logger.info("video poll rejected status=%s", response.status_code)
            raise self._map_http_error(response.status_code)
        try:
            body = response.json()
        except Exception as exc:
            raise StudioError("PROVIDER_REJECTED", "视频任务状态无法解析。", 502) from exc
        interpreted = interpret_video_task(body)
        if interpreted.normalized == "failed":
            code = None
            task = body.get("task") if isinstance(body, dict) else None
            error = task.get("error") if isinstance(task, dict) else None
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                code = error["code"]
            if code and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", code):
                logger.info("video provider terminal status=%s code=%s", interpreted.provider_status, code)
        return interpreted

    def fetch_assets(self, provider_task_id: str, task: TaskInput) -> list[ProviderAsset]:
        status = self.poll(provider_task_id)
        if status.normalized != "succeeded" or not status.result_url:
            raise StudioError("RESULT_NOT_FOUND", "视频结果还不存在。", 502, task.task_id)
        if not download_url_allowed(status.result_url, self.settings.video_base_url):
            logger.info("video result url rejected")
            raise StudioError("RESULT_NOT_FOUND", "视频结果地址无效。", 502, task.task_id)
        try:
            response = self.transport.request(
                "GET",
                status.result_url,
                headers={"Accept": "video/mp4,video/*"},
                timeout=(5, max(self.settings.video_timeout, 120)),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "下载视频超时。", 504, task.task_id) from exc
        except ConnectionError as exc:
            raise StudioError("ASSET_STORAGE_FAILED", "下载视频失败。", 502, task.task_id) from exc
        content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].strip().lower()
        if response.status_code >= 400 or not response.content or not content_type.startswith("video/"):
            logger.info("video download rejected status=%s", response.status_code)
            raise StudioError("RESULT_NOT_FOUND", "视频结果无法下载。", 502, task.task_id)
        if len(response.content) > 200 * 1024 * 1024:
            raise StudioError("ASSET_STORAGE_FAILED", "视频结果过大，未能保存。", 500, task.task_id)
        suffix = ".webm" if "webm" in content_type else ".mp4"
        return [
            ProviderAsset(
                media_type="video",
                mime_type=content_type,
                data=response.content,
                filename=f"{task.task_id}{suffix}",
            )
        ]

