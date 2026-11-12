from __future__ import annotations

import io
import time
import uuid

from PIL import Image, UnidentifiedImageError

from studio.config import Settings
from studio.database import connect, get_asset, insert_asset, list_assets
from studio.errors import StudioError
from studio.models.asset import Asset
from studio.providers.base import ProviderAsset
from studio.storage import resolve_under, safe_download_name, sha256_bytes

ALLOWED_UPLOAD_TYPES = {"image/png", "image/jpeg", "image/webp"}


def save_reference_image(settings: Settings, task_id: str, role: str, content_type: str, data: bytes) -> str:
    if content_type not in ALLOWED_UPLOAD_TYPES:
        raise StudioError("INPUT_INVALID", "只支持 PNG、JPEG、WebP 图片", 400)
    if len(data) > settings.max_upload_bytes:
        raise StudioError("INPUT_INVALID", "单张图片不能超过 12MB", 400)
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            converted = image.convert("RGB")
            width, height = converted.size
            if width < 256 or height < 256 or width > 5760 or height > 5760:
                raise StudioError("INPUT_INVALID", "图片边长需在 256 到 5760 像素之间", 400)
            aspect = width / height
            if aspect < 0.4 or aspect > 2.5:
                raise StudioError("INPUT_INVALID", "图片宽高比需在 0.4 到 2.5 之间", 400)
            converted.thumbnail((2048, 2048))
            path = resolve_under(settings.upload_dir, f"{task_id}_{role}.png")
            converted.save(path, format="PNG")
            return str(path)
    except StudioError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise StudioError("INPUT_INVALID", "图片无效", 400) from exc


def write_output(settings: Settings, storage_key: str, data: bytes) -> None:
    path = resolve_under(settings.output_dir, storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(data)
    temporary.replace(path)


def store_outputs(settings: Settings, task_id: str, outputs: list[ProviderAsset]) -> tuple[list[Asset], str | None]:
    if not outputs:
        raise StudioError("RESULT_NOT_FOUND", "生成结果不存在。", 502, task_id)
    prepared: list[Asset] = []
    video_path = None
    for item in outputs:
        if item.media_type == "image":
            _require_image(item.data)
        elif item.media_type != "video" or not item.mime_type.startswith("video/") or not item.data:
            raise StudioError("ASSET_STORAGE_FAILED", "生成结果无法保存。", 500, task_id)
        filename = safe_download_name(item.filename, f"{task_id}.bin")
        storage_key = filename
        write_output(settings, storage_key, item.data)
        asset = Asset(
            id=uuid.uuid4().hex,
            task_id=task_id,
            media_type=item.media_type,
            filename=filename,
            mime_type=item.mime_type,
            size_bytes=len(item.data),
            sha256=sha256_bytes(item.data),
            storage_key=storage_key,
            created_at=time.time(),
        )
        prepared.append(asset)
        if item.media_type == "video" and video_path is None:
            video_path = str(resolve_under(settings.output_dir, storage_key))
    with connect(settings) as conn:
        existing = list_assets(conn, task_id)
        if existing:
            return existing, video_path
        for asset in prepared:
            insert_asset(conn, asset)
    return prepared, video_path


def _require_image(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise StudioError("ASSET_STORAGE_FAILED", "生成结果无法保存。", 500) from exc


def read_asset(settings: Settings, asset_id: str) -> tuple[Asset, object]:
    with connect(settings) as conn:
        asset = get_asset(conn, asset_id)
    if asset is None:
        raise StudioError("RESULT_NOT_FOUND", "文件不存在", 404)
    path = resolve_under(settings.output_dir, asset.storage_key)
    if not path.is_file():
        raise StudioError("RESULT_NOT_FOUND", "文件不存在", 404)
    return asset, path


def assets_for(settings: Settings, task_id: str) -> list[Asset]:
    with connect(settings) as conn:
        return list_assets(conn, task_id)
