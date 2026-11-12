import json
import sys
from pathlib import Path

from studio.workflows_image import RESOLUTIONS, load_workflow

root = Path(__file__).resolve().parent
video_root = root / "workflows"
failed = False
for mode in ("t2v", "i2v", "flf"):
    path = video_root / f"{mode}_api.json"
    if not path.exists():
        print(f"MISSING: {path.name}")
        failed = True
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        assert isinstance(data, dict) and data
        assert all(isinstance(n, dict) and "class_type" in n and "inputs" in n for n in data.values()), (
            f"{path} is not API JSON"
        )
        text = json.dumps(data)
        assert "__H3_STUDIO_PROMPT__" in text, f"{path}: prompt token missing"
        assert mode == "t2v" or "STUDIO_FIRST.png" in text, f"{path}: first frame token missing"
        assert mode != "flf" or "STUDIO_LAST.png" in text, f"{path}: last frame token missing"
        nodes = [n for n in data.values() if n.get("class_type") == "SaveVideo"]
        assert len(nodes) == 1, f"{path}: expected exactly one SaveVideo node"
    except AssertionError as exc:
        print("FAIL", path.name, exc)
        failed = True
        continue
    print("OK", path.name, len(data), "nodes")

image_root = root / "workflows" / "image"
for name, resolution in (
    ("t2i_1mp_api.json", "1024x1024"),
    ("t2i_4mp_api.json", "2048x2048"),
):
    path = image_root / name
    if not path.exists():
        print(f"MISSING: {path.name}")
        failed = True
        continue
    try:
        data = load_workflow(path, RESOLUTIONS[resolution])
    except Exception as exc:
        print("FAIL", path.name, exc)
        failed = True
        continue
    print("OK", path.name, len(data), "nodes", resolution)

if failed:
    sys.exit(1)
