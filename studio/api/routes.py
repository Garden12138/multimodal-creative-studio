from __future__ import annotations

from pathlib import Path

from fastapi import File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from studio.errors import StudioError
from studio.validation import parse_image_request, parse_video_request

INDEX_PATH = Path(__file__).resolve().parents[2] / "index.html"


def register_routes(app) -> None:
    @app.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_PATH.read_text(encoding="utf-8")

    @app.get("/api/health")
    def health(request: Request):
        report = request.app.state.client.health()
        return {"web": "ok", **report}

    @app.post("/api/generate/image", status_code=202)
    async def generate_image(request: Request):
        if "application/json" not in request.headers.get("content-type", "").lower():
            raise StudioError("UNSUPPORTED_MEDIA", "图片请求必须是 JSON。", 415)
        try:
            body = await request.json()
        except Exception as exc:
            raise StudioError("BAD_JSON", "请求 JSON 无法解析。", 400) from exc
        prompt, size = parse_image_request(body)
        created = request.app.state.client.create_image(prompt, size)
        return {
            "task_id": created["task_id"],
            "media_type": "image",
            "mode": "t2i",
            "status": created["status"],
            "size": size,
        }

    @app.post("/api/generate/video")
    async def generate_video(
        request: Request,
        prompt: str = Form(""),
        mode: str = Form(""),
        first_frame: UploadFile | None = File(None),
        last_frame: UploadFile | None = File(None),
    ):
        settings = request.app.state.settings
        parsed_mode, text, first, last = parse_video_request(
            mode,
            prompt,
            await _read_upload(first_frame, settings.max_upload_bytes),
            await _read_upload(last_frame, settings.max_upload_bytes),
            settings.max_upload_bytes,
        )
        created = request.app.state.client.create_video(parsed_mode, text, first, last)
        return {
            "task_id": created["task_id"],
            "media_type": "video",
            "mode": parsed_mode,
            "status": created["status"],
            "duration": 5,
        }

    @app.get("/api/tasks/{media_type}/{task_id}")
    def get_task(media_type: str, task_id: str, request: Request):
        _known_media(media_type)
        return JSONResponse(
            request.app.state.client.get_task(media_type, task_id).public(),
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/api/tasks/{media_type}/{task_id}/file")
    def task_file(media_type: str, task_id: str, request: Request, download: str | None = Query(None)):
        _known_media(media_type)
        mime, content, filename = request.app.state.client.read_file(media_type, task_id)
        disposition = "attachment" if download == "1" else "inline"
        return Response(
            content=content,
            media_type=mime,
            headers={
                "Content-Disposition": f'{disposition}; filename="{filename}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


def install_exception_handlers(app) -> None:
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(StudioError)
    async def studio_error(_request: Request, exc: StudioError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, "task_id": exc.task_id}},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INPUT_INVALID", "message": "请求参数无效", "task_id": None}},
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "请求无法处理。"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "HTTP_ERROR", "message": detail, "task_id": None}},
        )

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        if isinstance(exc, (HTTPException, RequestValidationError, StudioError)):
            raise exc
        _log(exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "服务内部错误。", "task_id": None}},
        )


async def _read_upload(upload: UploadFile | None, limit: int) -> tuple[str, bytes] | None:
    if upload is None:
        return None
    data = await upload.read(limit + 1)
    if not data and not (upload.filename or "").strip():
        return None
    return upload.content_type or "", data


def _known_media(media_type: str) -> None:
    if media_type not in {"image", "video"}:
        raise StudioError("NOT_FOUND", "任务不存在。", 404)


def _log(exc: Exception) -> None:
    import logging

    logging.getLogger("studio").exception("unhandled error: %s", exc.__class__.__name__)
