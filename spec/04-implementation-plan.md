# Implementation Plan — V1

Cursor must execute phases in order and attach evidence before marking each gate complete.

## Phase 0 — Baseline and service evidence

- [ ] Inspect all repository files, Git status, and current branch.
- [ ] Run existing `python check_workflows.py`.
- [ ] Record current routes, SQLite schema, workflow names, frontend behavior, and launch command.
- [ ] Add `.gitignore`; remove `.DS_Store` only after confirming it is tracked and no user data is inside.
- [ ] Read the deployed H3 Native API `docs/AGENT_INTEGRATION.md` or `/openapi.json`.
- [ ] Verify exact request/response schema and all three video modes.
- [ ] Verify image ComfyUI `/system_stats`, `/object_info`, and actual workflow submission behavior.
- [ ] Locate or export the two image API workflows; redact secrets from fixtures.

**Gate 0:** No guessed provider contract. If a service contract/workflow cannot be obtained, record the precise blocker; do not fake success.

## Phase 1 — Safe modular extraction

- [ ] Add environment-based configuration.
- [ ] Keep `app.py` as compatibility entrypoint.
- [ ] Extract database, API, task, asset, provider, and worker responsibilities.
- [ ] Add schema migration versioning and migration tests.
- [ ] Preserve existing task rows, uploads, outputs, and video workflows.
- [ ] Keep legacy routes operational.

**Gate 1:** Existing history and legacy API behavior pass against a migrated test database.

## Phase 2 — Unified task and asset core

- [ ] Add modality/provider/model/params/provider task ID/raw status/error code fields additively.
- [ ] Add assets table and safe storage service.
- [ ] Implement normalized state transitions.
- [ ] Implement provider protocol and registry.
- [ ] Add provider-scoped workers; concurrency defaults to 1 per provider.
- [ ] Add restart reconciliation without blindly failing all running tasks.
- [ ] Test migration, transitions, path safety, and legacy history.

**Gate 2:** Image/video tasks coexist in one history model; old rows remain intact.

## Phase 3 — H3 Video Native API

- [ ] Implement `minimax_h3_native` using the verified local contract only.
- [ ] Use server-side Bearer authentication.
- [ ] Implement T2V/I2V/FLF submit, poll, state mapping, result download, and asset registration.
- [ ] Store provider task ID and raw provider status.
- [ ] Keep old `/api/tasks*` routes as aliases to the same task service.
- [ ] Do not call video ComfyUI `8189` from the new provider.
- [ ] Add mock tests from redacted local fixtures.

**Gate 3:** All three video modes pass contract tests; real 104 test submits, polls, downloads, and displays a video.

## Phase 4 — H3 Image ComfyUI

- [ ] Obtain API-format 1MP and 4MP workflow JSON from 104.
- [ ] Verify against current ComfyUI `/object_info` and actual API execution.
- [ ] Add prompt marker and validate its location.
- [ ] Implement `/prompt`, `/history/{prompt_id}`, `/view` flow.
- [ ] Support T2I 1024×1024 and 2048×2048.
- [ ] Store image bytes and register assets.
- [ ] Add timeout/error and mock tests.

**Gate 4:** Both image presets pass mock tests and real generation on 104; preview/download works.

## Phase 5 — Unified frontend

- [ ] Keep the existing conversation-first layout.
- [ ] Add Image/Video mode switch.
- [ ] Add image resolution selector.
- [ ] Preserve video T2V/I2V/FLF selector, upload behavior, and 5-second indicator.
- [ ] Add image result preview/download cards.
- [ ] Preserve video player/download.
- [ ] Render unified status/history and restore after refresh.
- [ ] Keep responsive layout and accessible labels.

**Gate 5:** Manual browser smoke test for both modalities; refresh restores history.

## Phase 6 — Deployment and acceptance

- [ ] Add `.env.example` and 104 deployment guide.
- [ ] Back up `tasks.db`, `uploads/`, and `outputs/` before deployment.
- [ ] Confirm GPU/service map before testing.
- [ ] Start/update Studio only; do not restart inference services without authorization.
- [ ] Run 2 image and 3 video real generation tests.
- [ ] Verify assets/history survive Studio restart.
- [ ] Publish a report separating automated/mock and real tests.

**Gate 6:** `READY_FOR_USER_TRIAL` only when image 1MP, image 4MP, video T2V, I2V, and FLF all pass on 104 and results are previewable/downloadable.

## Delivery artifacts

- Updated source and tests.
- `.env.example`, `.gitignore`.
- `docs/architecture.md`, `docs/deployment-104.md`, `docs/provider-integration.md`, `docs/acceptance-report.md`.
- Updated README and troubleshooting.
- File-change summary, migration instructions, test results, real-service evidence, and unresolved blockers.
