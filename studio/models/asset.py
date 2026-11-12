from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Asset:
    id: str
    task_id: str
    media_type: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    created_at: float


def asset_from_row(row) -> Asset:
    data = dict(row)
    return Asset(
        id=data["id"],
        task_id=data["task_id"],
        media_type=data["media_type"],
        filename=data["filename"],
        mime_type=data["mime_type"],
        size_bytes=data["size_bytes"],
        sha256=data["sha256"],
        storage_key=data["storage_key"],
        created_at=data["created_at"],
    )
