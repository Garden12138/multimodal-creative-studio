from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

TRANSITIONS = {
    "queued": {"submitting", "failed"},
    "submitting": {"running", "failed"},
    "running": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}

TERMINAL_STATES = {"succeeded", "failed"}
ACTIVE_STATES = {"queued", "submitting", "running"}


@dataclass
class Task:
    id: str
    media_type: str | None
    provider: str | None
    model: str | None
    mode: str
    prompt: str
    params: dict
    state: str
    provider_task_id: str | None
    provider_status: str | None
    created: float
    started: float | None
    finished: float | None
    error_code: str | None
    error: str | None
    comfy_prompt_id: str | None
    first_image: str | None
    last_image: str | None
    video: str | None


def task_from_row(row) -> Task:
    data = dict(row)
    raw_params = data.get("params_json")
    params: dict = {}
    if isinstance(raw_params, str) and raw_params:
        try:
            loaded = json.loads(raw_params)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            params = loaded
    return Task(
        id=data["id"],
        media_type=data.get("media_type"),
        provider=data.get("provider"),
        model=data.get("model"),
        mode=data["mode"],
        prompt=data["prompt"],
        params=params,
        state=data["state"],
        provider_task_id=data.get("provider_task_id"),
        provider_status=data.get("provider_status"),
        created=data["created"],
        started=data.get("started"),
        finished=data.get("finished"),
        error_code=data.get("error_code"),
        error=data.get("error"),
        comfy_prompt_id=data.get("comfy_prompt_id"),
        first_image=data.get("first_image"),
        last_image=data.get("last_image"),
        video=data.get("video"),
    )


def to_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()
