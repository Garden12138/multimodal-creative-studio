# Product Specification — V1

## 1. Product

**Name:** Multimodal Creative Studio  
**Repository:** `Garden12138/multimodal-creative-studio`  
**Positioning:** A local, conversation-based image/video creation workspace connected to existing inference services on `bjai-W580-G20`.

Studio is not a model runtime. It is the shared UI, task, and asset layer above existing services.

## 2. User outcomes

A user can open the Studio from the internal network, choose Image or Video, enter a prompt, upload video reference frames where required, submit a task, monitor progress, refresh the page without losing history, and preview/download generated results.

## 3. Functional requirements

### FR-001 Conversation workspace

- Keep the existing conversation timeline and prompt composer.
- User submissions appear as user messages; results appear as assistant/result cards.
- History is loaded from the backend after refresh.
- Keep submit feedback, disabled-submit protection, task status, and responsive layout.
- Do not turn the product into an admin dashboard.

### FR-002 Modality selection

- Two top-level modes: `图片` and `视频`.
- Switching modes changes composer controls without clearing prior history.
- Each message/result shows its modality and generation mode.

### FR-003 Image generation

V1 supports **text-to-image only**.

- Provider: H3 Image ComfyUI at `127.0.0.1:8190` by default.
- Mode: `t2i`.
- Resolutions: `1024x1024` (1MP) and `2048x2048` (4MP).
- Prompt is required. Resolution is selected in the composer.
- Backend selects the corresponding verified API workflow JSON.
- UI must not expose sampler, CFG, step count, quantization, VAE, attention backend, or other inference internals.
- Completed results must have preview and download actions.
- Missing/invalid workflow causes an explicit configuration error; never silently select another workflow.

**Verified 104 image baseline:** ComfyUI 0.45.0 on port 8190, GPU 2; W4A8 DiT + NVFP4-AWQ Qwen3-VL + FP16 VAE; PyTorch Attention required; PE off; 6 steps, CFG 1. Observed generation times: about 35s at 1024² and 76s at 2048². These are measurements, not SLAs.

### FR-004 Video generation

V1 preserves all existing modes:

| Mode | UI label | Required input |
|---|---|---|
| `t2v` | 文生视频 | Prompt |
| `i2v` | 图生视频 | Prompt + first/reference frame |
| `flf` | 首末帧视频 | Prompt + first frame + last frame |

- Primary provider: local H3 Native API at `127.0.0.1:8089`.
- Native API internally uses H3 Video ComfyUI at `127.0.0.1:8189`.
- Auth: server-side `Authorization: Bearer <API_KEY>`.
- Known route family: `POST /v2/video_generation`, `GET /v2/query/video_generation/{task_id}`, `GET /v2/query/video_generation`, `DELETE /v2/video_generation/{task_id}`.
- Known completion result URL: `task.content.url`.
- Local implementation is a supported subset, not full cloud API parity.
- Exact request JSON, content item schema, status values, and response nesting must be verified from the local docs/OpenAPI before implementation.
- Preserve current defaults: 5 seconds, 608×352, 4-step Turbo. Do not expose model/workflow tuning in UI.

### FR-005 Unified task lifecycle

Normalized states: `queued`, `submitting`, `running`, `succeeded`, `failed`; `cancelled` is reserved until provider cancellation is verified.

Persist Studio task ID, modality, provider, model, mode, prompt, params, provider task ID, raw provider status, timestamps, and normalized error. Unknown provider statuses must not be marked succeeded. No automatic resubmission/retry in V1.

### FR-006 Asset management

- Store uploads and generated assets under configured Studio directories.
- Persist asset ID, task ID, media type, MIME type, file name, size, SHA-256, and relative storage key.
- Never expose absolute server paths.
- Serve preview/download through validated asset routes only.
- Keep existing `uploads/` and `outputs/` contents during migration.

### FR-007 Provider health

`GET /api/health` reports Studio, DB, video provider, image provider, and each image workflow independently. Reachability is not the same as successful generation; do not claim generation works solely because a port responds.

### FR-008 Legacy compatibility

Keep these routes during V1:

- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/video`

The legacy video submit response should retain `id` and `state` keys. Legacy history and old output files remain readable. Legacy routes must use the same task service/worker system, not a second queue.

### FR-009 Composer UI

- Image: prompt + resolution selector + generate button.
- Video: prompt + T2V/I2V/FLF selector + conditional image upload controls + existing 5-second indicator + generate button.
- T2V rejects images; I2V requires exactly one; FLF requires exactly two.
- Image mode hides video frame uploads.
- Completed image card: image preview + download.
- Completed video card: video player + download.
- Failed card: concise message and task ID; diagnostics remain in server logs.

## 4. Non-functional requirements

- Studio bind: `0.0.0.0:8088`; browser URL on LAN: `http://192.168.10.104:8088`.
- Provider calls use loopback by default because services run on the same host.
- Default concurrency: one task per provider; independent image/video queues.
- No model weights, caches, generated media, secrets, or runtime DBs in Git.
- Desktop and mobile usable.
- Local trusted-network deployment only for V1; public multi-user security is out of scope.

## 5. Future extension (not V1)

Provider and asset abstractions should later permit audio and `MiniMaxAI/MiniMax-Music3`, including audio assets and music-specific request parameters. Do not add a Music3 UI, runtime, endpoint, or mock provider in V1.
