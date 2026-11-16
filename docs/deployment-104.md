# 104 部署说明

主机 `192.168.10.104`。这次只更新 Studio 页面层。不要改 ComfyUI workflow、模型、GPU，也不要在验收完成前停止 8090、8189、8190。

| 服务 | 地址 | Studio |
|---|---|---|
| Studio | `0.0.0.0:8088` | 浏览器入口 |
| Inference API | `127.0.0.1:8090` | 唯一的上游 |
| 视频 ComfyUI | `127.0.0.1:8189` | 不直接调用 |
| 图片 ComfyUI | `127.0.0.1:8190` | 不直接调用 |
| 旧 Native API | `127.0.0.1:8089` | 不再调用 |

Inference API 的 `INFERENCE_PUBLIC_BASE_URL` 可以仍是 `http://192.168.10.104:8090`。Studio 的 `INFERENCE_API_BASE_URL` 使用 `http://127.0.0.1:8090`。视频签名 URL 上的局域网地址会被改写到本机 8090，签名查询保持不变。

## 部署 Studio

在 104 上使用现有 Python，不要为了页面重新安装模型。下面的目录按机器上的实际检出位置替换。`./scripts/start.sh` 只会替换 8088 上的 `uvicorn app:app`，不会停止其他端口。

104 上直接执行 `python3` 会进入 Miniconda base，这个环境没有 `fastapi` 和 `Pillow`。不要用它跑测试，也不要往 base 里安装依赖。仓库里的 `.venv`，或原来的 `h3-video-studio` Conda 环境，才是 Studio 用过的解释器。

已有 `.env` 时不要执行 `cp -n .env.example .env`。`-n` 会保留旧文件，旧文件里没有 `INFERENCE_API_BASE_URL` 和 `INFERENCE_API_KEY`。

```bash
cd ~/ai-services/multimodal-creative-studio
git fetch origin
git checkout feature/inference-api-integration

STUDIO_PYTHON=""
for candidate in \
  .venv/bin/python \
  "$HOME/miniconda3/envs/h3-video-studio/bin/python" \
  "$HOME/miniconda3/envs/multimodal-inference-api/bin/python"
do
  if [[ -x "$candidate" ]] && "$candidate" -c 'import fastapi,uvicorn,requests,multipart,PIL,httpx' >/dev/null 2>&1; then
    STUDIO_PYTHON=$candidate
    break
  fi
done
if [[ -z "$STUDIO_PYTHON" ]]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  STUDIO_PYTHON=$PWD/.venv/bin/python
fi
"$STUDIO_PYTHON" -m unittest discover -s tests -t .

# 给旧 .env 补上缺失的键，不覆盖已经写过的值，也不打印密钥。
grep -q '^INFERENCE_API_BASE_URL=' .env || printf '\nINFERENCE_API_BASE_URL=http://127.0.0.1:8090\n' >> .env
grep -q '^INFERENCE_API_KEY=' .env || printf 'INFERENCE_API_KEY=\n' >> .env
chmod 600 .env
```

然后用编辑器把 `INFERENCE_API_KEY` 填成 8090 服务自己的密钥。先看 8090 进程的工作目录，不要把密钥打到终端：

```bash
pid=$(ss -ltnp | sed -n 's/.*:8090 .*pid=\([0-9][0-9]*\).*/\1/p' | head -n 1)
readlink -f "/proc/${pid}/cwd"
```

那个目录的 `.env` 里有 `INFERENCE_API_KEY`。复制到 Studio 的 `.env` 后，只检查是否已填写：

```bash
python3 - <<'PY'
from pathlib import Path
vals = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    vals[key.strip()] = value.strip().strip("\"'")
print("base_url", vals.get("INFERENCE_API_BASE_URL") or "MISSING")
print("api_key", "present" if vals.get("INFERENCE_API_KEY") else "MISSING")
PY

STUDIO_PYTHON="$STUDIO_PYTHON" ./scripts/start.sh --dry-run
STUDIO_PYTHON="$STUDIO_PYTHON" ./scripts/start.sh
```

然后打开 `http://192.168.10.104:8088`，按 `docs/studio-v2-acceptance.md` 的页面清单操作。每次提交只应在 8090 产生一个任务。

停止页面：

```bash
./scripts/stop.sh
```

这只停止 Studio。

## 旧 8089 下线

只有 10 项页面验收都通过之后才做。先看清端口，不要对 8090、8189、8190 发信号，也不要删除旧目录。

```bash
ss -ltnp | awk '/:8088|:8089|:8090|:8189|:8190/'
```

确认 8089 的命令行属于 `~/ai-services/h3-minimax-native-v2`，并且不是 `inference.runtime` 或 Studio。然后只停止那个进程：

```bash
cd ~/ai-services/h3-minimax-native-v2
if [[ -x ./scripts/stop.sh ]]; then
  ./scripts/stop.sh
else
  # 把 PID 换成上一则 ss 里已经核对过的 8089 进程。
  kill -TERM <已核对的 8089 PID>
fi
ss -ltnp | awk '/:8089|:8090|:8189|:8190/'
```

下线后 8090、8189、8190 仍应保持监听。再打开 Studio 做一次图片和一次视频，确认页面没有回到 8089。

回退时不要恢复 Studio 的旧代码，除非页面本身也要退回。只把旧 API 按它原来的方式重新启动：

```bash
cd ~/ai-services/h3-minimax-native-v2
if [[ -x ./scripts/start.sh ]]; then
  ./scripts/start.sh
else
  # 使用该目录原来的启动命令，而不是 Studio 的 start.sh。
  true
fi
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8089/v2/health
```

如果 Studio 页面也要退回旧实现，在 Studio 目录切回部署前的提交再执行 `./scripts/start.sh`。不要删除 `h3-minimax-native-v2` 目录。

## 页面操作

1. 打开 `http://192.168.10.104:8088`。页头应能显示图片和视频状态。
2. 切到图片，分别用 1024×1024 和 2048×2048 各生成一张，确认预览和下载。
3. 切到视频。文生视频直接提交。图生视频上传一张参考图。首末帧上传两张图。三种都要能播放和下载。
4. 刷新页面。刚才的任务还在，并且状态来自 8090，而不是本地缓存的“已完成”。
5. 查看页面源代码和网络请求，确认没有 `INFERENCE_API_KEY`，也没有发往 8189、8190、8089 的请求。
6. 同一次点击只产生一个 8090 任务。失败或“提交状态不确定”时不要再点一次，除非确实要新建另一个任务。
