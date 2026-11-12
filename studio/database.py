from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from studio.config import Settings
from studio.models.asset import Asset, asset_from_row
from studio.models.task import TRANSITIONS, Task, task_from_row
from studio.storage import resolve_under, sha256_file

LEGACY_TASK_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL,
    created REAL NOT NULL,
    started REAL,
    finished REAL,
    comfy_prompt_id TEXT,
    first_image TEXT,
    last_image TEXT,
    video TEXT,
    error TEXT
)
"""

ADDED_COLUMNS = (
    ("media_type", "TEXT"),
    ("provider", "TEXT"),
    ("model", "TEXT"),
    ("params_json", "TEXT"),
    ("provider_task_id", "TEXT"),
    ("provider_status", "TEXT"),
    ("error_code", "TEXT"),
)

SCHEMA_VERSION = 2
UNRECONCILABLE = "服务重启时任务还没有上游任务编号，未自动重新提交。"


@contextmanager
def connect(settings: Settings):
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at REAL NOT NULL
        )"""
    )
    conn.execute(LEGACY_TASK_SQL)
    existing = _columns(conn)
    for name, declaration in ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_provider_state_created ON tasks(provider, state, created)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_task ON assets(task_id, created_at)")
    conn.execute("UPDATE tasks SET media_type='video' WHERE media_type IS NULL OR media_type=''")
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, time.time()),
    )


def reconcile_unfinished(conn: sqlite3.Connection, now: float | None = None) -> None:
    """Continue tasks that already have a provider id. Do not resubmit the rest."""
    moment = time.time() if now is None else now
    rows = conn.execute(
        "SELECT id, state, provider_task_id FROM tasks WHERE state IN ('submitting', 'running')"
    ).fetchall()
    for row in rows:
        if row["provider_task_id"]:
            if row["state"] == "submitting":
                conn.execute("UPDATE tasks SET state='running' WHERE id=?", (row["id"],))
            continue
        conn.execute(
            "UPDATE tasks SET state='failed', finished=?, error_code=?, error=? WHERE id=?",
            (moment, "INTERNAL_ERROR", UNRECONCILABLE, row["id"]),
        )
    conn.execute(
        """UPDATE tasks
           SET provider='legacy_comfyui_video'
           WHERE (provider IS NULL OR provider='')
             AND media_type='video'
             AND state IN ('succeeded', 'failed')
             AND (comfy_prompt_id IS NOT NULL OR (video IS NOT NULL AND video != ''))"""
    )
    conn.execute(
        """UPDATE tasks
           SET provider='minimax_h3_native',
               model=COALESCE(NULLIF(model, ''), 'MiniMax-H3')
           WHERE (provider IS NULL OR provider='')
             AND media_type='video'
             AND state='queued'"""
    )
    conn.execute(
        """UPDATE tasks
           SET provider='minimax_h3_native'
           WHERE (provider IS NULL OR provider='')
             AND media_type='video'
             AND provider_task_id IS NOT NULL
             AND state IN ('submitting', 'running')"""
    )
    conn.execute(
        """UPDATE tasks
           SET provider='comfyui_h3_image'
           WHERE (provider IS NULL OR provider='')
             AND media_type='image'
             AND provider_task_id IS NOT NULL
             AND state IN ('submitting', 'running')"""
    )


def backfill_video_assets(conn: sqlite3.Connection, output_dir: Path) -> None:
    rows = conn.execute(
        "SELECT id, video, finished, created FROM tasks WHERE video IS NOT NULL AND video != ''"
    ).fetchall()
    for row in rows:
        existing = conn.execute(
            "SELECT id FROM assets WHERE task_id=? AND media_type='video' LIMIT 1",
            (row["id"],),
        ).fetchone()
        if existing:
            continue
        try:
            path = resolve_under(output_dir, row["video"])
        except Exception:
            continue
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska"}.get(
            suffix, "application/octet-stream"
        )
        storage_key = path.resolve().relative_to(output_dir.resolve()).as_posix()
        conn.execute(
            """INSERT INTO assets
               (id, task_id, media_type, filename, mime_type, size_bytes, sha256, storage_key, created_at)
               VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex,
                row["id"],
                path.name,
                mime,
                path.stat().st_size,
                sha256_file(path),
                storage_key,
                row["finished"] or row["created"] or time.time(),
            ),
        )


def init_db(settings: Settings) -> None:
    settings.ensure_dirs()
    with connect(settings) as conn:
        migrate(conn)
        reconcile_unfinished(conn)
        backfill_video_assets(conn, settings.output_dir)


def insert_task(conn: sqlite3.Connection, task: Task) -> None:
    conn.execute(
        """INSERT INTO tasks (
            id, mode, prompt, state, created, started, finished, comfy_prompt_id,
            first_image, last_image, video, error, media_type, provider, model,
            params_json, provider_task_id, provider_status, error_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            task.id,
            task.mode,
            task.prompt,
            task.state,
            task.created,
            task.started,
            task.finished,
            task.comfy_prompt_id,
            task.first_image,
            task.last_image,
            task.video,
            task.error,
            task.media_type,
            task.provider,
            task.model,
            json.dumps(task.params, ensure_ascii=False),
            task.provider_task_id,
            task.provider_status,
            task.error_code,
        ),
    )


def get_task(conn: sqlite3.Connection, task_id: str) -> Task | None:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return task_from_row(row) if row else None


def list_tasks(conn: sqlite3.Connection, *, media_type: str | None, limit: int) -> list[Task]:
    if media_type:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE media_type=? ORDER BY created DESC LIMIT ?",
            (media_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [task_from_row(row) for row in rows]


def update_task(conn: sqlite3.Connection, task_id: str, **values) -> Task:
    current = get_task(conn, task_id)
    if current is None:
        raise KeyError(task_id)
    new_state = values.get("state", current.state)
    if new_state != current.state and new_state not in TRANSITIONS.get(current.state, set()):
        raise ValueError(f"invalid transition {current.state} -> {new_state}")
    if "params" in values:
        values["params_json"] = json.dumps(values.pop("params"), ensure_ascii=False)
    assignments = ", ".join(f"{key}=?" for key in values)
    conn.execute(
        f"UPDATE tasks SET {assignments} WHERE id=?",
        (*values.values(), task_id),
    )
    updated = get_task(conn, task_id)
    if updated is None:
        raise KeyError(task_id)
    return updated


def claim_next(conn: sqlite3.Connection, provider_id: str, now: float) -> Task | None:
    active = conn.execute(
        """SELECT * FROM tasks
           WHERE provider=? AND state IN ('submitting', 'running')
           ORDER BY created LIMIT 1""",
        (provider_id,),
    ).fetchone()
    if active:
        return task_from_row(active)
    queued = conn.execute(
        """SELECT id FROM tasks
           WHERE provider=? AND state='queued'
           ORDER BY created LIMIT 1""",
        (provider_id,),
    ).fetchone()
    if not queued:
        return None
    cursor = conn.execute(
        "UPDATE tasks SET state='submitting', started=? WHERE id=? AND state='queued'",
        (now, queued["id"]),
    )
    if cursor.rowcount != 1:
        return None
    return get_task(conn, queued["id"])


def insert_asset(conn: sqlite3.Connection, asset: Asset) -> None:
    conn.execute(
        """INSERT INTO assets
           (id, task_id, media_type, filename, mime_type, size_bytes, sha256, storage_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            asset.id,
            asset.task_id,
            asset.media_type,
            asset.filename,
            asset.mime_type,
            asset.size_bytes,
            asset.sha256,
            asset.storage_key,
            asset.created_at,
        ),
    )


def list_assets(conn: sqlite3.Connection, task_id: str) -> list[Asset]:
    rows = conn.execute(
        "SELECT * FROM assets WHERE task_id=? ORDER BY created_at",
        (task_id,),
    ).fetchall()
    return [asset_from_row(row) for row in rows]


def get_asset(conn: sqlite3.Connection, asset_id: str) -> Asset | None:
    row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    return asset_from_row(row) if row else None
