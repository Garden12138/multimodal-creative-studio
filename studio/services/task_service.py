from __future__ import annotations

import time
import uuid

from studio.config import Settings
from studio.database import connect, get_task, insert_task, list_assets, list_tasks
from studio.errors import StudioError
from studio.models.asset import Asset
from studio.models.task import Task, to_iso
from studio.providers.image.comfyui_h3_image import IMAGE_MODEL
from studio.providers.registry import ProviderRegistry
from studio.providers.video.comfyui_h3_video import VIDEO_MODEL
from studio.workflows_video import prepare_video_workflow, workflow_file
from studio.services.asset_service import save_reference_image
from studio.workflows_image import RESOLUTIONS, load_workflow

RULES = {
    ("image", "t2i"): ("forbid", "forbid", "文生图片不能上传参考图", "文生图片不能上传参考图"),
    ("video", "t2v"): ("forbid", "forbid", "文生视频不能上传图片", "文生视频不能上传图片"),
    ("video", "i2v"): ("require", "forbid", "图生视频仅上传一张首帧图", "图生视频仅上传一张首帧图"),
    ("video", "flf"): ("require", "require", "首末帧模式需要两张图片", "首末帧模式需要两张图片"),
}


def create_task(
    settings: Settings,
    registry: ProviderRegistry,
    *,
    media_type: str,
    mode: str,
    prompt: str,
    resolution: str = "",
    first: tuple[str, bytes] | None = None,
    last: tuple[str, bytes] | None = None,
) -> Task:
    media_type = (media_type or "").strip()
    mode = (mode or "").strip()
    prompt = (prompt or "").strip()
    resolution = (resolution or "").strip()
    rule = RULES.get((media_type, mode))
    if rule is None:
        raise StudioError("INPUT_INVALID", "生成模式不支持", 400)
    if not 1 <= len(prompt) <= settings.prompt_max_length:
        raise StudioError("INPUT_INVALID", "提示词长度应为 1~2000 字", 400)
    first_flag, last_flag, forbid_message, require_message = rule
    _expect_frame(first_flag, first is not None, forbid_message, require_message)
    _expect_frame(last_flag, last is not None, forbid_message, require_message)
    provider = registry.for_media(media_type)
    if media_type == "image":
        if resolution not in RESOLUTIONS:
            raise StudioError("INPUT_INVALID", "图片分辨率仅支持 1024x1024 和 2048x2048。", 400)
        path = settings.image_workflow_1mp if resolution == "1024x1024" else settings.image_workflow_4mp
        _require_workflow(path, RESOLUTIONS[resolution])
        model = IMAGE_MODEL
        params = {"resolution": resolution}
    else:
        _require_video_workflow(settings, mode)
        model = VIDEO_MODEL
        params = {"duration_seconds": 5}
    task_id = uuid.uuid4().hex
    first_path = save_reference_image(settings, task_id, "first", first[0], first[1]) if first else None
    last_path = save_reference_image(settings, task_id, "last", last[0], last[1]) if last else None
    task = Task(
        id=task_id,
        media_type=media_type,
        provider=provider.provider_id,
        model=model,
        mode=mode,
        prompt=prompt,
        params=params,
        state="queued",
        provider_task_id=None,
        provider_status=None,
        created=time.time(),
        started=None,
        finished=None,
        error_code=None,
        error=None,
        comfy_prompt_id=None,
        first_image=first_path,
        last_image=last_path,
        video=None,
    )
    with connect(settings) as conn:
        insert_task(conn, task)
    return task


def _expect_frame(flag: str, provided: bool, forbid_message: str, require_message: str) -> None:
    if flag == "forbid" and provided:
        raise StudioError("INPUT_INVALID", forbid_message, 400)
    if flag == "require" and not provided:
        raise StudioError("INPUT_INVALID", require_message, 400)


def _require_video_workflow(settings: Settings, mode: str) -> None:
    first = "first.png" if mode in {"i2v", "flf"} else None
    last = "last.png" if mode == "flf" else None
    try:
        prepare_video_workflow(workflow_file(settings.workflow_dir, mode), mode, "prompt", "check", first, last)
    except FileNotFoundError as exc:
        raise StudioError("WORKFLOW_NOT_READY", "视频生成工作流尚未配置，请检查服务端工作流文件。", 503) from exc
    except ValueError as exc:
        raise StudioError("WORKFLOW_INVALID", "视频生成工作流无效，已停止提交。", 503) from exc


def _require_workflow(path, megapixels: float) -> None:
    try:
        load_workflow(path, megapixels)
    except FileNotFoundError as exc:
        raise StudioError("WORKFLOW_NOT_READY", "图片生成工作流尚未配置，请检查服务端工作流文件。", 503) from exc
    except ValueError as exc:
        raise StudioError("WORKFLOW_INVALID", "图片生成工作流无效，已停止提交。", 503) from exc


def get_task_record(settings: Settings, task_id: str) -> tuple[Task, list[Asset]]:
    with connect(settings) as conn:
        task = get_task(conn, task_id)
        if task is None:
            raise StudioError("RESULT_NOT_FOUND", "任务不存在", 404)
        return task, list_assets(conn, task_id)


def list_task_records(settings: Settings, *, media_type: str | None, limit: int) -> list[tuple[Task, list[Asset]]]:
    with connect(settings) as conn:
        tasks = list_tasks(conn, media_type=media_type, limit=limit)
        return [(task, list_assets(conn, task.id)) for task in tasks]


def task_v1(task: Task, assets: list[Asset]) -> dict:
    return {
        "task_id": task.id,
        "media_type": task.media_type,
        "provider": task.provider,
        "model": task.model,
        "mode": task.mode,
        "prompt": task.prompt,
        "params": task.params,
        "status": task.state,
        "created_at": to_iso(task.created),
        "started_at": to_iso(task.started),
        "finished_at": to_iso(task.finished),
        "error": task.error,
        "assets": [
            {
                "asset_id": asset.id,
                "media_type": asset.media_type,
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes,
                "url": f"/api/v1/assets/{asset.id}/content",
            }
            for asset in assets
        ],
    }


def task_legacy(task: Task) -> dict:
    return {
        "id": task.id,
        "mode": task.mode,
        "prompt": task.prompt,
        "state": task.state,
        "created": task.created,
        "started": task.started,
        "finished": task.finished,
        "error": task.error,
    }
