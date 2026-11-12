from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskInput:
    task_id: str
    media_type: str
    mode: str
    prompt: str
    params: dict
    first_image: str | None = None
    last_image: str | None = None


@dataclass
class ProviderSubmission:
    provider_task_id: str
    provider_status: str | None = None


@dataclass
class ProviderTaskStatus:
    provider_status: str
    normalized: str
    error_code: str | None = None
    error_message: str | None = None
    result_url: str | None = None


@dataclass
class ProviderAsset:
    media_type: str
    mime_type: str
    data: bytes
    filename: str = ""


@dataclass
class Provider:
    provider_id: str
    supported_media_types: tuple[str, ...]
    supported_modes: tuple[str, ...]

    def health(self) -> dict:
        raise NotImplementedError

    def submit(self, task: TaskInput) -> ProviderSubmission:
        raise NotImplementedError

    def poll(self, provider_task_id: str) -> ProviderTaskStatus:
        raise NotImplementedError

    def fetch_assets(self, provider_task_id: str, task: TaskInput) -> list[ProviderAsset]:
        raise NotImplementedError


def task_input_from_task(task) -> TaskInput:
    return TaskInput(
        task_id=task.id,
        media_type=task.media_type or "",
        mode=task.mode,
        prompt=task.prompt,
        params=dict(task.params or {}),
        first_image=task.first_image,
        last_image=task.last_image,
    )
