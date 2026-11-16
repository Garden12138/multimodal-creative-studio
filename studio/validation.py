from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from studio.errors import StudioError

IMAGE_SIZES = {"1024x1024", "2048x2048"}
VIDEO_MODES = {"t2v", "i2v", "flf"}
MIME_BY_FORMAT = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
ROLE_LABEL = {"reference": "参考图片", "first": "首帧图片", "last": "末帧图片"}


def parse_image_request(body: object) -> tuple[str, str]:
    if not isinstance(body, dict):
        raise StudioError("BAD_JSON", "请求 JSON 必须是对象。", 400)
    unknown = set(body) - {"prompt", "size"}
    if unknown:
        raise StudioError("INPUT_INVALID", "请求包含不支持的字段。", 422)
    return _prompt(body.get("prompt"), 4000), _image_size(body.get("size"))


def parse_video_request(
    mode: str,
    prompt: str,
    first: tuple[str, bytes] | None,
    last: tuple[str, bytes] | None,
    limit: int,
) -> tuple[str, str, tuple[str, bytes] | None, tuple[str, bytes] | None]:
    if mode not in VIDEO_MODES:
        raise StudioError("INPUT_INVALID", "视频模式仅支持文生、图生和首末帧。", 422)
    text = _prompt(prompt, 2000)
    if mode == "t2v":
        if first or last:
            raise StudioError("INPUT_INVALID", "文生视频不接收参考图。", 422)
        return mode, text, None, None
    if mode == "i2v":
        if last:
            raise StudioError("INPUT_INVALID", "图生视频只接收一张参考图。", 422)
        if not first:
            raise StudioError("INPUT_INVALID", "请先上传参考图片。", 422)
        return mode, text, inspect_image(first[0], first[1], "reference", limit), None
    if not first:
        raise StudioError("INPUT_INVALID", "请上传首帧图片。", 422)
    if not last:
        raise StudioError("INPUT_INVALID", "请上传末帧图片。", 422)
    return (
        mode,
        text,
        inspect_image(first[0], first[1], "first", limit),
        inspect_image(last[0], last[1], "last", limit),
    )


def inspect_image(content_type: str, raw: bytes, role: str, limit: int) -> tuple[str, bytes]:
    label = ROLE_LABEL[role]
    if not raw:
        raise StudioError("INPUT_INVALID", f"{label}是空的。", 422)
    if len(raw) > limit:
        raise StudioError("INPUT_INVALID", f"{label}不能超过 12MB。", 422)
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared == "image/jpg":
        declared = "image/jpeg"
    if declared and declared not in set(MIME_BY_FORMAT.values()):
        raise StudioError("INPUT_INVALID", f"{label}只能是 PNG、JPG 或 WebP。", 422)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            mime = MIME_BY_FORMAT.get(image.format or "")
            width, height = image.size
            if mime is None:
                raise StudioError("INPUT_INVALID", f"{label}只能是 PNG、JPG 或 WebP。", 422)
            if width < 256 or height < 256 or width > 5760 or height > 5760 or not 0.4 <= (width / height) <= 2.5:
                raise StudioError("INPUT_INVALID", f"{label}的边长或宽高比超出支持范围。", 422)
    except StudioError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise StudioError("INPUT_INVALID", f"{label}无法识别。", 422) from exc
    if declared and declared != mime:
        raise StudioError("INPUT_INVALID", f"{label}的格式和文件内容不一致。", 422)
    return mime, raw


def _prompt(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StudioError("INPUT_INVALID", "提示词不能为空。", 422)
    text = value.strip()
    if len(text) > limit:
        raise StudioError("INPUT_INVALID", f"提示词长度必须在 1 到 {limit} 个字符之间。", 422)
    return text


def _image_size(value: object) -> str:
    if value not in IMAGE_SIZES:
        raise StudioError("INPUT_INVALID", "图片尺寸仅支持 1024x1024 和 2048x2048。", 422)
    return str(value)
