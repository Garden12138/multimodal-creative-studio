from __future__ import annotations

import hashlib
from pathlib import Path

from studio.errors import StudioError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_under(root: Path, stored: str) -> Path:
    base = root.resolve()
    raw = (stored or "").strip()
    if not raw or "\x00" in raw:
        raise StudioError("RESULT_NOT_FOUND", "文件不存在", 404)
    candidate = Path(raw)
    if any(part == ".." for part in candidate.parts):
        raise StudioError("RESULT_NOT_FOUND", "文件不存在", 404)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        raise StudioError("RESULT_NOT_FOUND", "文件不存在", 404)
    return resolved


def safe_download_name(name: str, fallback: str) -> str:
    cleaned = Path(name or "").name
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        return fallback
    return cleaned[:180]
