# H3 Video Studio · 内网轻量网页

此目录是完整的 FastAPI + HTML/JavaScript 应用，不依赖 Codex、GitHub 或 Hugging Face 网络。模型继续由原有 ComfyUI 加载。

## 第一步：准备 ComfyUI API 工作流

在原有 ComfyUI 界面打开已经成功的视频工作流，不要覆盖原版。新建临时副本，提示词改为 `__H3_STUDIO_PROMPT__`，保持固定的 FP8 / 4-step Turbo 等模型配置。

* 文生视频 T2V：两图片端口不连接，导出 API JSON 为 `workflows/t2v_api.json`。
* 图生视频 I2V：Load Image 选择服务器 `ComfyUI/input/STUDIO_FIRST.png`，连接 `first_frame`，导出 `workflows/i2v_api.json`。
* 首末帧 FLF：第二个 Load Image 选择 `STUDIO_LAST.png`，连接 `last_frame`，导出 `workflows/flf_api.json`。

必须使用 ComfyUI 菜单 File → Export Workflow (API)，不是普通 Save 导出。检查 `python check_workflows.py`，所有文件显示 OK 后再运行任务。

## 第二步：在服务器安装

```bash
cd ~/ai-services/h3-video-studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

如果 pip 报缺失 SOCKS support，先在安装命令前临时清除终端中的 ALL_PROXY/HTTP_PROXY/HTTPS_PROXY 环境变量，不要修改现有 AvatarChat 进程。

## 第三步：启动

ComfyUI 必须事先启动并保持正常。应用访问 ComfyUI `http://127.0.0.1:8189`，即使 ComfyUI 监听 `0.0.0.0:8189` 也能正常访问。

```bash
cd ~/ai-services/h3-video-studio
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8088 --workers 1
```

内网浏览器打开 `http://192.168.10.104:8088`。检查 `curl http://127.0.0.1:8088/api/health`。

## 说明

- 默认以三份已经验收的 API 工作流参数运行（0.2MP / 5秒 / 4 steps），网页不提供模型修改。
- 单 Worker 顺序排队，任务元数据写入 `tasks.db`，视频独立保存到 `outputs`。
- 若中途重启页面服务，正在运行的任务会标记为失败；ComfyUI 里的推理可能还在继续，重试前先检查 ComfyUI 队列。
- 8088 是无登录的局域网测试服务，仅在可信网段开放；若要对更广泛员工开放，建议增加认证。
- ComfyUI 原生 8189 管理端仍可能对内网开放，建议之后单独限制管理访问。
- 不要通过此应用删除现有 ComfyUI 模型、工作流或停止 AvatarChat。
