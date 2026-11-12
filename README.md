# Multimodal Creative Studio

本地图片 / 视频创作工作台。Studio 负责任务、历史、预览和下载；模型仍由 104 上已经在跑的服务加载。

- 图片：`comfyui_h3_image`，默认 `http://127.0.0.1:8190`，1024×1024 与 2048×2048。
- 视频：页面任务直接提交到视频 ComfyUI，默认 `http://127.0.0.1:8189`。8089 仍按原有方式单独运行；Studio 不再把页面任务提交给它，避免它回调 Studio 后两边互相等待。
- 技术栈保持 FastAPI、SQLite、原生 HTML/CSS/JS。入口仍是 `uvicorn app:app`。

## 本地检查

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python check_workflows.py
python -m unittest discover -s tests -t .
```

2026-11-11 在本仓库执行：`check_workflows.py` 五份工作流均为 OK；`unittest` 15 项通过。这些是本地和 mock 测试，不是 104 真生成。

## 配置

`./scripts/start.sh` 会在没有 `.env` 时从 `.env.example` 生成，并从 Native API 的环境文件写入 `VIDEO_NATIVE_API_KEY`。不要把 `.env` 提交到 Git，也不要把密钥写进前端或日志。

`COMFY_URL` 不再被新的视频链路读取。V1 不允许把 `VIDEO_PROVIDER` 改成别的值来退回 `:8189`。

## 启动

在 104 上进入本仓库后执行：

```bash
./scripts/start.sh
```

脚本只管理 Studio。8088 上如果还有以前手动启动的 `uvicorn app:app --host 0.0.0.0 --port 8088 --workers 1`，会先停掉它，再以后台方式启动新的 Studio，同样是 `--workers 1`。

8089、8189、8190 保持各自现有的启动方式。脚本只探测这三路，不启动、不停止、不重启。

查看和停止：

```bash
./scripts/status.sh
./scripts/stop.sh
```

`./scripts/stop.sh` 只停止 Studio。浏览器打开 `http://192.168.10.104:8088`。

先看会做什么：

```bash
./scripts/start.sh --dry-run
```

健康检查只说明端口和配置是否可读，不能代替一次真实生成。操作细节见 `docs/deployment-104.md`，未执行的真实验收见 `docs/acceptance-report.md`。

## 数据

升级会给现有 `tasks` 表追加列，并新增 `assets` 表。旧行、`uploads/`、`outputs/` 和三份视频 workflow 都会保留。已经拿到上游任务号的运行中任务会在重启后继续查询；没有上游任务号的任务会标为失败，且不会自动重新提交。
