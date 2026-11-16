# 架构说明

Studio 是 Inference API 前面的页面和代理。它不加载模型，不解析 ComfyUI workflow，也不保存生成文件。

```text
Browser
  |
  v
Studio :8088
  |
  +-- POST /api/generate/image
  +-- POST /api/generate/video
  +-- GET  /api/tasks/{media_type}/{task_id}
  +-- GET  /api/tasks/{media_type}/{task_id}/file
  |
  v
Inference API :8090
  |
  +-- ComfyUI 图片 :8190
  +-- ComfyUI 视频 :8189
```

Studio 不请求 `:8189`、`:8190` 或旧的 `:8089`。

## 代码

`app.py` 仍导出 `app`，由 `uvicorn app:app` 启动。

- `studio/config.py`：读取 `INFERENCE_API_BASE_URL` 和 `INFERENCE_API_KEY`。
- `studio/inference_client.py`：按 multimodal-inference-api 1.0.0 的源码路径创建和查询任务，并校验下载地址。
- `studio/validation.py`：提示词、分辨率和参考图校验。
- `studio/api/routes.py`：上表中的页面接口。
- `index.html`：原有对话页面。任务索引放在 `localStorage`。

没有任务数据库，也没有后台 worker。一次页面提交只向 8090 发一次创建请求。`needs_review` 后页面停止轮询，不会自动再创建。

## 状态

8090 返回的 `submitting` 在 Studio 里显示为 `running`。页面只使用这些状态：

| 状态 | 页面 |
|---|---|
| queued | 排队中 |
| running | 生成中 |
| succeeded | 已完成 |
| failed | 失败 |
| needs_review | 提交状态不确定，停止轮询 |

未知状态不会显示成成功。

## 下载

图片成功响应里的 `assets[].url` 是相对路径。视频成功响应里的 `task.content.url` 是绝对路径，104 上可能写成 `192.168.10.104:8090`。Studio 核对路径、端口和签名参数后，改用 `INFERENCE_API_BASE_URL` 取文件，并保留原始查询字符串。签名不覆盖 Host。

这个请求禁止自动重定向。不匹配的端口、路径或其他来源会被拒绝，不会由 Studio 发出。浏览器只访问 Studio 的 `/file` 代理，每次下载都会先向 8090 取一次新的签名地址。生成文件不写入 Studio 的 `outputs/`。
