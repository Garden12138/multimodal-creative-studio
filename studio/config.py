from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是整数") from exc
    if value <= 0:
        raise RuntimeError(f"环境变量 {name} 必须大于 0")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是数字") from exc
    if value <= 0:
        raise RuntimeError(f"环境变量 {name} 必须大于 0")
    return value


def inference_base_url(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.port is None:
        raise RuntimeError("INFERENCE_API_BASE_URL 必须是带端口的 http(s) 地址")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
        raise RuntimeError("INFERENCE_API_BASE_URL 不能包含用户信息、路径或查询")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    base_dir: Path
    inference_base_url: str
    inference_api_key: str
    inference_timeout: float
    file_timeout: float
    max_upload_bytes: int = 12 * 1024 * 1024

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Settings":
        base = (base_dir or Path(__file__).resolve().parent.parent).resolve()
        return cls(
            host=os.environ.get("STUDIO_HOST", "0.0.0.0"),
            port=_positive_int("STUDIO_PORT", 8088),
            base_dir=base,
            inference_base_url=inference_base_url(os.environ.get("INFERENCE_API_BASE_URL", "http://127.0.0.1:8090")),
            inference_api_key=os.environ.get("INFERENCE_API_KEY", "").strip(),
            inference_timeout=_positive_float("INFERENCE_API_TIMEOUT_SECONDS", 60),
            file_timeout=_positive_float("INFERENCE_FILE_TIMEOUT_SECONDS", 120),
            max_upload_bytes=_positive_int("STUDIO_MAX_UPLOAD_BYTES", 12 * 1024 * 1024),
        )
