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


def append_missing(env_lines: list[str], example_lines: list[str]) -> list[str]:
    present = set(parse_lines(env_lines))
    extra: list[str] = []
    for raw in example_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            continue
        key = raw.split("=", 1)[0].strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in present:
            continue
        extra.append(raw.rstrip("\n"))
        present.add(key)
    if not extra:
        return env_lines
    output = list(env_lines)
    if output and output[-1] != "":
        output.append("")
    output.extend(extra)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    env_path = repo / ".env"
    example_path = repo / ".env.example"
    example_lines = example_path.read_text(encoding="utf-8").splitlines() if example_path.is_file() else []
    if env_path.is_file():
        lines = append_missing(env_path.read_text(encoding="utf-8").splitlines(), example_lines)
    elif example_lines:
        lines = example_lines
    else:
        lines = ["INFERENCE_API_KEY="]

    key = parse_lines(lines).get("INFERENCE_API_KEY", "")
    if args.apply:
        text = "\n".join(lines)
        if not text.endswith("\n"):
            text += "\n"
        current = env_path.read_text(encoding="utf-8") if env_path.is_file() else None
        if current != text:
            env_path.write_text(text, encoding="utf-8")
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)

    print("key_state=present" if key else "key_state=missing")


if __name__ == "__main__":
    main()
