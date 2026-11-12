from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from studio.database import UNRECONCILABLE, connect, get_asset, get_task, init_db, list_assets, migrate, reconcile_unfinished
from studio.database import LEGACY_TASK_SQL
from studio.storage import resolve_under
from tests.helpers import make_settings


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = make_settings(Path(self.tmp.name))

    def test_legacy_rows_survive_and_running_tasks_are_not_all_failed(self):
        video = self.settings.output_dir / "legacy.mp4"
        video.write_bytes(b"legacy-video")
        with sqlite3.connect(self.settings.database_path) as conn:
            conn.execute(LEGACY_TASK_SQL)
            conn.execute(
                "INSERT INTO tasks (id, mode, prompt, state, created, finished, video) VALUES (?,?,?,?,?,?,?)",
                ("old-ok", "t2v", "keep me", "succeeded", 10, 12, str(video.resolve())),
            )
            conn.execute(
                "INSERT INTO tasks (id, mode, prompt, state, created) VALUES (?,?,?,?,?)",
                ("old-queue", "i2v", "still queued", "queued", 11),
            )
            conn.execute(
                "INSERT INTO tasks (id, mode, prompt, state, created, comfy_prompt_id) VALUES (?,?,?,?,?,?)",
                ("old-running", "t2v", "lost provider id", "running", 12, "comfy-1"),
            )
            conn.execute(
                "INSERT INTO tasks (id, mode, prompt, state, created) VALUES (?,?,?,?,?)",
                ("tracked", "flf", "has provider id", "running", 13),
            )
        with connect(self.settings) as conn:
            migrate(conn)
            conn.execute("UPDATE tasks SET provider_task_id=? WHERE id=?", ("native-1", "tracked"))
            reconcile_unfinished(conn)
        init_db(self.settings)
        with connect(self.settings) as conn:
            kept = get_task(conn, "old-ok")
            queued = get_task(conn, "old-queue")
            lost = get_task(conn, "old-running")
            tracked = get_task(conn, "tracked")
            assets = list_assets(conn, "old-ok")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        self.assertEqual(kept.state, "succeeded")
        self.assertEqual(kept.prompt, "keep me")
        self.assertEqual(kept.media_type, "video")
        self.assertEqual(kept.provider, "legacy_comfyui_video")
        self.assertEqual(kept.video, str(video.resolve()))
        self.assertEqual(queued.state, "queued")
        self.assertEqual(queued.provider, "minimax_h3_native")
        self.assertEqual(lost.state, "failed")
        self.assertEqual(lost.error, UNRECONCILABLE)
        self.assertNotIn("网页服务重启", lost.error)
        self.assertEqual(tracked.state, "running")
        self.assertEqual(tracked.provider_task_id, "native-1")
        self.assertIsNone(tracked.error)
        self.assertIn("provider_task_id", columns)
        self.assertIn("error_code", columns)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].sha256, __import__("hashlib").sha256(b"legacy-video").hexdigest())
        self.assertTrue(resolve_under(self.settings.output_dir, assets[0].storage_key).is_file())

    def test_asset_path_cannot_escape_output_dir(self):
        init_db(self.settings)
        with connect(self.settings) as conn:
            conn.execute(
                "INSERT INTO tasks (id, mode, prompt, state, created, media_type) VALUES (?,?,?,?,?,?)",
                ("t1", "t2i", "x", "succeeded", 1, "image"),
            )
            conn.execute(
                """INSERT INTO assets
                   (id, task_id, media_type, filename, mime_type, size_bytes, sha256, storage_key, created_at)
                   VALUES ('bad', 't1', 'image', 'x.png', 'image/png', 1, 'abc', ?, 1)""",
                ("../../etc/passwd",),
            )
            asset = get_asset(conn, "bad")
        with self.assertRaises(Exception):
            resolve_under(self.settings.output_dir, asset.storage_key)


if __name__ == "__main__":
    unittest.main()
