#!/usr/bin/env python3
"""Create a local .env from the example without printing or copying secrets."""

from __future__ import annotations

import argparse
import os
import re
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    env_path = repo / ".env"
    example_path = repo / ".env.example"
    if env_path.is_file():
        text = env_path.read_text(encoding="utf-8")
    elif example_path.is_file():
        text = example_path.read_text(encoding="utf-8")
    else:
        text = "INFERENCE_API_KEY=\n"

    key = parse_lines(text.splitlines()).get("INFERENCE_API_KEY", "")
    if args.apply and not env_path.is_file():
        env_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
    elif args.apply and env_path.is_file():
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)

    print("key_state=present" if key else "key_state=missing")


if __name__ == "__main__":
    main()
