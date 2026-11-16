# Shared helpers for the Studio start/stop/status scripts.
# This file is sourced. It does not start or stop anything by itself.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "请运行 scripts/start.sh、scripts/stop.sh 或 scripts/status.sh" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFERENCE_PORT=8090
NATIVE_PORT=8089
IMAGE_COMFY_PORT=8190
VIDEO_COMFY_PORT=8189

say() {
  printf '%s\n' "$*"
}

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

redact_stream() {
  sed -E \
    -e 's/(Bearer[[:space:]]+)[^[:space:]]+/\1***/g' \
    -e 's/([A-Za-z0-9_]*(API_KEY|TOKEN|SECRET)[=:][[:space:]]*)[^[:space:]]+/\1***/g'
}

http_code() {
  local url="$1" code="000"
  if ! command -v curl >/dev/null 2>&1; then
    printf '000'
    return 0
  fi
  code="$(curl -sS -o /dev/null -w '%{http_code}' --noproxy '*' --connect-timeout 2 --max-time 5 "$url" 2>/dev/null || true)"
  if [[ ! "$code" =~ ^[0-9]{3}$ ]]; then
    code="000"
  fi
  printf '%s' "$code"
}

process_cmdline() {
  local pid="$1"
  if [[ -r "/proc/${pid}/cmdline" ]]; then
    tr '\0' ' ' < "/proc/${pid}/cmdline"
    return 0
  fi
  ps -p "$pid" -o args= 2>/dev/null || true
}

listener_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NF && !seen[$0]++'
    return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -lptnH "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | awk 'NF && !seen[$0]++'
    return 0
  fi
  die "需要 lsof 或 ss 才能检查端口 ${port}"
}

is_protected_command() {
  local cmd="$1"
  [[ "$cmd" == *"--port ${INFERENCE_PORT}"* || "$cmd" == *"--port=${INFERENCE_PORT}"* \
    || "$cmd" == *"--port ${NATIVE_PORT}"* || "$cmd" == *"--port=${NATIVE_PORT}"* \
    || "$cmd" == *"--port ${IMAGE_COMFY_PORT}"* || "$cmd" == *"--port=${IMAGE_COMFY_PORT}"* \
    || "$cmd" == *"--port ${VIDEO_COMFY_PORT}"* || "$cmd" == *"--port=${VIDEO_COMFY_PORT}"* \
    || "$cmd" == *"inference.runtime"* || "$cmd" == *"h3-minimax"* || "$cmd" == *"comfy"* || "$cmd" == *"main.py"* ]]
}

is_studio_command() {
  local cmd="$1"
  [[ "$cmd" == *uvicorn* && "$cmd" == *app:app* ]] && ! is_protected_command "$cmd"
}

kill_tree() {
  local pid="$1" signal="$2" child
  local children
  children="$(pgrep -P "$pid" 2>/dev/null || true)"
  for child in $children; do
    kill_tree "$child" "$signal"
  done
  kill "-${signal}" "$pid" 2>/dev/null || true
}

stop_pid() {
  local pid="$1"
  local waited=0
  kill_tree "$pid" TERM
  while [[ "$waited" -lt 20 ]]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.5
    waited=$((waited + 1))
  done
  kill_tree "$pid" KILL
}

stop_studio_on_port() {
  local port="$1"
  local pids pid cmd
  pids="$(listener_pids "$port" || true)"
  if [[ -z "$pids" ]]; then
    say "端口 ${port} 当前没有监听进程。"
    return 0
  fi
  for pid in $pids; do
    [[ "$pid" =~ ^[0-9]+$ ]] || die "无法识别端口 ${port} 的进程号。"
    [[ "$pid" -gt 1 ]] || die "拒绝操作系统进程 ${pid}。"
    cmd="$(process_cmdline "$pid")"
    if ! is_studio_command "$cmd"; then
      die "端口 ${port} 被其他进程占用，已停止操作：pid=${pid} ${cmd}"
    fi
    say "停止已有 Studio：pid=${pid} ${cmd}"
    if [[ "${DRY_RUN:-0}" == 1 ]]; then
      continue
    fi
    stop_pid "$pid"
  done
  if [[ "${DRY_RUN:-0}" == 1 ]]; then
    return 0
  fi
  pids="$(listener_pids "$port" || true)"
  if [[ -n "$pids" ]]; then
    die "端口 ${port} 仍被占用：${pids}"
  fi
}

probe_independent_services() {
  local inference_code native_code image_code video_code
  inference_code="$(http_code "http://127.0.0.1:${INFERENCE_PORT}/healthz")"
  native_code="$(http_code "http://127.0.0.1:${NATIVE_PORT}/v2/health")"
  image_code="$(http_code "http://127.0.0.1:${IMAGE_COMFY_PORT}/system_stats")"
  video_code="$(http_code "http://127.0.0.1:${VIDEO_COMFY_PORT}/system_stats")"
  say "Inference API ${INFERENCE_PORT}：HTTP ${inference_code}。Studio 只调用这一路。"
  say "旧 Native API ${NATIVE_PORT}：HTTP ${native_code}。Studio 不再调用，脚本不启动、不停止。"
  say "图片 ComfyUI ${IMAGE_COMFY_PORT}：HTTP ${image_code}。由 Inference API 使用，Studio 不直接调用，脚本不启动、不停止。"
  say "视频 ComfyUI ${VIDEO_COMFY_PORT}：HTTP ${video_code}。由 Inference API 使用，Studio 不直接调用，脚本不启动、不停止。"
  if [[ "$inference_code" != "200" ]]; then
    say "8090 目前没有响应。生成前需要 Inference API 已经按原有方式启动。"
  fi
}

python_can_import() {
  local py="$1"
  [[ -x "$py" ]] || return 1
  "$py" -c 'import fastapi, uvicorn, requests, multipart, PIL, httpx' >/dev/null 2>&1
}

find_conda_python() {
  local bases=() base
  if command -v conda >/dev/null 2>&1; then
    base="$(conda info --base 2>/dev/null || true)"
    [[ -n "$base" ]] && bases+=("$base")
  fi
  bases+=(
    "$HOME/miniconda3"
    "$HOME/anaconda3"
    "$HOME/mambaforge"
    "$HOME/miniforge3"
    "$HOME/.conda"
    "/opt/conda"
  )
  for base in "${bases[@]}"; do
    if [[ -x "${base}/envs/h3-video-studio/bin/python" ]]; then
      printf '%s\n' "${base}/envs/h3-video-studio/bin/python"
      return 0
    fi
  done
  return 1
}

select_python() {
  local conda_py=""
  if [[ -n "${STUDIO_PYTHON:-}" ]] && python_can_import "$STUDIO_PYTHON"; then
    PY="$STUDIO_PYTHON"
    say "使用已有 Python：$PY"
    return 0
  fi
  if python_can_import "$ROOT/.venv/bin/python"; then
    PY="$ROOT/.venv/bin/python"
    say "使用 $PY"
    return 0
  fi
  conda_py="$(find_conda_python || true)"
  if [[ -n "$conda_py" ]] && python_can_import "$conda_py"; then
    PY="$conda_py"
    say "使用已有 Conda 环境：$PY"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && python_can_import "$(command -v python3)"; then
    PY="$(command -v python3)"
    say "使用已有 python3：$PY"
    return 0
  fi
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    if [[ "${DRY_RUN:-0}" == 1 ]]; then
      say "[dry-run] 将用 $ROOT/.venv 安装 requirements.txt"
      PY="$ROOT/.venv/bin/python"
      return 0
    fi
    say "补齐 $ROOT/.venv 中的依赖。"
    "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
    PY="$ROOT/.venv/bin/python"
    return 0
  fi
  if [[ "${DRY_RUN:-0}" == 1 ]]; then
    say "[dry-run] 将创建 $ROOT/.venv 并安装 requirements.txt"
    PY="${conda_py:-python3}"
    return 0
  fi
  if python3 -m venv "$ROOT/.venv"; then
    say "创建 $ROOT/.venv 并安装依赖。"
    "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
    PY="$ROOT/.venv/bin/python"
    return 0
  fi
  die "没有可用的 Python 环境。请设置 STUDIO_PYTHON 指向已经装好依赖的解释器。"
}

prepare_runtime_env() {
  local report line key_state=""
  local -a prepare_args=(--repo "$ROOT")
  if [[ "${DRY_RUN:-0}" != 1 ]]; then
    prepare_args+=(--apply)
  fi
  report="$(python3 "$ROOT/scripts/prepare_env.py" "${prepare_args[@]}")"
  while IFS= read -r line; do
    case "$line" in
      key_state=present|key_state=missing)
        key_state="${line#key_state=}"
        ;;
      *)
        die "环境准备结果无法识别。"
        ;;
    esac
  done <<<"$report"
  [[ -n "$key_state" ]] || die "环境准备结果不完整。"
  if [[ "$key_state" == present ]]; then
    say "推理服务密钥已在 .env 中，内容未打印。"
    return 0
  fi
  say "没有找到 INFERENCE_API_KEY。请写入本仓库 .env，不要把密钥交给浏览器。"
  if [[ "${DRY_RUN:-0}" != 1 ]]; then
    exit 1
  fi
}

load_dotenv() {
  [[ -f "$ROOT/.env" ]] || return 0
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
}
