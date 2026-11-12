from __future__ import annotations

import json
from pathlib import Path

PROMPT_MARKER = "__H3_STUDIO_PROMPT__"
FIRST_MARKER = "STUDIO_FIRST.png"
LAST_MARKER = "STUDIO_LAST.png"


def workflow_file(workflow_dir: Path, mode: str) -> Path:
    return workflow_dir / f"{mode}_api.json"


def prepare_video_workflow(
    path: Path,
    mode: str,
    prompt: str,
    task_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict:
    workflow = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict) or not workflow or not all(
        isinstance(node, dict) and "class_type" in node and isinstance(node.get("inputs"), dict)
        for node in workflow.values()
    ):
        raise ValueError("workflow is not API JSON")
    mapping = {PROMPT_MARKER: prompt}
    if mode in {"i2v", "flf"}:
        if not first_name:
            raise ValueError("missing first frame")
        mapping[FIRST_MARKER] = first_name
    if mode == "flf":
        if not last_name:
            raise ValueError("missing last frame")
        mapping[LAST_MARKER] = last_name
    counts: dict[str, int] = {}
    workflow = _replace(workflow, mapping, counts)
    for marker in mapping:
        if counts.get(marker, 0) < 1:
            raise ValueError(f"missing marker {marker}")
    saved = [node for node in workflow.values() if node.get("class_type") == "SaveVideo"]
    if len(saved) != 1 or "filename_prefix" not in saved[0].get("inputs", {}):
        raise ValueError("workflow must contain one SaveVideo node")
    saved[0]["inputs"]["filename_prefix"] = f"video/studio_{task_id}"
    rendered = json.dumps(workflow)
    for marker in mapping:
        if marker in rendered:
            raise ValueError(f"marker remained {marker}")
    return workflow


def _replace(value, mapping: dict[str, str], counts: dict[str, int]):
    if isinstance(value, dict):
        return {key: _replace(item, mapping, counts) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, mapping, counts) for item in value]
    if isinstance(value, str):
        for marker, replacement in mapping.items():
            found = value.count(marker)
            if found:
                counts[marker] = counts.get(marker, 0) + found
                value = value.replace(marker, replacement)
    return value
