from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote, urlencode

from studio.config import Settings
from studio.errors import StudioError
from studio.http_client import Transport
from studio.providers.base import ProviderAsset, ProviderSubmission, ProviderTaskStatus, TaskInput
from studio.storage import resolve_under
from studio.workflows_video import prepare_video_workflow, workflow_file

logger = logging.getLogger("studio.video")
VIDEO_MODEL = "MiniMax-H3"
VIDEO_SUFFIXES = {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska"}
MAX_VIDEO_BYTES = 200 * 1024 * 1024


def extract_videos(record: dict) -> list[dict]:
    outputs = record.get("outputs") if isinstance(record, dict) else None
    if not isinstance(outputs, dict):
        return []
    videos = []
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for key in ("videos", "gifs", "images"):
            items = node_output.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                subfolder = item.get("subfolder") or ""
                file_type = item.get("type") or "output"
                if not isinstance(filename, str) or Path(filename).suffix.lower() not in VIDEO_SUFFIXES:
                    continue
                if not _safe_file_part(filename) or not isinstance(subfolder, str):
                    continue
                if any(part == ".." for part in Path(subfolder).parts):
                    continue
                if file_type not in {"output", "temp"}:
                    continue
                videos.append({"filename": filename, "subfolder": subfolder, "type": file_type})
    return videos


def interpret_video_history(provider_task_id: str, body: dict) -> tuple[ProviderTaskStatus, dict | None]:
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
        if not extract_videos(record):
            return (
                ProviderTaskStatus("success", "failed", "RESULT_NOT_FOUND", "视频任务已结束，但没有输出文件。"),
                record,
            )
        return ProviderTaskStatus("success", "succeeded"), record
    if status_str == "error":
        return ProviderTaskStatus("error", "failed", "PROVIDER_REJECTED", "视频生成失败。"), record
    if status.get("completed") is True:
        return (
            ProviderTaskStatus(status_str, "failed", "PROVIDER_REJECTED", "视频服务返回了无法识别的完成状态。"),
            record,
        )
    return ProviderTaskStatus(status_str, "running"), record


def _safe_file_part(filename: str) -> bool:
    return bool(filename) and "/" not in filename and "\\" not in filename and ".." not in filename


def _video_mime(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    mime = VIDEO_SUFFIXES.get(suffix)
    if mime == "video/mp4" and b"ftyp" in data[:64]:
        return mime
    if mime in {"video/webm", "video/x-matroska"} and data.startswith(b"\x1a\x45\xdf\xa3"):
        return mime
    raise StudioError("RESULT_NOT_FOUND", "视频服务返回的内容不是有效视频。", 502)


class ComfyUIH3VideoProvider:
    provider_id = "comfyui_h3_video"
    supported_media_types = ("video",)
    supported_modes = ("t2v", "i2v", "flf")
    model = VIDEO_MODEL

    def __init__(self, settings: Settings, transport: Transport):
        self.settings = settings
        self.transport = transport

    def workflow_flags(self) -> dict[str, bool]:
        flags = {}
        for mode in self.supported_modes:
            try:
                first = "first.png" if mode in {"i2v", "flf"} else None
                last = "last.png" if mode == "flf" else None
                prepare_video_workflow(workflow_file(self.settings.workflow_dir, mode), mode, "prompt", "check", first, last)
                flags[mode] = True
            except Exception:
                logger.info("video workflow not ready mode=%s", mode)
                flags[mode] = False
        return flags

    def health(self) -> dict:
        workflows = self.workflow_flags()
        try:
            response = self.transport.request(
                "GET",
                f"{self.settings.video_comfy_base_url}/system_stats",
                timeout=(3, 5),
            )
        except (TimeoutError, ConnectionError):
            return {"provider": self.provider_id, "status": "unreachable", "workflows": workflows}
        if response.status_code != 200:
            return {"provider": self.provider_id, "status": "degraded", "workflows": workflows}
        status = "ok" if all(workflows.values()) else "degraded"
        return {"provider": self.provider_id, "status": status, "workflows": workflows}

    def _upload(self, stored: str | None, task_id: str) -> str:
        if not stored:
            raise StudioError("INPUT_INVALID", "缺少参考图片。", 400, task_id)
        path = resolve_under(self.settings.upload_dir, stored)
        if not path.is_file():
            raise StudioError("INPUT_INVALID", "参考图片不存在。", 400, task_id)
        try:
            response = self.transport.request(
                "POST",
                f"{self.settings.video_comfy_base_url}/upload/image",
                data={"overwrite": "true"},
                files={"image": (path.name, path.read_bytes(), "image/png")},
                timeout=(5, self.settings.video_timeout),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "上传参考图超时，未自动重试。", 504, task_id) from exc
        except ConnectionError as exc:
            raise StudioError("PROVIDER_UNAVAILABLE", "视频服务不可达，未自动重试。", 503, task_id) from exc
        if response.status_code >= 400:
            logger.info("video upload rejected status=%s", response.status_code)
            raise StudioError("PROVIDER_REJECTED", "视频服务拒绝了参考图。", 502, task_id)
        try:
            body = response.json()
        except Exception as exc:
            raise StudioError("PROVIDER_REJECTED", "视频服务没有返回参考图名称。", 502, task_id) from exc
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(name, str) or not _safe_file_part(name):
            raise StudioError("PROVIDER_REJECTED", "视频服务没有返回参考图名称。", 502, task_id)
        return name

    def submit(self, task: TaskInput) -> ProviderSubmission:
        first_name = self._upload(task.first_image, task.task_id) if task.mode in {"i2v", "flf"} else None
        last_name = self._upload(task.last_image, task.task_id) if task.mode == "flf" else None
        path = workflow_file(self.settings.workflow_dir, task.mode)
        try:
            workflow = prepare_video_workflow(path, task.mode, task.prompt, task.task_id, first_name, last_name)
        except FileNotFoundError as exc:
            raise StudioError("WORKFLOW_NOT_READY", "视频生成工作流尚未配置，请检查服务端工作流文件。", 503, task.task_id) from exc
        except ValueError as exc:
            logger.info("video workflow invalid")
            raise StudioError("WORKFLOW_INVALID", "视频生成工作流无效，已停止提交。", 503, task.task_id) from exc
        try:
            response = self.transport.request(
                "POST",
                f"{self.settings.video_comfy_base_url}/prompt",
                json_body={"prompt": workflow, "client_id": task.task_id},
                timeout=(5, self.settings.video_timeout),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "视频服务提交超时，未自动重试。", 504, task.task_id) from exc
        except ConnectionError as exc:
            raise StudioError("PROVIDER_UNAVAILABLE", "视频服务不可达，未自动重试。", 503, task.task_id) from exc
        if response.status_code >= 400:
            logger.info("video submit rejected status=%s", response.status_code)
            raise StudioError("PROVIDER_REJECTED", "视频服务拒绝了这次任务。", 502, task.task_id)
        try:
            body = response.json()
        except Exception as exc:
            raise StudioError("PROVIDER_REJECTED", "视频服务返回了无法解析的提交结果。", 502, task.task_id) from exc
        prompt_id = body.get("prompt_id") if isinstance(body, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id or "/" in prompt_id or ".." in prompt_id:
            raise StudioError("PROVIDER_REJECTED", "视频服务没有返回任务编号。", 502, task.task_id)
        return ProviderSubmission(prompt_id, "queued")

    def _history(self, provider_task_id: str) -> tuple[ProviderTaskStatus, dict | None]:
        if not provider_task_id or "/" in provider_task_id or ".." in provider_task_id:
            raise StudioError("PROVIDER_REJECTED", "视频任务编号无效。", 502)
        try:
            response = self.transport.request(
                "GET",
                f"{self.settings.video_comfy_base_url}/history/{quote(provider_task_id)}",
                timeout=(5, self.settings.video_timeout),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "查询视频任务超时。", 504) from exc
        except ConnectionError as exc:
            raise StudioError("PROVIDER_UNAVAILABLE", "视频服务不可达。", 503) from exc
        if response.status_code == 404:
            return ProviderTaskStatus("pending", "running"), None
        if response.status_code >= 400:
            logger.info("video history rejected status=%s", response.status_code)
            raise StudioError("PROVIDER_UNAVAILABLE", "查询视频任务失败。", 503)
        try:
            body = response.json()
        except Exception as exc:
            raise StudioError("PROVIDER_REJECTED", "视频任务状态无法解析。", 502) from exc
        return interpret_video_history(provider_task_id, body)

    def poll(self, provider_task_id: str) -> ProviderTaskStatus:
        status, _record = self._history(provider_task_id)
        return status

    def fetch_assets(self, provider_task_id: str, task: TaskInput) -> list[ProviderAsset]:
        status, record = self._history(provider_task_id)
        if status.normalized != "succeeded" or record is None:
            raise StudioError(
                status.error_code or "RESULT_NOT_FOUND",
                status.error_message or "视频结果还不存在。",
                502,
                task.task_id,
            )
        videos = extract_videos(record)
        if not videos:
            raise StudioError("RESULT_NOT_FOUND", "视频任务已结束，但没有输出文件。", 502, task.task_id)
        chosen = videos[0]
        query = urlencode(
            {"filename": chosen["filename"], "subfolder": chosen["subfolder"], "type": chosen["type"]}
        )
        try:
            response = self.transport.request(
                "GET",
                f"{self.settings.video_comfy_base_url}/view?{query}",
                timeout=(5, self.settings.video_timeout),
            )
        except TimeoutError as exc:
            raise StudioError("PROVIDER_TIMEOUT", "下载视频超时。", 504, task.task_id) from exc
        except ConnectionError as exc:
            raise StudioError("ASSET_STORAGE_FAILED", "下载视频失败。", 502, task.task_id) from exc
        if response.status_code >= 400 or not response.content:
            logger.info("video view rejected status=%s", response.status_code)
            raise StudioError("RESULT_NOT_FOUND", "视频结果无法下载。", 502, task.task_id)
        if len(response.content) > MAX_VIDEO_BYTES:
            raise StudioError("ASSET_STORAGE_FAILED", "视频结果过大，未能保存。", 500, task.task_id)
        mime = _video_mime(response.content, chosen["filename"])
        suffix = Path(chosen["filename"]).suffix.lower()
        return [ProviderAsset("video", mime, response.content, f"{task.task_id}{suffix}")]
