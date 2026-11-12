# Shared helpers for the Studio start/stop/status scripts.
# This file is sourced. It does not start or stop anything by itself.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "请运行 scripts/start.sh、scripts/stop.sh 或 scripts/status.sh" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
H3_NATIVE_DIR="${H3_NATIVE_DIR:-$HOME/ai-services/h3-minimax-native-v2}"
STUDIO_LEGACY_DIR="${STUDIO_LEGACY_DIR:-$HOME/ai-services/h3-video-studio}"
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
  [[ "$cmd" == *"--port ${NATIVE_PORT}"* || "$cmd" == *"--port=${NATIVE_PORT}"* \
    || "$cmd" == *"--port ${IMAGE_COMFY_PORT}"* || "$cmd" == *"--port=${IMAGE_COMFY_PORT}"* \
    || "$cmd" == *"--port ${VIDEO_COMFY_PORT}"* || "$cmd" == *"--port=${VIDEO_COMFY_PORT}"* \
    || "$cmd" == *"h3-minimax"* || "$cmd" == *"comfy"* || "$cmd" == *"main.py"* ]]
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
  local native_code image_code video_code
  native_code="$(http_code "http://127.0.0.1:${NATIVE_PORT}/v2/health")"
  image_code="$(http_code "http://127.0.0.1:${IMAGE_COMFY_PORT}/system_stats")"
  video_code="$(http_code "http://127.0.0.1:${VIDEO_COMFY_PORT}/system_stats")"
  say "Native API ${NATIVE_PORT}：HTTP ${native_code}。按现有方式单独部署，脚本不启动、不停止。"
  say "图片 ComfyUI ${IMAGE_COMFY_PORT}：HTTP ${image_code}。按现有方式单独部署，脚本不启动、不停止。"
  say "视频 ComfyUI ${VIDEO_COMFY_PORT}：HTTP ${video_code}。按现有方式单独部署，脚本不启动、不停止。"
  if [[ "$native_code" != "200" && "$native_code" != "401" && "$native_code" != "403" ]]; then
    say "8089 目前没有响应。视频生成需要 Native API 已经按原有方式启动。"
  fi
  if [[ "$image_code" != "200" ]]; then
    say "8190 目前没有正常响应。图片生成需要它按原来的方式已经在运行。"
  fi
  if [[ "$video_code" != "200" ]]; then
    say "8189 目前没有正常响应。视频生成需要它按原来的方式已经在运行。"
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
  if python_can_import "$ROOT/.venv/bin/python"; then
    PY="$ROOT/.venv/bin/python"
    say "使用 $PY"
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
  conda_py="$(find_conda_python || true)"
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
  if [[ -n "$conda_py" ]]; then
    say "无法创建 .venv，改用已有环境并安装依赖：$conda_py"
    "$conda_py" -m pip install -r "$ROOT/requirements.txt"
    PY="$conda_py"
    return 0
  fi
  die "没有可用的 Python 环境。"
}

prepare_runtime_env() {
  local mode="plan" report line
  local -a prepare_args=()
  local key_state="" data_state="" database_path=""
  if [[ "${DRY_RUN:-0}" != 1 ]]; then
    mode="apply"
  fi
  prepare_args=(
    --repo "$ROOT"
    --native-env "$H3_NATIVE_DIR/.env"
    --legacy-dir "$STUDIO_LEGACY_DIR"
  )
  if [[ "$mode" == apply ]]; then
    prepare_args+=(--apply)
  fi
  report="$(python3 "$ROOT/scripts/prepare_env.py" "${prepare_args[@]}")"
  while IFS= read -r line; do
    case "$line" in
      key_state=present|key_state=copied|key_state=ready|key_state=missing)
        key_state="${line#key_state=}"
        ;;
      data_state=local|data_state=legacy|data_state=empty|data_state=custom)
        data_state="${line#data_state=}"
        ;;
      database_path=*)
        eval "$line"
        ;;
      *)
        die "环境准备结果无法识别。"
        ;;
    esac
  done <<<"$report"
  [[ -n "$key_state" && -n "$data_state" && -n "$database_path" ]] || die "环境准备结果不完整。"

  case "$key_state" in
    present)
      say "视频密钥已在本仓库 .env 中，内容未打印。"
      ;;
    copied)
      say "已从 ${H3_NATIVE_DIR}/.env 写入 VIDEO_NATIVE_API_KEY，内容未打印。"
      ;;
    ready)
      say "[dry-run] 正式启动时会从 ${H3_NATIVE_DIR}/.env 写入 VIDEO_NATIVE_API_KEY，内容不会打印。"
      ;;
    missing)
      say "没有找到视频密钥。请在本仓库 .env 填写 VIDEO_NATIVE_API_KEY，或确认 ${H3_NATIVE_DIR}/.env 中有 H3_MINIMAX_API_KEY。"
      if [[ "${DRY_RUN:-0}" != 1 ]]; then
        exit 1
      fi
      ;;
  esac
  case "$data_state" in
    legacy)
      say "继续使用已有数据：${database_path}，以及同目录的 uploads 和 outputs。"
      ;;
    local)
      say "使用本仓库里的 tasks.db。"
      ;;
    custom)
      say ".env 已指定数据路径，保持不变：${database_path}"
      ;;
    empty)
      say "还没有 tasks.db。启动后会在配置的路径创建新库。"
      ;;
  esac
  PREPARED_DATABASE_PATH="$database_path"
}

load_dotenv() {
  [[ -f "$ROOT/.env" ]] || return 0
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
}

backup_database() {
  local db="$1" stamp dest
  [[ -n "$db" && -f "$db" ]] || return 0
  if [[ "$db" != /* ]]; then
    db="$ROOT/$db"
  fi
  [[ -f "$db" ]] || return 0
  stamp="$(date +%Y%m%d-%H%M%S)"
  dest="${db}.backup.${stamp}"
  cp -a "$db" "$dest"
  [[ -f "${db}-wal" ]] && cp -a "${db}-wal" "${dest}-wal"
  [[ -f "${db}-shm" ]] && cp -a "${db}-shm" "${dest}-shm"
  say "已备份数据库：${dest}"
}
