# 架构说明

Studio 是 104 上现有推理服务前面的任务与素材层。它不加载模型，也不改 GPU 分配。

```text
Browser
  |
  v
FastAPI Studio  0.0.0.0:8088
  |
  +-- /api/v1/tasks 与 /api/tasks
  +-- SQLite tasks / assets
  +-- 图片 worker，同一时刻 1 个任务，GPU 2 上的 ComfyUI :8190
  +-- 视频 worker，同一时刻 1 个任务，Native API :8089
```

视频 worker 只请求 `:8089`。图片 worker 只请求 `:8190`。两个队列互不等待。

## 代码布局

`app.py` 继续导出 `app`，供 `uvicorn app:app` 使用。实现位于 `studio/`：

- `config.py`：环境变量。
- `database.py`：建表、追加迁移、重启对账。
- `services/task_service.py`：图片和视频共用的校验与记录。
- `services/worker.py`：按 provider 各跑一个线程。
- `providers/video/minimax_h3_native.py`：视频 Native API。
- `providers/image/comfyui_h3_image.py`：图片 ComfyUI。
- `api/routes.py`：`/api/v1/*`、旧 `/api/tasks*` 和 `/api/health`。

`index.html` 仍是原生页面。一级切换是图片 / 视频；历史来自 `GET /api/v1/tasks`。

## 改造前的行为

改造前 `app.py` 直接请求 `COMFY_URL`（默认 `127.0.0.1:8189`），路由为：

- `GET /`
- `GET /api/health`
- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/video`

旧表列为 `id`、`mode`、`prompt`、`state`、`created`、`started`、`finished`、`comfy_prompt_id`、`first_image`、`last_image`、`video`、`error`。服务启动时会把所有 `running` 行改成失败。

## 现在的数据库

迁移只追加，不删除旧表和旧列。新增：

- 任务列：`media_type`、`provider`、`model`、`params_json`、`provider_task_id`、`provider_status`、`error_code`
- `assets`：`id`、`task_id`、`media_type`、`filename`、`mime_type`、`size_bytes`、`sha256`、`storage_key`、`created_at`
- `schema_migrations`

旧的空 `media_type` 会填成 `video`。已成功或失败、且带有 Comfy prompt id 或视频路径的旧行标记为 `legacy_comfyui_video`，只用于展示，没有对应 worker。仍在排队、且没有 provider 的旧视频任务改由 `minimax_h3_native` 接手。

状态只允许：

```text
queued -> submitting -> running -> succeeded
                      \-> failed
```

`submitting` / `running` 在重启时：

- 已有 `provider_task_id`：继续查询，不重新提交。`submitting` 会先改成 `running`。
- 没有 `provider_task_id`：标为失败，原因是无法继续跟踪，不自动重试。

未知的上游状态不会写成 `succeeded`。

## 素材

上传图片会检查声明的 MIME、实际图像、12MB 上限、边长 256–5760 和宽高比 0.4–2.5，再存成 `uploads/` 下的 PNG。生成文件写入 `outputs/`，对外只给 asset id。读取时路径必须落在配置的目录内。

## 健康检查

`GET /api/health` 分开报告数据库、视频 Native API、图片 ComfyUI，以及 1MP / 4MP 两份 workflow 是否通过本地校验。端口能打开不等于生成成功。
