#!/usr/bin/env python3
"""Prepare the server-local .env without printing secrets."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import stat
from pathlib import Path


def parse_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        values[key] = strip_quotes(value.strip())
    return values


def strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def format_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._~+/=:@-]*", value):
        return value
    return shlex.quote(value)


def upsert(lines: list[str], updates: dict[str, str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            output.append(raw)
            continue
        key = raw.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={format_value(updates[key])}")
            seen.add(key)
        else:
            output.append(raw)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={format_value(value)}")
    return output


def same_directory(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--native-env", required=True)
    parser.add_argument("--legacy-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    env_path = repo / ".env"
    example_path = repo / ".env.example"
    native_path = Path(args.native_env)
    legacy = Path(args.legacy_dir)

    if env_path.is_file():
        original = env_path.read_text(encoding="utf-8").splitlines()
    elif example_path.is_file():
        original = example_path.read_text(encoding="utf-8").splitlines()
    else:
        original = []

    current = parse_lines(original)
    native = parse_lines(native_path.read_text(encoding="utf-8").splitlines()) if native_path.is_file() else {}
    updates: dict[str, str] = {}

    if current.get("VIDEO_NATIVE_API_KEY"):
        key_state = "present"
    elif native.get("H3_MINIMAX_API_KEY"):
        updates["VIDEO_NATIVE_API_KEY"] = native["H3_MINIMAX_API_KEY"]
        key_state = "copied" if args.apply else "ready"
    else:
        key_state = "missing"

    defaults = {
        "STUDIO_DATABASE_PATH": "./tasks.db",
        "STUDIO_UPLOAD_DIR": "./uploads",
        "STUDIO_OUTPUT_DIR": "./outputs",
    }
    database_path = current.get("STUDIO_DATABASE_PATH", defaults["STUDIO_DATABASE_PATH"])
    if (repo / "tasks.db").is_file():
        data_state = "local"
    elif not same_directory(legacy, repo) and (legacy / "tasks.db").is_file():
        untouched = all(current.get(name, default) == default for name, default in defaults.items())
        if untouched:
            database_path = str((legacy / "tasks.db").resolve())
            updates["STUDIO_DATABASE_PATH"] = database_path
            updates["STUDIO_UPLOAD_DIR"] = str((legacy / "uploads").resolve())
            updates["STUDIO_OUTPUT_DIR"] = str((legacy / "outputs").resolve())
            data_state = "legacy"
        else:
            data_state = "custom"
    else:
        data_state = "empty"

    if args.apply and (updates or not env_path.is_file()):
        env_path.write_text("\n".join(upsert(original, updates)) + "\n", encoding="utf-8")
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
    elif args.apply and env_path.is_file():
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)

    print(f"key_state={key_state}")
    print(f"data_state={data_state}")
    print("database_path=" + shlex.quote(database_path))


if __name__ == "__main__":
    main()
