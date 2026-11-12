# 104 Machine — Integration, Deployment & Acceptance

## 1. Host and GPU map

Host: `bjai-W580-G20` · LAN: `192.168.10.104` · Ubuntu · about 62 GiB RAM · 3 × RTX 2080 Ti 22GB · Driver `575.64.03` · CUDA `12.9`.

| GPU | Existing workload | Rule |
|---|---|---|
| GPU 0 | H3 Video | Use existing service only |
| GPU 1 | AvatarChat | Do not touch |
| GPU 2 | H3 Image | Use existing service only |

Studio must not manage GPU processes or change `CUDA_VISIBLE_DEVICES` for existing services.

## 2. Service map

| Component | Directory | Local URL | Role |
|---|---|---|---|
| H3 Video ComfyUI | `~/ai-services/minimax-h3` | `http://127.0.0.1:8189` | Video engine, GPU 0 |
| H3 Native API V2 | `~/ai-services/h3-minimax-native-v2` | `http://127.0.0.1:8089` | Studio-facing video API |
| H3 Image ComfyUI | `~/ai-services/minimax-h3-image/ComfyUI` | `http://127.0.0.1:8190` | Image engine, GPU 2 |
| Studio | currently `~/ai-services/h3-video-studio` | `http://0.0.0.0:8088` bind | Web/API |

Browser URL: `http://192.168.10.104:8088`.

## 3. Video integration

Required path: `Studio → Native API :8089 → Video ComfyUI :8189`.

Known local route family:

- `POST /v2/video_generation`
- `GET /v2/query/video_generation/{task_id}`
- `GET /v2/query/video_generation`
- `DELETE /v2/video_generation/{task_id}`

Auth: `Authorization: Bearer <API_KEY>`. Known request shape uses `content[]` for text and first-/last-frame content. Known completion URL is under `task.content.url`. Local implementation is a subset of the cloud protocol.

**The exact request JSON, `content[]` item type/fields, status values, query parameters, error schema, and result download auth are not contained in this Studio repository.** Before implementing the adapter, inspect:

```bash
cd ~/ai-services/h3-minimax-native-v2
sed -n '1,260p' docs/AGENT_INTEGRATION.md
curl -fsS http://127.0.0.1:8089/openapi.json | python -m json.tool | less
```

If OpenAPI is unavailable, inspect `/docs` and the local implementation (`server.py`, `minimax_v2_api.py`). Capture redacted fixtures. Do not infer from cloud docs.

Video baseline: 5 seconds, 608×352, 4-step Turbo; existing ComfyUI 0.38.0 and PyTorch `2.10.0+cu128`. These settings remain in the existing service; Studio must not mutate them.

## 4. Image integration

Existing service: `~/ai-services/minimax-h3-image/ComfyUI`, ComfyUI 0.45.0, `127.0.0.1:8190`, GPU 2.

Known startup configuration:

```bash
CUDA_VISIBLE_DEVICES=2 python main.py \
  --listen 0.0.0.0 --port 8190 --lowvram --reserve-vram 2 \
  --use-pytorch-cross-attention --disable-xformers
```

Verified baseline: W4A8 DiT + NVFP4-AWQ Qwen3-VL + FP16 VAE; PyTorch Attention required; PE off; 6 steps; CFG 1. Observed times: 1024×1024 about 35s, 2048×2048 about 76s (not an SLA).

Use ComfyUI endpoints:

- `GET /system_stats`
- `GET /object_info`
- `POST /prompt`
- `GET /history/{prompt_id}`
- `GET /view?filename=...&subfolder=...&type=output`

### Image workflow files

The current GitHub repository has no image workflow JSON. Exact export filenames and their 104 filesystem location have not been verified. Cursor must locate the verified files or ask for an API export; it must not guess node IDs or fabricate a workflow.

Required project paths after verification:

- `workflows/image/t2i_1mp_api.json`
- `workflows/image/t2i_4mp_api.json`

Use ComfyUI **File → Export Workflow (API)**, not ordinary UI workflow save. Verify API JSON structure (`class_type` and `inputs`), model settings, intended positive-prompt field, unique marker `__H3_IMAGE_STUDIO_PROMPT__`, and image output node. Execute through port 8190 and retrieve the actual output before declaring the workflow valid. Do not commit weights, caches, generated images, or secrets.

## 5. Suggested `.env.example`

```dotenv
STUDIO_HOST=0.0.0.0
STUDIO_PORT=8088
STUDIO_DATABASE_PATH=./tasks.db
STUDIO_UPLOAD_DIR=./uploads
STUDIO_OUTPUT_DIR=./outputs

VIDEO_PROVIDER=minimax_h3_native
VIDEO_NATIVE_BASE_URL=http://127.0.0.1:8089
VIDEO_NATIVE_API_KEY=
VIDEO_NATIVE_TIMEOUT_SECONDS=60
VIDEO_POLL_INTERVAL_SECONDS=5
VIDEO_TASK_TIMEOUT_SECONDS=3600

IMAGE_PROVIDER=comfyui_h3_image
IMAGE_COMFYUI_BASE_URL=http://127.0.0.1:8190
IMAGE_COMFYUI_TIMEOUT_SECONDS=60
IMAGE_POLL_INTERVAL_SECONDS=3
IMAGE_TASK_TIMEOUT_SECONDS=1800
IMAGE_WORKFLOW_1MP=workflows/image/t2i_1mp_api.json
IMAGE_WORKFLOW_4MP=workflows/image/t2i_4mp_api.json
```

Never commit `.env` or expose the video API key in frontend code/health output.

## 6. Deployment

Keep the existing entrypoint and port where practical:

```bash
cd ~/ai-services/h3-video-studio
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8088 --workers 1
```

Health:

```bash
curl -fsS http://127.0.0.1:8088/api/health
```

Before database migration:

```bash
cp tasks.db "tasks.db.backup.$(date +%Y%m%d-%H%M%S)"
```

Do not delete/overwrite `uploads/`, `outputs/`, the DB, or existing video workflow JSONs.

## 7. Real acceptance matrix

Mock tests and real 104 tests must be reported separately.

| ID | Test | Expected |
|---|---|---|
| RT-01 | Video Native API readiness | reachable/authenticated |
| RT-02 | Image ComfyUI readiness | `/system_stats` succeeds |
| RT-03 | Video T2V | generated video previews/downloads |
| RT-04 | Video I2V | first frame submitted; result previews/downloads |
| RT-05 | Video FLF | first and last frames submitted; result previews/downloads |
| RT-06 | Image 1024×1024 | generated image previews/downloads |
| RT-07 | Image 2048×2048 | generated image previews/downloads |
| RT-08 | History refresh | all tasks remain visible |
| RT-09 | Studio restart | terminal tasks/assets remain available |
| RT-10 | Independent image/video task | provider queues remain isolated |

Before testing, verify GPU 0=video, GPU 1=AvatarChat, GPU 2=image. Do not restart/kill/reconfigure inference services without separate user authorization.

Final report must include commit SHA, branch, runtime, automated test counts, contract evidence, real service results, task IDs, durations, output types/sizes, and blockers. Verdict must be one of `READY_FOR_USER_TRIAL`, `NEEDS_FIXES`, `BLOCKED_BY_PROVIDER_CONTRACT`, `BLOCKED_BY_104_SERVICE`. `READY_FOR_USER_TRIAL` requires RT-03 through RT-07 to pass.
