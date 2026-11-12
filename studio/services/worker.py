from __future__ import annotations

import logging
import threading
import time

from studio.config import Settings
from studio.database import claim_next, connect, get_task, list_assets, update_task
from studio.errors import StudioError
from studio.providers.base import Provider, task_input_from_task
from studio.services.asset_service import store_outputs

logger = logging.getLogger("studio.worker")
TRANSIENT = {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"}


def worker_loop(settings: Settings, provider: Provider, stop: threading.Event) -> None:
    while not stop.is_set():
        with connect(settings) as conn:
            task = claim_next(conn, provider.provider_id, time.time())
        if task is None:
            stop.wait(settings.idle_interval)
            continue
        try:
            execute_task(settings, provider, task.id, stop)
        except Exception:
            logger.exception("worker crashed task=%s", task.id)
            _fail(settings, task.id, "INTERNAL_ERROR", "生成任务时发生内部错误。")


def execute_task(settings: Settings, provider: Provider, task_id: str, stop: threading.Event) -> None:
    task = _reload(settings, task_id)
    if task is None or task.state in {"succeeded", "failed"}:
        return
    if not task.provider_task_id:
        try:
            submission = provider.submit(task_input_from_task(task))
        except StudioError as exc:
            _fail(settings, task_id, exc.code, exc.message)
            return
        if not submission.provider_task_id:
            _fail(settings, task_id, "PROVIDER_REJECTED", "上游没有返回任务编号。")
            return
        with connect(settings) as conn:
            update_task(
                conn,
                task_id,
                state="running",
                provider_task_id=submission.provider_task_id,
                provider_status=submission.provider_status,
            )
        task = _reload(settings, task_id)
        if task is None:
            return
    timeout = settings.image_task_timeout if provider.provider_id == "comfyui_h3_image" else settings.video_task_timeout
    interval = settings.image_poll_interval if provider.provider_id == "comfyui_h3_image" else settings.video_poll_interval
    elapsed = max(0.0, time.time() - task.started) if task.started else 0.0
    deadline = time.monotonic() + max(0.0, timeout - elapsed)
    first_poll = True
    while not stop.is_set():
        if not first_poll and time.monotonic() > deadline:
            _fail(settings, task_id, "PROVIDER_TIMEOUT", "任务等待超时，未自动重新提交。")
            return
        first_poll = False
        try:
            status = provider.poll(task.provider_task_id or "")
        except StudioError as exc:
            if exc.code not in TRANSIENT:
                _fail(settings, task_id, exc.code, exc.message)
                return
            logger.info("provider poll will retry task=%s code=%s", task_id, exc.code)
            stop.wait(interval)
            continue
        with connect(settings) as conn:
            current = get_task(conn, task_id)
            if current is None or current.state in {"succeeded", "failed"}:
                return
            update_task(conn, task_id, provider_status=status.provider_status)
        if status.normalized == "succeeded":
            _complete(settings, provider, task_id)
            return
        if status.normalized == "failed":
            _fail(
                settings,
                task_id,
                status.error_code or "PROVIDER_REJECTED",
                status.error_message or "生成失败。",
            )
            return
        if status.normalized not in {"queued", "running"}:
            _fail(settings, task_id, "INTERNAL_ERROR", "上游返回了无法识别的状态，未记为成功。")
            return
        stop.wait(interval)


def _complete(settings: Settings, provider: Provider, task_id: str) -> None:
    task = _reload(settings, task_id)
    if task is None:
        return
    with connect(settings) as conn:
        existing = list_assets(conn, task_id)
    video_path = task.video
    if not existing:
        try:
            fetched = provider.fetch_assets(task.provider_task_id or "", task_input_from_task(task))
            _stored, video_path = store_outputs(settings, task_id, fetched)
        except StudioError as exc:
            _fail(settings, task_id, exc.code, exc.message)
            return
    elif video_path is None:
        for asset in existing:
            if asset.media_type == "video":
                video_path = str(settings.output_dir / asset.storage_key)
                break
    with connect(settings) as conn:
        current = get_task(conn, task_id)
        if current is None or current.state == "succeeded":
            return
        values = {
            "state": "succeeded",
            "finished": time.time(),
            "error": None,
            "error_code": None,
        }
        if video_path:
            values["video"] = video_path
        update_task(conn, task_id, **values)


def _reload(settings: Settings, task_id: str):
    with connect(settings) as conn:
        return get_task(conn, task_id)


def _fail(settings: Settings, task_id: str, code: str, message: str) -> None:
    with connect(settings) as conn:
        current = get_task(conn, task_id)
        if current is None or current.state in {"succeeded", "failed"}:
            return
        update_task(
            conn,
            task_id,
            state="failed",
            finished=time.time(),
            error_code=code,
            error=message[:500],
        )
    logger.info("task failed id=%s code=%s", task_id, code)
