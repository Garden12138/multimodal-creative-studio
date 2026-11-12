# Cursor Agent Prompt — Multimodal Creative Studio V1

你负责在当前打开的仓库实现 **Multimodal Creative Studio V1**。仓库：`Garden12138/multimodal-creative-studio`。

开始前完整阅读：

- `spec/00-constitution.md`
- `spec/01-product-spec.md`
- `spec/02-architecture-plan.md`
- `spec/03-api-contract.md`
- `spec/04-implementation-plan.md`
- `docs/104-integration-and-acceptance.md`

这些文件是产品和工程约束。冲突时优先遵守 `00-constitution.md`。

## 0. 最终目标

V1 必须真正接入 104 机器两个现有服务：

1. **H3 Video**：Studio → `127.0.0.1:8089` Native API → `127.0.0.1:8189` ComfyUI；支持 T2V、I2V、FLF。
2. **H3 Image**：Studio → `127.0.0.1:8190` ComfyUI；支持 T2I 1024×1024、2048×2048。

不能只做 UI、不能只做 mock、不能把图片能力推迟到下一版本。

GPU 固定：GPU 0=H3 Video，GPU 1=AvatarChat（严禁触碰），GPU 2=H3 Image。不得重装模型服务，不得改变 GPU 分配，不得未经授权重启/停止 104 上的推理进程。

## 1. 工作约束

- 先检查仓库、分支和 Git 状态，不覆盖用户未提交修改。
- 不重写成 React/Vue，不更换技术栈；保留 FastAPI、SQLite、原生 HTML/CSS/JS。
- 保持 `uvicorn app:app` 入口和 8088 部署方式可用。
- 渐进式重构，先测试和兼容，再切 Provider。
- 不为目录整洁而搬动所有文件。
- 不删除旧 DB、历史、上传、输出、视频 workflow。
- 不提交、不 Push，除非用户另行要求。
- 不修改仓库以外文件；对 104 机器只读检查，真实生成测试须得到用户授权。
- 每阶段运行测试并记录证据；不要只输出计划，必须实际实现代码、测试和文档。

## 2. Gate 0：核实服务合同（禁止猜测）

### 2.1 Video Native API

先尝试访问：

- `http://192.168.10.104:8089/openapi.json`
- `http://192.168.10.104:8089/docs`

如本机无法访问，检查当前 SSH 配置是否可连接 `bjai-W580-G20`，只读读取：

`~/ai-services/h3-minimax-native-v2/docs/AGENT_INTEGRATION.md`

必要时检查 `server.py`、`minimax_v2_api.py`。

已知路由族：

- `POST /v2/video_generation`
- `GET /v2/query/video_generation/{task_id}`
- `GET /v2/query/video_generation`
- `DELETE /v2/video_generation/{task_id}`

已知认证：`Authorization: Bearer <API_KEY>`。已知请求使用 `content[]`，支持 text、first-frame、last-frame；完成结果 URL 位于 `task.content.url`。

**以上信息不足以推导完整 JSON。** 必须核实每种 content item 的精确 type/字段、必填字段、模型名、任务状态、查询响应层级、结果下载鉴权和错误结构。将脱敏契约样例放入 `docs/contracts/h3-native-api-fixtures.md`。不允许从云端 API 猜字段。

如果无法取得准确契约：记录 `VIDEO_NATIVE_CONTRACT_UNVERIFIED`，继续完成不依赖该合同的数据库、任务核心、图片 Provider、mock 测试和 UI；但不得宣称视频 Native API 完成或 V1 验收通过。

### 2.2 Image ComfyUI

检查 104 的 `http://192.168.10.104:8190/system_stats` 和 `/object_info`。如 SSH 可用，在 `~/ai-services/minimax-h3-image/ComfyUI` 范围内只读查找现有工作流。

当前仓库没有图像 workflow JSON。不要把普通 UI JSON 当 API JSON，也不要虚构工作流。必须取得 1MP/4MP 的 `File → Export Workflow (API)` 文件，放入：

- `workflows/image/t2i_1mp_api.json`
- `workflows/image/t2i_4mp_api.json`

验证 API JSON、prompt marker、模型/量化/VAE/attention/sampling 设置和输出节点；通过运行中的 8190 服务实际执行验证。正向提示词使用唯一标记 `__H3_IMAGE_STUDIO_PROMPT__`。不确定文件路径或节点 ID 时先查证，不得猜测。

图像已验证基线：ComfyUI 0.45.0、GPU 2、W4A8 DiT + NVFP4-AWQ Qwen3-VL + FP16 VAE、PyTorch Attention、PE off、6 steps、CFG 1。不要修改这些参数。

## 3. Phase 1：基线和清理

1. 阅读 `app.py`、`index.html`、全部 workflow、README、requirements。
2. 运行 `python check_workflows.py`。
3. 记录现有路由和 SQLite schema。当前任务表含 `id/mode/prompt/state/created/started/finished/comfy_prompt_id/first_image/last_image/video/error` 等字段。
4. 增加 `.gitignore`，排除 `.DS_Store`、`.env`、`.venv`、`__pycache__`、SQLite 临时文件、日志、uploads、outputs。
5. 删除已确认的 `.DS_Store`。
6. 保留三份视频 workflow JSON 原样。
7. 为旧 API/DB 行为建立测试。

## 4. Phase 2：模块化和统一任务核心

按 architecture spec 建立 config、database/migrations、task/asset model、services、provider protocol/registry、API routes、workers。保持 `app.py` 兼容入口。

- 对现有 SQLite 做 additive migration；保留旧表、旧列、历史和输出。
- 新增 media/provider/model/params/provider_task_id/provider_status/error_code 等字段及 assets 表。
- 统一状态机；未知 provider 状态不能变成 succeeded。
- Studio task ID 与 provider task ID 分开存储。
- 增加文件路径安全、SHA-256、MIME 校验。
- 图片和视频使用独立 provider worker/concurrency，默认每个 provider 同时最多一个任务。
- 重启恢复不能无条件把所有 running 任务标为 failed。

## 5. Phase 3：Video Native API Provider

实现 `minimax_h3_native`：

- 只调用 8089；Bearer Key 仅后端保存。
- 按真实本地契约实现 T2V/I2V/FLF。
- 保存 provider task ID 和原始状态；轮询；下载完成结果至 Studio outputs；登记 asset。
- 处理超时、网络错误、认证错误、拒绝任务、缺失结果 URL。
- 不打印 API Key。
- 不允许静默回退到视频 ComfyUI 8189。
- 保留 `/api/tasks*` 兼容路由，但映射到统一 Task Service，不得保留第二套队列。
- 用真实契约的脱敏 fixture 写 mock tests。

## 6. Phase 4：Image ComfyUI Provider

实现 `comfyui_h3_image`：

- 调用 8190；根据 resolution 选择 1MP/4MP API workflow。
- 替换唯一 prompt placeholder；提交 `/prompt`；轮询 `/history/{prompt_id}`。
- 从 history 输出元数据获取图片，通过 `/view` 下载 bytes。
- 保存至 Studio outputs 并登记 asset；支持预览和下载。
- 处理 workflow 缺失/无效、无 prompt_id、ComfyUI error、空输出、下载失败、超时。
- 不硬编码未经当前 workflow 验证的 node ID。

## 7. Phase 5：统一 API 和 UI

实现 `spec/03-api-contract.md` 中的：

- `POST /api/v1/tasks`
- `GET /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/assets/{asset_id}/content`

`/api/health` 分别报告 DB、Video Native API、Image ComfyUI、两个 image workflow 的状态。

前端保留对话式体验：

- 一级切换：图片 / 视频。
- 图片：prompt + 1024×1024 / 2048×2048 选择。
- 视频：保留 T2V / I2V / FLF 选择、对应图片上传、5 秒视频展示。
- 统一显示状态和历史；刷新后恢复。
- 图片结果卡片展示预览和下载；视频结果保留播放器和下载。
- V1 不添加 Music3 假入口。

## 8. Phase 6：测试和文档

必须补齐 DB migration、task state、Video Native API fixture、Image workflow marker、ComfyUI history parsing、asset download/path safety、upload validation、API validation、legacy API compatibility、health 和前端 smoke tests。

Mock 测试与 104 真实测试分开报告。更新 README、架构、104 部署、Provider 集成、验收报告。

## 9. 104 真实验收

只有得到用户授权后才执行真实生成。未经授权不重启/停止推理服务。真实验收覆盖：

1. H3 Image 1024×1024。
2. H3 Image 2048×2048。
3. H3 Video T2V。
4. H3 Video I2V。
5. H3 Video FLF。
6. 图片/视频历史、预览、下载。
7. Studio 重启后历史和资产仍存在。

如果无法操作 104，只完成本地代码和 mock 验收，提供可复制的真实验收命令，报告必须标记真实验收 `NOT RUN`，不能写 PASS。

## 10. 最终报告

输出代码改造摘要、文件变更清单、DB 迁移说明、`.env` 配置、测试命令与真实结果、104 验收命令、启动和使用步骤、未解决阻塞。

最终状态只能是：`READY_FOR_USER_TRIAL`、`NEEDS_FIXES`、`BLOCKED_BY_PROVIDER_CONTRACT`、`BLOCKED_BY_104_SERVICE`。

只有图片两个分辨率和视频三个模式全部真实通过，且结果可预览/下载，才允许 `READY_FOR_USER_TRIAL`。不能因为代码能启动或 mock tests 通过就宣布 V1 完成。
