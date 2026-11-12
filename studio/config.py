from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    base_dir: Path
    database_path: Path
    upload_dir: Path
    output_dir: Path
    workflow_dir: Path
    video_provider: str
    video_base_url: str
    video_comfy_base_url: str
    video_api_key: str
    video_timeout: float
    video_poll_interval: float
    video_task_timeout: float
    image_provider: str
    image_base_url: str
    image_timeout: float
    image_poll_interval: float
    image_task_timeout: float
    image_workflow_1mp: Path
    image_workflow_4mp: Path
    idle_interval: float = 2.0
    max_upload_bytes: int = 12 * 1024 * 1024
    prompt_max_length: int = 2000

    @staticmethod
    def _path(base: Path, env_name: str, default: str) -> Path:
        raw = os.environ.get(env_name, default)
        path = Path(raw)
        if not path.is_absolute():
            path = base / path
        return path

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Settings":
        base = (base_dir or Path(__file__).resolve().parent.parent).resolve()
        requested_video = os.environ.get("VIDEO_PROVIDER", "comfyui_h3_video")
        image_provider = os.environ.get("IMAGE_PROVIDER", "comfyui_h3_image")
        if requested_video not in {"comfyui_h3_video", "minimax_h3_native"}:
            raise RuntimeError("视频任务只提交到本机视频 ComfyUI")
        if image_provider != "comfyui_h3_image":
            raise RuntimeError("V1 只允许 IMAGE_PROVIDER=comfyui_h3_image")
        # 8089 still calls back into Studio. Sending the page task there makes the
        # single video worker wait on itself, so execution always uses ComfyUI.
        return cls(
            host=os.environ.get("STUDIO_HOST", "0.0.0.0"),
            port=_positive_int("STUDIO_PORT", 8088),
            base_dir=base,
            database_path=cls._path(base, "STUDIO_DATABASE_PATH", "tasks.db"),
            upload_dir=cls._path(base, "STUDIO_UPLOAD_DIR", "uploads"),
            output_dir=cls._path(base, "STUDIO_OUTPUT_DIR", "outputs"),
            workflow_dir=base / "workflows",
            video_provider="comfyui_h3_video",
            video_base_url=os.environ.get("VIDEO_NATIVE_BASE_URL", "http://127.0.0.1:8089").rstrip("/"),
            video_comfy_base_url=os.environ.get("VIDEO_COMFYUI_BASE_URL", "http://127.0.0.1:8189").rstrip("/"),
            video_api_key=os.environ.get("VIDEO_NATIVE_API_KEY", ""),
            video_timeout=_positive_float("VIDEO_NATIVE_TIMEOUT_SECONDS", 60),
            video_poll_interval=_positive_float("VIDEO_POLL_INTERVAL_SECONDS", 5),
            video_task_timeout=_positive_float("VIDEO_TASK_TIMEOUT_SECONDS", 3600),
            image_provider=image_provider,
            image_base_url=os.environ.get("IMAGE_COMFYUI_BASE_URL", "http://127.0.0.1:8190").rstrip("/"),
            image_timeout=_positive_float("IMAGE_COMFYUI_TIMEOUT_SECONDS", 60),
            image_poll_interval=_positive_float("IMAGE_POLL_INTERVAL_SECONDS", 3),
            image_task_timeout=_positive_float("IMAGE_TASK_TIMEOUT_SECONDS", 1800),
            image_workflow_1mp=cls._path(base, "IMAGE_WORKFLOW_1MP", "workflows/image/t2i_1mp_api.json"),
            image_workflow_4mp=cls._path(base, "IMAGE_WORKFLOW_4MP", "workflows/image/t2i_4mp_api.json"),
        )

    def ensure_dirs(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
