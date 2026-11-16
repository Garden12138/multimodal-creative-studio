from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = "native-secret-should-not-leak"


class PrepareEnvTests(unittest.TestCase):
    def test_prepare_env_does_not_copy_native_key_or_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".env.example").write_text(
                textwrap.dedent(
                    """\
                    STUDIO_HOST=0.0.0.0
                    STUDIO_PORT=8088
                    INFERENCE_API_BASE_URL=http://127.0.0.1:8090
                    INFERENCE_API_KEY=
                    """
                ),
                encoding="utf-8",
            )
            (repo / "native.env").write_text(f"H3_MINIMAX_API_KEY={SECRET}\nVIDEO_NATIVE_API_KEY={SECRET}\n", encoding="utf-8")
            legacy = repo / "legacy"
            legacy.mkdir()
            (legacy / "tasks.db").write_bytes(b"sqlite")

            missing = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "prepare_env.py"), "--repo", str(repo), "--apply"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(missing.returncode, 0, missing.stderr)
            self.assertIn("key_state=missing", missing.stdout)
            env_text = (repo / ".env").read_text(encoding="utf-8")
            self.assertNotIn(SECRET, env_text)
            self.assertNotIn(SECRET, missing.stdout)
            self.assertNotIn("VIDEO_NATIVE", env_text)
            self.assertNotIn("tasks.db", env_text)
            self.assertNotIn("8189", env_text)
            self.assertNotIn("8190", env_text)
            self.assertNotIn("8089", env_text)

            env_text = env_text.replace("INFERENCE_API_KEY=", "INFERENCE_API_KEY=present-key-value", 1)
            (repo / ".env").write_text(env_text, encoding="utf-8")
            present = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "prepare_env.py"), "--repo", str(repo)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(present.returncode, 0, present.stderr)
            self.assertIn("key_state=present", present.stdout)
            self.assertNotIn("present-key-value", present.stdout)
            self.assertEqual((repo / "native.env").read_text(encoding="utf-8"), f"H3_MINIMAX_API_KEY={SECRET}\nVIDEO_NATIVE_API_KEY={SECRET}\n")


if __name__ == "__main__":
    unittest.main()
