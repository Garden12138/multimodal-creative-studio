# 104 部署说明

主机 `bjai-W580-G20` / `192.168.10.104`。Studio 只作为编排层。不要重装模型，不要改 GPU，不要在未获授权时重启或停止推理进程。

| GPU | 现有负载 | 约束 |
|---|---|---|
| GPU 0 | H3 Video | 只使用现有服务 |
| GPU 1 | AvatarChat | 不操作 |
| GPU 2 | H3 Image | 只使用现有服务 |

| 服务 | 本机地址 | Studio 是否直接调用 |
|---|---|---|
| H3 Video ComfyUI | `127.0.0.1:8189` | 是，视频 |
| H3 Native API | `127.0.0.1:8089` | 否。它会回调 Studio，页面任务不再提交给它 |
| H3 Image ComfyUI | `127.0.0.1:8190` | 是，图片 |
| Studio | `0.0.0.0:8088` | 浏览器入口 |

2026-11-11 从开发机只读看到：`:8089/openapi.json` 可访问但 `paths` 为空；`:8190/system_stats` 返回 ComfyUI `0.45.0`，启动参数是 `main.py --listen 0.0.0.0 --port 8190 --lowvram --reserve-vram 2`。进程内只看到一张卡，这与 `CUDA_VISIBLE_DEVICES=2` 时把物理 GPU 2 显示成 `cuda:0` 一致。这次没有执行 `nvidia-smi`，也没有改动该进程。现场参数里没有规格草稿中的 `--use-pytorch-cross-attention --disable-xformers`；PyTorch Attention 写在已成功执行的 workflow 节点里。不要为了对齐草稿而重启 8190。

## 启动

在部署机进入本仓库：

```bash
./scripts/start.sh --dry-run
./scripts/start.sh
```

`start.sh` 只部署 Studio，按这个顺序处理：

1. 探测 8089、8190、8189。Native API 和两套 ComfyUI 都保持现有启动方式，脚本不启动、不停止、不重启。
2. 如果本仓库 `.env` 里还没有 `VIDEO_NATIVE_API_KEY`，从 Native API 目录的 `.env` 读取 `H3_MINIMAX_API_KEY` 写入。脚本不打印密钥，也不改那份文件。
3. 本仓库还没有 `tasks.db`、而 `~/ai-services/h3-video-studio/tasks.db` 存在时，继续使用旧目录里的数据库、`uploads/` 和 `outputs/`。
4. 停掉 8088 上以前手动启动的 `uvicorn app:app --port 8088`。端口上如果是别的程序，脚本停止操作。
5. 备份将要使用的 `tasks.db`，然后启动 `uvicorn app:app --host 0.0.0.0 --port 8088 --workers 1`。

必须保持一个 worker。图片 worker 和视频 worker 都在这个进程里。日志写到 `logs/studio.log`。

```bash
./scripts/status.sh
./scripts/stop.sh
```

`stop.sh` 只停止 Studio。8089、8189、8190 保持原状。

目录不同时可以覆盖：

```bash
H3_NATIVE_DIR=~/ai-services/h3-minimax-native-v2 \
STUDIO_LEGACY_DIR=~/ai-services/h3-video-studio \
./scripts/start.sh
```

密钥只放在服务器的 `.env`。Studio 日志和 `/api/health` 不回传密钥。
