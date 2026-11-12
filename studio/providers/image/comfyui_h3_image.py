from __future__ import annotations

import io
import logging
import secrets
from urllib.parse import quote, urlencode

from PIL import Image, UnidentifiedImageError

from studio.config import Settings
from studio.errors import StudioError
from studio.http_client import Transport
from studio.providers.base import ProviderAsset, ProviderSubmission, ProviderTaskStatus, TaskInput
from studio.workflows_image import RESOLUTIONS, load_workflow, prepare_workflow

logger = logging.getLogger("studio.image")
IMAGE_MODEL = "minimax-h3-image"
MAX_IMAGE_BYTES = 40 * 1024 * 1024


def extract_images(record: dict) -> list[dict]:
    outputs = record.get("outputs") if isinstance(record, dict) else None
    if not isinstance(outputs, dict):
        return []
    images = []
    for node_output in outputs.values():
        if not isinstance(node_output, dict) or not isinstance(node_output.get("images"), list):
            continue
        for item in node_output["images"]:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename")
            subfolder = item.get("subfolder") or ""
            image_type = item.get("type") or "output"
            if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or ".." in filename:
                continue
            if not isinstance(subfolder, str) or ".." in subfolder.replace("\\", "/").split("/"):
                continue
            if image_type not in {"output", "temp"}:
                continue
            images.append({"filename": filename, "subfolder": subfolder, "type": image_type})
    return images


def interpret_history(provider_task_id: str, body: dict) -> tuple[ProviderTaskStatus, dict | None]:
    if not isinstance(body, dict) or provider_task_id not in body:
        return ProviderTaskStatus("pending", "running"), None
    record = body.get(provider_task_id)
    if not isinstance(record, dict):
        return ProviderTaskStatus("pending", "running"), None
    status = record.get("status") if isinstance(record.get("status"), dict) else {}
    status_str = status.get("status_str")
    if not isinstance(status_str, str) or not status_str:
        return ProviderTaskStatus("pending", "running"), record
    if status_str == "success":
        if not extract_images(record):
            return (
                ProviderTaskStatus("success", "failed", "RESULT_NOT_FOUND", "图片任务已结束，但没有输出文件。"),
                record,
            )
        return ProviderTaskStatus("success", "succeeded"), record
    if status_str == "error":
        return ProviderTaskStatus("error", "failed", "PROVIDER_REJECTED", "图片生成失败。"), record
    if status.get("completed") is True:
        return (
            ProviderTaskStatus(status_str, "failed", "PROVIDER_REJECTED", "图片服务返回了无法识别的完成状态。"),
            record,
        )
    return ProviderTaskStatus(status_str, "running"), record


class ComfyUIH3ImageProvider:
    provider_id = "comfyui_h3_image"
    supported_media_types = ("image",)
    supported_modes = ("t2i",)
    model = IMAGE_MODEL

    def __init__(self, settings: Settings, transport: Transport, seed_fn=None):
        self.settings = settings
        self.transport = transport
        self.seed_fn = seed_fn or (lambda: secrets.randbits(63))

    def _workflow_for(self, resolution: str) -> tuple:
        megapixels = RESOLUTIONS.get(resolution)
        if megapixels is None:
            raise StudioError("INPUT_INVALID", "图片分辨率仅支持 1024x1024 和 2048x2048。", 400)
        path = self.settings.image_workflow_1mp if resolution == "1024x1024" else self.settings.image_workflow_4mp
        return path, megapixels

    def workflow_flags(self) -> dict[str, bool]:
        flags = {}
        for key, resolution in (("t2i_1mp", "1024x1024"), ("t2i_4mp", "2048x2048")):
            path, megapixels = self._workflow_for(resolution)
            try:
                load_workflow(path, megapixels)
                flags[key] = True
            except Exception:
                logger.info("image workflow not ready key=%s", key)
                flags[key] = False
        return flags

    def health(self) -> dict:
        workflows = self.workflow_flags()
        try:
            response = self.transport.request(
                "GET",
                f"{self.settings.image_base_url}/system_stats",
                timeout=(3, 5),
            )
        except (TimeoutError, ConnectionError):
            return {"provider": self.provider_id, "status": "unreachable", "workflows": workflows}
        if response.status_code != 200:
            return {"provider": self.provider_id, "status": "degraded", "workflows": workflows}
        status = "ok" if all(workflows.values()) else "degraded"
        return {"provider": self.provider_id, "status": status, "workflows": workflows}

    def submit(self, task: TaskInput) -> ProviderSubmission:
        resolution = str((task.params or {}).get("resolution") or "")
        path, megapixels = self._workflow_for(resolution)
        try:
            workflow = prepare_workflow(path, megapixels, task.prompt, task.task_id, self.seed_fn())
        except FileNotFoundError as exc:
            raise StudioError("WORKFLOW_NOT_READY", "图片生成工作流尚未配置，请检查服务端工作流文件。", 503, task.task_id) from exc
        except ValueError as exc:
            logger.info("image workflow invalid")
            raise StudioError("WORKFLOW_INVALID", "图片生成工作流无效，已停止提交。", 503, task.task_id) from exc
        try:
            response = self.transport.request(
                "POST",
                f"{self.settings.image_base_url}/prompt",
                json_body={"prompt": workflow, "client_id": task.task_id},
                timeout=(5, self.settings.image_timeout),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "图片服务提交超时，未自动重试。", 504, task.task_id) from exc
        except ConnectionError as exc:
            raise StudioError("PROVIDER_UNAVAILABLE", "图片服务不可达，未自动重试。", 503, task.task_id) from exc
        if response.status_code >= 400:
            logger.info("image submit rejected status=%s", response.status_code)
            raise StudioError("PROVIDER_REJECTED", "图片服务拒绝了这次任务。", 502, task.task_id)
        try:
            body = response.json()
        except Exception as exc:
            raise StudioError("PROVIDER_REJECTED", "图片服务返回了无法解析的提交结果。", 502, task.task_id) from exc
        prompt_id = body.get("prompt_id") if isinstance(body, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id or "/" in prompt_id:
            raise StudioError("PROVIDER_REJECTED", "图片服务没有返回任务编号。", 502, task.task_id)
        return ProviderSubmission(prompt_id, "queued")

    def _history(self, provider_task_id: str) -> tuple[ProviderTaskStatus, dict | None]:
        if not provider_task_id or "/" in provider_task_id or ".." in provider_task_id:
            raise StudioError("PROVIDER_REJECTED", "图片任务编号无效。", 502)
        try:
            response = self.transport.request(
                "GET",
                f"{self.settings.image_base_url}/history/{quote(provider_task_id)}",
                timeout=(5, self.settings.image_timeout),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "查询图片任务超时。", 504) from exc
        except ConnectionError as exc:
            raise StudioError("PROVIDER_UNAVAILABLE", "图片服务不可达。", 503) from exc
        if response.status_code == 404:
            return ProviderTaskStatus("pending", "running"), None
        if response.status_code >= 400:
            logger.info("image history rejected status=%s", response.status_code)
            raise StudioError("PROVIDER_UNAVAILABLE", "查询图片任务失败。", 503)
        try:
            body = response.json()
        except Exception as exc:
            raise StudioError("PROVIDER_REJECTED", "图片任务状态无法解析。", 502) from exc
        return interpret_history(provider_task_id, body)

    def poll(self, provider_task_id: str) -> ProviderTaskStatus:
        status, _record = self._history(provider_task_id)
        return status

    def fetch_assets(self, provider_task_id: str, task: TaskInput) -> list[ProviderAsset]:
        status, record = self._history(provider_task_id)
        if status.normalized != "succeeded" or record is None:
            raise StudioError(
                status.error_code or "RESULT_NOT_FOUND",
                status.error_message or "图片结果还不存在。",
                502,
                task.task_id,
            )
        images = extract_images(record)
        if not images:
            raise StudioError("RESULT_NOT_FOUND", "图片任务已结束，但没有输出文件。", 502, task.task_id)
        assets = []
        for index, image in enumerate(images, start=1):
            query = urlencode(
                {"filename": image["filename"], "subfolder": image["subfolder"], "type": image["type"]}
            )
            try:
                response = self.transport.request(
                    "GET",
                    f"{self.settings.image_base_url}/view?{query}",
                    timeout=(5, self.settings.image_timeout),
                )
            except TimeoutError as exc:
                raise StudioError("PROVIDER_TIMEOUT", "下载图片超时。", 504, task.task_id) from exc
            except ConnectionError as exc:
                raise StudioError("ASSET_STORAGE_FAILED", "下载图片失败。", 502, task.task_id) from exc
            if response.status_code >= 400 or not response.content:
                logger.info("image view rejected status=%s", response.status_code)
                raise StudioError("RESULT_NOT_FOUND", "图片结果无法下载。", 502, task.task_id)
            if len(response.content) > MAX_IMAGE_BYTES:
                raise StudioError("ASSET_STORAGE_FAILED", "图片结果过大，未能保存。", 500, task.task_id)
            mime = _validated_image_mime(response.content)
            suffix = { "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp" }[mime]
            filename = f"{task.task_id}{suffix}" if len(images) == 1 else f"{task.task_id}_{index}{suffix}"
            assets.append(ProviderAsset("image", mime, response.content, filename))
        return assets


def _validated_image_mime(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = (image.format or "").upper()
    except (UnidentifiedImageError, OSError) as exc:
        raise StudioError("RESULT_NOT_FOUND", "图片服务返回的内容不是有效图片。", 502) from exc
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(fmt)
    if mime is None:
        raise StudioError("RESULT_NOT_FOUND", "图片服务返回了不支持的图片格式。", 502)
    return mime
