# Project Constitution — Multimodal Creative Studio

- Repository: `Garden12138/multimodal-creative-studio`
- Spec baseline: 2026-11-11
- V1 status: implementation specification, not an acceptance claim

## 1. Mission

Build a lightweight, conversation-oriented creative workspace that submits, monitors, previews, and manages image/video generation tasks through the user's existing local inference services.

**V1 must genuinely integrate BOTH existing services on `bjai-W580-G20` (`192.168.10.104`):**

- H3 Video through the local Native API on port `8089`.
- H3 Image through the ComfyUI API on port `8190`.

This is a V1 delivery requirement, not a placeholder for a later release.

## 2. Non-negotiable invariants

1. Preserve video T2V, I2V, and first/last-frame (FLF) generation.
2. New Studio video requests go through `Studio → H3 Native API :8089 → H3 Video ComfyUI :8189`. Do not make the new Studio depend directly on video ComfyUI workflow internals.
3. Image generation goes through the existing image ComfyUI API on `:8190`; V1 supports T2I at `1024×1024` and `2048×2048`.
4. Never invent local API contracts. Read the deployed `~/ai-services/h3-minimax-native-v2/docs/AGENT_INTEGRATION.md`, `/openapi.json`, or local implementation. If unavailable, record a blocker; do not infer from MiniMax cloud API docs.
5. Do not reinstall, replace, or supervise model services. Studio is an orchestration/UI layer.
6. GPU assignment is fixed: GPU 0 = H3 Video; GPU 1 = AvatarChat; GPU 2 = H3 Image. Do not stop, restart, reconfigure, or consume GPU 1.
7. Keep the existing lightweight stack: FastAPI, SQLite, vanilla HTML/CSS/JavaScript. Do not introduce React, Redis, Celery, Kubernetes, or another database without a demonstrated need.
8. Use additive SQLite migrations. Never drop existing task history, columns, uploads, outputs, or workflow JSONs.
9. Preserve the current chat-style video UI and existing legacy routes while introducing the unified API.
10. Keep the Native API key server-side. Never return it to the browser or print it in logs, test fixtures, or committed files.
11. No silent fallback from Native API to video ComfyUI `8189`. Report provider failure explicitly.
12. Do not claim a test passed unless it was actually run. Mock tests and real 104-machine tests are separate.
13. No OpenAI-compatible API facade is required for V1. Studio has its own API and calls the local H3 Native API protocol.
14. Music is a future extension point only. Prepare provider/asset abstractions for `MiniMaxAI/MiniMax-Music3`, but do not implement or expose a fake music provider in V1.

## 3. V1 scope

### Required

- Image T2I at 1024×1024 and 2048×2048.
- Video T2V, I2V, FLF.
- Unified task history, status, preview, and download.
- Provider health for image and video separately.
- Preserve existing video task history and output files.
- Deploy on 104 at `0.0.0.0:8088`.
- Automated mock/unit tests and a separate real-service acceptance procedure.

### Not in V1

- Music generation/playback.
- Image editing, inpainting, outpainting, background removal, or image-to-image without a separately verified workflow.
- Video editing/timeline/composition/subtitles.
- Model installation/downloads, GPU management, or ComfyUI administration.
- Cloud providers, multi-tenant accounts, or public internet deployment.
- UI controls for internal model/sampler/quantization settings.

## 4. Engineering quality bar

- Bounded connect/read timeouts for every provider call.
- Durable Studio task ID and provider task ID stored separately.
- Explicit state mapping; unknown provider state is never success.
- Safe, normalized user-facing errors; detailed logs must redact secrets.
- Uploaded/generated paths must resolve inside configured storage roots.
- Validate actual image content, MIME type, and upload size.
- Health reports Studio, DB, video provider, image provider, and image workflow readiness independently.
- Keep one web worker unless worker ownership is made multi-process safe.
- Default concurrency is one task per provider; image and video queues are independent.
