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

这次只替换 8088 上的 Studio。下面每一步都在 `~/ai-services/multimodal-creative-studio` 执行。不要使用 Miniconda base 的 `python3`，也不要使用仓库里的旧 `.venv`。Studio 用原来的 Conda 环境 `h3-video-studio`。

不要停止 8090、8089、8189、8190。不要把密钥打印到终端，也不要执行 `cat .env`。

### 1. 对齐代码

```bash
cd ~/ai-services/multimodal-creative-studio
git fetch origin
git checkout feature/inference-api-integration
git pull --ff-only origin feature/inference-api-integration
git rev-parse --short HEAD
```

当前说明对应的提交从 `cec1367` 开始。更早的提交里，`start.sh` 仍可能优先选中 `.venv`。

### 2. 固定 Conda 解释器

```bash
unset STUDIO_PYTHON
export STUDIO_PYTHON="$HOME/miniconda3/envs/h3-video-studio/bin/python"
test -x "$STUDIO_PYTHON"
"$STUDIO_PYTHON" -c 'import fastapi,uvicorn,requests,multipart,PIL,httpx; print("python-ok")'
```

这里必须打印 `python-ok`。如果失败，停下来，不要改用 `.venv` 或 base `python3`。

可选的本地 mock 测试：

```bash
"$STUDIO_PYTHON" -m unittest discover -s tests -t .
```

这 28 项通过只说明代码和依赖可用，不是 104 真生成。

### 3. 把 8090 的密钥写入 Studio `.env`

机器上已经有旧 `.env`。不要执行 `cp .env.example .env` 或 `cp -n .env.example .env`，否则会留下一份没有密钥的旧文件。

Studio 需要这两个值：

```dotenv
INFERENCE_API_BASE_URL=http://127.0.0.1:8090
INFERENCE_API_KEY=<与正在运行的 8090 相同>
```

密钥来自 8090 进程工作目录里的 `.env`。Inference API 优先读 `INFERENCE_API_KEY`；这个值为空时才使用 `H3_MINIMAX_API_KEY`。下面的脚本只复制实际生效的那一个，并写回 Studio 的 `.env`。它打印长度，不打印密钥。

```bash
pid=$(ss -ltnp | sed -n 's/.*:8090 .*pid=\([0-9][0-9]*\).*/\1/p' | head -n 1)
test -n "$pid"
API_DIR=$(readlink -f "/proc/${pid}/cwd")
echo "8090 目录：$API_DIR"
test -f "$API_DIR/.env"

"$STUDIO_PYTHON" - "$API_DIR" <<'PY'
import sys
from pathlib import Path

api_dir = Path(sys.argv[1])
studio = Path(".env")

def parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values

source = parse(api_dir / ".env")
key = source.get("INFERENCE_API_KEY") or source.get("H3_MINIMAX_API_KEY") or ""
if len(key) < 24:
    raise SystemExit("8090 的 .env 里没有可用密钥")

lines = studio.read_text(encoding="utf-8").splitlines() if studio.is_file() else []

def upsert(items: list[str], name: str, value: str) -> list[str]:
    output: list[str] = []
    found = False
    for item in items:
        if item.startswith(name + "="):
            output.append(f"{name}={value}")
            found = True
        else:
            output.append(item)
    if not found:
        if output and output[-1] != "":
            output.append("")
        output.append(f"{name}={value}")
    return output

lines = upsert(lines, "INFERENCE_API_BASE_URL", "http://127.0.0.1:8090")
lines = upsert(lines, "INFERENCE_API_KEY", key)
studio.write_text("\n".join(lines) + "\n", encoding="utf-8")
studio.chmod(0o600)
print("base_url http://127.0.0.1:8090")
print("api_key present")
print("api_key_length", len(key))
PY
```

然后用这把密钥访问 8090，确认它确实被接受。输出里只有状态码，没有密钥：

```bash
"$STUDIO_PYTHON" - <<'PY'
from pathlib import Path
import requests

values = {}
for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    values[key.strip()] = value.strip().strip("\"'")
base = values["INFERENCE_API_BASE_URL"].rstrip("/")
session = requests.Session()
session.trust_env = False
response = session.get(
    base + "/v1/health",
    headers={"Authorization": "Bearer " + values["INFERENCE_API_KEY"]},
    timeout=10,
)
print("v1_health", response.status_code)
if response.status_code == 200:
    print("inference", response.json().get("status"))
PY
```

`v1_health` 必须是 `200`。如果是 `401`，说明写入的密钥和正在运行的 8090 不一致，不要启动 Studio。

### 4. 启动

```bash
STUDIO_PYTHON="$STUDIO_PYTHON" ./scripts/start.sh --dry-run
STUDIO_PYTHON="$STUDIO_PYTHON" ./scripts/start.sh
curl -fsS --noproxy '*' http://127.0.0.1:8088/api/health
echo
```

`--dry-run` 只检查，不停止进程，也不改 `.env`。正式的 `start.sh` 只替换 8088 上的 `uvicorn app:app`。健康检查应显示 `"web":"ok"`，并且 `inference` 不是 `misconfigured`。

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
