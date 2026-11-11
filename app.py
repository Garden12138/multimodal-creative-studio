import copy
import io
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image, UnidentifiedImageError

BASE = Path(__file__).resolve().parent
WORKFLOWS = BASE / "workflows"
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
COMFY = os.environ.get("COMFY_URL", "http://127.0.0.1:8189").rstrip("/")
COMFY_OUTPUT = Path(os.environ.get("COMFY_OUTPUT", str(Path.home() / "ai-services/minimax-h3/ComfyUI/output"))).resolve()
DATABASE = BASE / "tasks.db"
STOP = threading.Event()
MAX_UPLOAD = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16_000_000

for folder in (WORKFLOWS, UPLOADS, OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)


def db():
    conn = sqlite3.connect(DATABASE, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def setup():
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, mode TEXT NOT NULL, prompt TEXT NOT NULL,
            state TEXT NOT NULL, created REAL NOT NULL, started REAL,
            finished REAL, comfy_prompt_id TEXT, first_image TEXT,
            last_image TEXT, video TEXT, error TEXT
        )""")
        conn.execute("UPDATE tasks SET state='failed', finished=?, error=? WHERE state='running'",
                     (time.time(), "网页服务重启；请检查 ComfyUI 任务并重新提交"))


def row(task_id):
    with db() as conn:
        r = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(r) if r else None


def patch(task_id, **values):
    columns = ", ".join(f"{key}=?" for key in values)
    with db() as conn:
        conn.execute(f"UPDATE tasks SET {columns} WHERE id=?", (*values.values(), task_id))


def recursive_replace(obj, mapping, counts):
    if isinstance(obj, dict):
        return {k: recursive_replace(v, mapping, counts) for k, v in obj.items()}
    if isinstance(obj, list):
        return [recursive_replace(v, mapping, counts) for v in obj]
    if isinstance(obj, str):
        for marker, value in mapping.items():
            if marker in obj:
                counts[marker] = counts.get(marker, 0) + obj.count(marker)
                obj = obj.replace(marker, value)
    return obj


def make_workflow(task, first_name, last_name):
    path = WORKFLOWS / f"{task['mode']}_api.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict) or not workflow or not all(
        isinstance(node, dict) and "class_type" in node and "inputs" in node
        for node in workflow.values()
    ):
        raise ValueError("工作流文件不是 ComfyUI API 格式")
    mapping = {"__H3_STUDIO_PROMPT__": task["prompt"]}
    if task["mode"] in ("i2v", "flf"):
        mapping["STUDIO_FIRST.png"] = first_name
    if task["mode"] == "flf":
        mapping["STUDIO_LAST.png"] = last_name
    counts = {}
    workflow = recursive_replace(workflow, mapping, counts)
    for marker in mapping:
        if not counts.get(marker):
            raise ValueError(f"工作流缺少占位标记 {marker}，请重新导出 API JSON")

    save_nodes = [node for node in workflow.values()
                  if node.get("class_type") == "SaveVideo"
                  and "filename_prefix" in node.get("inputs", {})]
    if len(save_nodes) != 1:
        raise ValueError("工作流必须恰好包含一个 SaveVideo 节点")
    save_nodes[0]["inputs"]["filename_prefix"] = f"video/h3studio_{task['id']}"
    return workflow


def upload_to_comfy(session, path):
    with open(path, "rb") as image_file:
        response = session.post(
            f"{COMFY}/upload/image",
            files={"image": (path.name, image_file, "image/png")},
            data={"overwrite": "true"},
            timeout=120,
        )
    response.raise_for_status()
    info = response.json()
    if not isinstance(info.get("name"), str):
        raise RuntimeError("ComfyUI 图片上传未返回文件名")
    return info["name"]


def wait_for_free_queue(session):
    while not STOP.is_set():
        reply = session.get(f"{COMFY}/queue", timeout=20)
        reply.raise_for_status()
        queue = reply.json()
        if not queue.get("queue_running") and not queue.get("queue_pending"):
            return
        STOP.wait(4)
    raise RuntimeError("网页服务正在停止")


def find_video(task_id):
    base = (COMFY_OUTPUT / "video").resolve()
    for path in sorted(base.glob(f"h3studio_{task_id}*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_file() and path.suffix.lower() in (".mp4", ".webm", ".mkv"):
            resolved = path.resolve()
            if resolved.is_relative_to(base) and resolved.stat().st_size:
                return resolved
    return None


def process(task):
    with requests.Session() as session:
        wait_for_free_queue(session)
        first = upload_to_comfy(session, Path(task["first_image"])) if task["first_image"] else None
        last = upload_to_comfy(session, Path(task["last_image"])) if task["last_image"] else None
        workflow = make_workflow(task, first, last)
        result = session.post(f"{COMFY}/prompt", json={"prompt": workflow, "client_id": task["id"]}, timeout=45)
        if not result.ok:
            raise RuntimeError(f"ComfyUI 拒绝任务：{result.text[:1200]}")
        info = result.json()
        pid = info.get("prompt_id")
        if not pid:
            raise RuntimeError(f"ComfyUI 没有返回 prompt_id：{info}")
        patch(task["id"], comfy_prompt_id=pid)

        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline and not STOP.is_set():
            reply = session.get(f"{COMFY}/history/{pid}", timeout=30)
            reply.raise_for_status()
            record = reply.json().get(pid)
            if record:
                status = record.get("status", {})
                if status.get("status_str") == "error":
                    errors = [x for x in status.get("messages", []) if isinstance(x, list) and x and x[0] == "execution_error"]
                    raise RuntimeError(f"ComfyUI 执行失败：{str(errors or status)[:1200]}")
                video = find_video(task["id"])
                if not video:
                    raise RuntimeError("工作流执行结束，但未找到 MP4/WebM；请检查 Save Video 节点")
                target = OUTPUTS / f"{task['id']}{video.suffix.lower()}"
                shutil.copy2(video, target)
                patch(task["id"], state="succeeded", video=str(target), finished=time.time())
                return
            STOP.wait(5)
        raise TimeoutError("任务等待超过一小时或服务关闭，请查看 ComfyUI 历史")


def worker():
    while not STOP.is_set():
        with db() as conn:
            next_task = conn.execute("SELECT id FROM tasks WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if next_task:
                conn.execute("UPDATE tasks SET state='running', started=? WHERE id=?", (time.time(), next_task["id"]))
        if not next_task:
            STOP.wait(2)
            continue
        task_id = next_task["id"]
        try:
            process(row(task_id))
        except Exception as exc:
            patch(task_id, state="failed", finished=time.time(), error=str(exc)[:1500])
            print(f"[H3] {task_id} failed: {exc}", flush=True)


@asynccontextmanager
async def lifespan(app):
    setup()
    STOP.clear()
    thread = threading.Thread(target=worker, name="h3-worker", daemon=True)
    thread.start()
    yield
    STOP.set()
    thread.join(timeout=2)


app = FastAPI(title="H3 Video Studio", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    try:
        r = requests.get(f"{COMFY}/system_stats", timeout=5)
        comfy_ok = r.ok
    except requests.RequestException:
        comfy_ok = False
    return {"web": "ok", "comfyui": "ok" if comfy_ok else "unreachable",
            "workflows": {m: (WORKFLOWS / f"{m}_api.json").is_file() for m in ("t2v", "i2v", "flf")}}


async def receive_image(file, task_id, suffix):
    if not file:
        return None
    if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(400, "只支持 PNG、JPEG、WebP 图片")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "单张图片不能超过 12MB")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((2048, 2048))
            path = UPLOADS / f"{task_id}_{suffix}.png"
            im.save(path)
            return str(path)
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise HTTPException(400, f"图片无效：{e}")


@app.post("/api/tasks")
async def create_task(mode: str = Form(...), prompt: str = Form(...),
                      first_frame: UploadFile | None = File(None),
                      last_frame: UploadFile | None = File(None)):
    prompt = prompt.strip()
    if mode not in ("t2v", "i2v", "flf"):
        raise HTTPException(400, "生成模式不支持")
    if not 1 <= len(prompt) <= 2000:
        raise HTTPException(400, "提示词长度应为 1~2000 字")
    if mode == "t2v" and (first_frame or last_frame):
        raise HTTPException(400, "文生视频不能上传图片")
    if mode == "i2v" and (not first_frame or last_frame):
        raise HTTPException(400, "图生视频仅上传一张首帧图")
    if mode == "flf" and (not first_frame or not last_frame):
        raise HTTPException(400, "首末帧模式需要两张图片")
    if not (WORKFLOWS / f"{mode}_api.json").is_file():
        raise HTTPException(503, "对应工作流未准备好")
    task_id = uuid.uuid4().hex
    first = await receive_image(first_frame, task_id, "first")
    last = await receive_image(last_frame, task_id, "last")
    with db() as conn:
        conn.execute("INSERT INTO tasks (id,mode,prompt,state,created,first_image,last_image) VALUES (?,?,?,?,?,?,?)",
                     (task_id, mode, prompt, "queued", time.time(), first, last))
    return {"id": task_id, "state": "queued"}


def public_task(task):
    fields = ("id", "mode", "prompt", "state", "created", "started", "finished", "error")
    return {key: task.get(key) for key in fields}


@app.get("/api/tasks")
def list_tasks():
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created DESC LIMIT 30").fetchall()
    return [public_task(dict(x)) for x in rows]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = row(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return public_task(task)


@app.get("/api/tasks/{task_id}/video")
def get_video(task_id: str):
    task = row(task_id)
    if not task or task["state"] != "succeeded" or not task["video"]:
        raise HTTPException(404, "视频尚未生成")
    path = Path(task["video"]).resolve()
    if not path.is_relative_to(OUTPUTS.resolve()) or not path.is_file():
        raise HTTPException(404, "视频文件不存在")
    mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska"}.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=mime, filename=f"h3_{task_id}{path.suffix}", content_disposition_type="inline")
