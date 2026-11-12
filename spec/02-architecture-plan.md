# Architecture Plan — V1

## 1. Current repository baseline

Reviewed from GitHub `main` on 2026-11-11:

- `app.py`: one FastAPI module containing SQLite setup, upload validation, ComfyUI workflow mutation, worker thread, polling, and file delivery.
- `index.html`: vanilla HTML/CSS/JavaScript conversation-style video UI.
- `workflows/t2v_api.json`, `workflows/i2v_api.json`, `workflows/flf_api.json`: existing ComfyUI API-format video workflows.
- `check_workflows.py`: validates those three workflows.
- `requirements.txt`: FastAPI, Uvicorn, Requests, python-multipart, Pillow.
- `.DS_Store` exists; `.gitignore`, tests, modular providers, and image workflows are absent.
- Current `app.py` calls video ComfyUI directly at `COMFY_URL` (default `127.0.0.1:8189`). This is the legacy path. New V1 video requests should use Native API `8089`.

Current SQLite `tasks` columns include `id`, `mode`, `prompt`, `state`, `created`, `started`, `finished`, `comfy_prompt_id`, `first_image`, `last_image`, `video`, and `error`. Existing rows/files must survive migration.

## 2. Target topology

```text
Browser
  |
  v
FastAPI Studio (0.0.0.0:8088)
  |
  +-- Conversation / Task API
  +-- Task Service -------- SQLite
  |       +-- state machine
  |       +-- provider registry
  |       +-- asset registry
  |
  +-- Video Provider: H3 Native API :8089
  |       +-- H3 Video ComfyUI :8189 (GPU 0)
  |
  +-- Image Provider: H3 Image ComfyUI :8190 (GPU 2)

GPU 1 / AvatarChat is outside Studio ownership.
```

## 3. Suggested source layout

Use gradual extraction; keep `app.py` as a compatibility entrypoint exporting `app` so `uvicorn app:app` still works.

```text
app.py
studio/
  config.py
  database.py
  models/task.py
  models/asset.py
  api/health.py
  api/tasks.py
  api/legacy_video.py
  services/task_service.py
  services/worker.py
  services/asset_service.py
  providers/base.py
  providers/registry.py
  providers/video/minimax_h3_native.py
  providers/image/comfyui_h3_image.py
index.html
workflows/
  t2v_api.json                 # preserve
  i2v_api.json                 # preserve
  flf_api.json                 # preserve
  image/t2i_1mp_api.json       # import verified API export
  image/t2i_4mp_api.json       # import verified API export
tests/
docs/
spec/
prompts/
.env.example
.gitignore
```

Do not move/rename current workflow files unless all references and compatibility tests are updated.

## 4. Provider contract

Keep provider-specific HTTP details inside adapters. Minimal protocol:

- `provider_id`, `supported_media_types`, `supported_modes`
- `health()`
- `submit(task_input) -> ProviderSubmission`
- `poll(provider_task_id) -> ProviderTaskStatus`
- `fetch_assets(provider_task_id) -> list[ProviderAsset]`
- optional `cancel()` only if verified

### Video: `minimax_h3_native`

- Base URL: `http://127.0.0.1:8089`.
- Bearer auth.
- Known route family: `/v2/video_generation`, `/v2/query/video_generation/{task_id}`, `/v2/query/video_generation`, `/v2/video_generation/{task_id}` (DELETE).
- Known result URL: `task.content.url`.
- Request uses `content[]` for text and first/last-frame inputs.
- Exact local item fields and status schema are not present in this repository. Read `~/ai-services/h3-minimax-native-v2/docs/AGENT_INTEGRATION.md`, `/openapi.json`, or local source. Capture redacted fixtures. Do not guess cloud API fields.
- Verify T2V, I2V, FLF against the actual local service.
- New provider must not call video ComfyUI `8189` directly.

### Image: `comfyui_h3_image`

- Base URL: `http://127.0.0.1:8190`.
- Use `GET /system_stats`, `GET /object_info`, `POST /prompt`, `GET /history/{prompt_id}`, and `GET /view?filename=...&subfolder=...&type=output`.
- Two separately exported API workflows are required: 1MP and 4MP.
- Current GitHub repo has no image workflow JSON. Locate or export them from 104 using ComfyUI `File → Export Workflow (API)`, not ordinary UI save.
- Put them at `workflows/image/t2i_1mp_api.json` and `workflows/image/t2i_4mp_api.json` after validation.
- Insert a unique prompt marker `__H3_IMAGE_STUDIO_PROMPT__` in the positive prompt field and verify it occurs in the intended field.
- Validate API JSON shape, prompt marker, model configuration, and image output node. Do not hardcode unverified node IDs.
- Parse output metadata from history and retrieve bytes using `/view`.
- Keep model, quantization, attention, sampler, steps, CFG, and VAE in the verified workflow; UI must not mutate them.

## 5. Unified task and asset model

Extend existing `tasks` table additively. Keep old columns during compatibility period.

Logical task fields:

- `id` (Studio ID)
- `media_type` (`image`/`video`)
- `provider`, `model`, `mode`
- `prompt`, `params_json`
- `state`, `provider_task_id`, `provider_status`
- `created`, `started`, `finished`
- `error_code`, `error`
- legacy `comfy_prompt_id`, `first_image`, `last_image`, `video` fields retained as needed

Add `assets` table with `id`, `task_id`, `media_type`, `filename`, `mime_type`, `size_bytes`, `sha256`, `storage_key`, `created_at`.

State transitions:

```text
queued -> submitting -> running -> succeeded
                      \-> failed
queued/submitting/running -> failed
```

- Store provider ID immediately after submission.
- Preserve raw provider status for diagnostics.
- Unknown status is never success.
- On restart, reconcile non-terminal tasks when supported; do not blindly mark every running task failed.
- No auto-resubmit after network timeout.

## 6. Worker/concurrency

- One FastAPI process initially.
- Independent provider workers or provider-scoped concurrency gates.
- Default concurrency = 1 for each provider.
- Image and video can run independently because they use GPU 2 and GPU 0.
- Do not send multiple simultaneous generations to the same ComfyUI provider unless verified safe.
- Use bounded polling intervals and task deadlines.

## 7. Database migration

- Back up `tasks.db` before 104 deployment.
- Add schema version tracking.
- Use additive `ALTER TABLE` and create `assets`; do not drop/rename old table or columns.
- Existing history and output files must remain readable.
- Test migration using a fixture matching the current schema.

## 8. Error model

Normalize to `PROVIDER_UNAVAILABLE`, `PROVIDER_AUTH_FAILED`, `PROVIDER_REJECTED`, `WORKFLOW_NOT_READY`, `WORKFLOW_INVALID`, `INPUT_INVALID`, `PROVIDER_TIMEOUT`, `RESULT_NOT_FOUND`, `ASSET_STORAGE_FAILED`, `INTERNAL_ERROR`.

Redact keys and sensitive headers. Do not expose tracebacks or absolute paths to browser clients.

## 9. Music3 future compatibility

Do not implement Music3 now. Keep modality/provider/asset handling extensible so audio can be added later. Do not register a music provider until its runtime and API contract are separately verified.
