from __future__ import annotations

from fastapi import File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from studio.database import connect
from studio.errors import StudioError
from studio.services.asset_service import read_asset
from studio.services.task_service import (
    create_task,
    get_task_record,
    list_task_records,
    task_legacy,
    task_v1,
)


def register_routes(app, ctx) -> None:
    settings = ctx.settings
    registry = ctx.registry

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (settings.base_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health():
        database = "ok"
        try:
            with connect(settings) as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception:
            database = "unreachable"
        providers = {}
        try:
            providers["video"] = registry.get(settings.video_provider).health()
        except Exception:
            providers["video"] = {"provider": settings.video_provider, "status": "degraded"}
        try:
            providers["image"] = registry.get(settings.image_provider).health()
        except Exception:
            providers["image"] = {
                "provider": settings.image_provider,
                "status": "degraded",
                "workflows": {"t2i_1mp": False, "t2i_4mp": False},
            }
        return {"web": "ok", "database": database, "providers": providers}

    @app.post("/api/v1/tasks", status_code=202)
    async def create_v1_task(
        media_type: str = Form(""),
        mode: str = Form(""),
        prompt: str = Form(""),
        resolution: str = Form(""),
        first_frame: UploadFile | None = File(None),
        last_frame: UploadFile | None = File(None),
    ):
        task = create_task(
            settings,
            registry,
            media_type=media_type,
            mode=mode,
            prompt=prompt,
            resolution=resolution,
            first=await _optional_upload(first_frame, settings.max_upload_bytes),
            last=await _optional_upload(last_frame, settings.max_upload_bytes),
        )
        return {
            "task_id": task.id,
            "media_type": task.media_type,
            "mode": task.mode,
            "status": task.state,
            "created_at": task_v1(task, [])["created_at"],
        }

    @app.get("/api/v1/tasks")
    def list_v1_tasks(media_type: str | None = None, limit: int = 30):
        if media_type not in {None, "", "image", "video"}:
            raise StudioError("INPUT_INVALID", "生成模式不支持", 400)
        bounded = min(max(limit, 1), 100)
        records = list_task_records(settings, media_type=media_type or None, limit=bounded)
        return {"items": [task_v1(task, assets) for task, assets in records]}

    @app.get("/api/v1/tasks/{task_id}")
    def get_v1_task(task_id: str):
        task, assets = get_task_record(settings, task_id)
        return task_v1(task, assets)

    @app.get("/api/v1/assets/{asset_id}/content")
    def asset_content(asset_id: str, download: str | None = Query(None)):
        asset, path = read_asset(settings, asset_id)
        disposition = "attachment" if download == "1" else "inline"
        return FileResponse(
            path,
            media_type=asset.mime_type,
            filename=asset.filename,
            content_disposition_type=disposition,
        )

    @app.post("/api/tasks")
    async def create_legacy_task(
        mode: str = Form(""),
        prompt: str = Form(""),
        first_frame: UploadFile | None = File(None),
        last_frame: UploadFile | None = File(None),
    ):
        task = create_task(
            settings,
            registry,
            media_type="video",
            mode=mode,
            prompt=prompt,
            first=await _optional_upload(first_frame, settings.max_upload_bytes),
            last=await _optional_upload(last_frame, settings.max_upload_bytes),
        )
        return {"id": task.id, "state": task.state}

    @app.get("/api/tasks")
    def list_legacy_tasks():
        records = list_task_records(settings, media_type="video", limit=30)
        return [task_legacy(task) for task, _assets in records]

    @app.get("/api/tasks/{task_id}")
    def get_legacy_task(task_id: str):
        task, _assets = get_task_record(settings, task_id)
        return task_legacy(task)

    @app.get("/api/tasks/{task_id}/video")
    def legacy_video(task_id: str):
        task, assets = get_task_record(settings, task_id)
        if task.state != "succeeded":
            raise StudioError("RESULT_NOT_FOUND", "视频尚未生成", 404, task_id)
        video_assets = [asset for asset in assets if asset.media_type == "video"]
        if video_assets:
            _asset, path = read_asset(settings, video_assets[0].id)
            return FileResponse(
                path,
                media_type=video_assets[0].mime_type,
                filename=video_assets[0].filename,
                content_disposition_type="inline",
            )
        if not task.video:
            raise StudioError("RESULT_NOT_FOUND", "视频尚未生成", 404, task_id)
        from studio.storage import resolve_under

        path = resolve_under(settings.output_dir, task.video)
        if not path.is_file():
            raise StudioError("RESULT_NOT_FOUND", "视频文件不存在", 404, task_id)
        mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska"}.get(
            path.suffix.lower(), "application/octet-stream"
        )
        return FileResponse(path, media_type=mime, filename=path.name, content_disposition_type="inline")


async def _optional_upload(upload: UploadFile | None, limit: int) -> tuple[str, bytes] | None:
    if upload is None or not upload.filename:
        return None
    data = await upload.read(limit + 1)
    return upload.content_type or "", data


def install_exception_handlers(app) -> None:
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(StudioError)
    async def studio_error(_request: Request, exc: StudioError):
        if _request.url.path.startswith("/api/tasks"):
            return JSONResponse(status_code=exc.http_status, content={"detail": exc.message})
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, "task_id": exc.task_id}},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "请求参数无效"})

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        if isinstance(exc, (HTTPException, RequestValidationError, StudioError)):
            raise exc
        logging_exception(exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "服务内部错误。", "task_id": None}},
        )


def logging_exception(exc: Exception) -> None:
    import logging

    logging.getLogger("studio").exception("unhandled error")
