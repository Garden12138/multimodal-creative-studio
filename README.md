# Multimodal Creative Studio

本地图片 / 视频创作页面。浏览器只访问 Studio；Studio 再调用已经发布的 multimodal-inference-api 1.0.0。Studio 不加载模型，不管理 GPU，也不直接请求 ComfyUI。

```text
Browser -> Studio :8088 -> Inference API :8090 -> ComfyUI :8189 / :8190
```

旧的 Native API `:8089` 不再被 Studio 调用。

## 本地检查

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -t .
```

2026-11-16 在本仓库执行：`unittest` 28 项通过。这是 mock 测试，不是 104 真生成。页面验收记录见 `docs/studio-v2-acceptance.md`。

## 配置

还没有 `.env` 时，复制 `.env.example`。104 上如果已经有旧 `.env`，不要用 `cp -n` 覆盖；把 `INFERENCE_API_BASE_URL` 和 `INFERENCE_API_KEY` 补进去。`./scripts/start.sh` 会在没有 `.env` 时从示例生成，并给旧文件补上缺失的键，但不会从 8089 复制密钥，也不会打印密钥。

`INFERENCE_API_BASE_URL` 默认是 `http://127.0.0.1:8090`。密钥只留在 Studio 后端。

## 启动

```bash
./scripts/start.sh --dry-run
./scripts/start.sh
```

脚本只管理 Studio。它会探测 8090、8089、8189、8190，但不启动、不停止、不重启这些进程。104 上的现有 Python 可以用 `STUDIO_PYTHON` 指定，避免为了页面再装一套模型环境。

```bash
./scripts/status.sh
./scripts/stop.sh
```

浏览器打开 `http://127.0.0.1:8088`。104 上是 `http://192.168.10.104:8088`。

历史记录保存在浏览器 `localStorage`，键名 `studio.v2.history`。刷新后用任务编号向 8090 查询真实状态。旧的 Studio SQLite 不迁移。

接入细节见 `docs/inference-api-integration.md`，104 操作见 `docs/deployment-104.md`。
