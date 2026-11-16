# Inference API 接入

契约来自 multimodal-inference-api `v1.0.0` 的实现，不是推测字段。本仓库不修改那个仓库。

## 调用

Studio 后端使用 `Authorization: Bearer <INFERENCE_API_KEY>`。浏览器不持有这把密钥。

| Studio | Inference API 1.0.0 |
|---|---|
| `POST /api/generate/image` | `POST /v1/images/generations` |
| `GET /api/tasks/image/{task_id}` | `GET /v1/images/generations/{task_id}` |
| `POST /api/generate/video` | `POST /v2/video_generation` |
| `GET /api/tasks/video/{task_id}` | `GET /v2/query/video_generation/{task_id}` |
| `GET /api/health` | `GET /v1/health` |
| `GET /api/tasks/image/{task_id}/file` | `GET /v1/files/{asset_id}` |
| `GET /api/tasks/video/{task_id}/file` | `GET /v2/files/{task_id}` |

图片创建体只有 `model=MiniMax-H3-Image`、`prompt`、`size`。`size` 为 `1024x1024` 或 `2048x2048`。成功创建时期望 HTTP 202。

视频创建体为 `model=MiniMax-H3`、一条文本、`resolution=768P`、`duration=5`。文生使用 `ratio=16:9`。图生和首末帧使用 `ratio=adaptive`，图片是 `data:image/...;base64`，角色为 `first_frame` 和 `last_frame`。成功创建时期望 HTTP 200 和 `task_id`。

查询结果里的签名 URL 不返回给浏览器。`file_ready` 只表示 Studio 可以代为下载。

## 下载校验

允许的路径只有 `/v1/files/{32位十六进制}` 和 `/v2/files/{32位十六进制}`。查询参数只能是 `expires` 和 64 位十六进制 `signature`。绝对地址的端口必须等于 `INFERENCE_API_BASE_URL` 的端口。实际请求改写到该配置地址，查询字符串保持不变，并且 `allow_redirects=false`。文件请求不附带 API Key，因为 1.0.0 的签名本身可以下载。

## 历史

`localStorage` 键 `studio.v2.history` 只保存 `task_id`、`media_type`、`mode`、`prompt`、`size`、`duration`、`created_at`。不保存状态、签名 URL 或 API Key。重新打开页面后，逐条查询 8090。8090 没有任务列表接口，因此不做服务端历史，也不迁移旧 SQLite。

## 已删除

ComfyUI 图片和视频 Provider、旧 8089 Native Provider、Provider Registry、任务 Worker、SQLite 任务状态、workflow 解析，以及旧的 `/api/tasks` 和 `/api/v1/tasks`。
