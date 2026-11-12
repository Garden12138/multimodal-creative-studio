#!/usr/bin/env bash
# Start Studio on 8088. Native API and both ComfyUI services stay independently deployed.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

DRY_RUN=0
FOREGROUND=0

usage() {
  cat <<'EOF'
用法:
  ./scripts/start.sh              后台启动 Studio
  ./scripts/start.sh --foreground 在当前终端启动 Studio
  ./scripts/start.sh --dry-run    只检查，不停止、不启动、不写 .env

脚本只管理 8088 上的 Studio。8089、8189、8190 只探测，不启动、不停止、不重启。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --foreground) FOREGROUND=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1。可用 --dry-run、--foreground。" ;;
  esac
  shift
done

cd "$ROOT"
command -v python3 >/dev/null 2>&1 || die "需要 python3。"

say "仓库目录：${ROOT}"
probe_independent_services
prepare_runtime_env
select_python

if python_can_import "$PY"; then
  (cd "$ROOT" && "$PY" check_workflows.py)
elif [[ "$DRY_RUN" == 1 ]]; then
  say "[dry-run] 正式启动前会运行 check_workflows.py。"
else
  die "依赖没有安装完整。"
fi

if [[ "$DRY_RUN" != 1 ]]; then
  load_dotenv
fi
STUDIO_HOST="${STUDIO_HOST:-0.0.0.0}"
STUDIO_PORT="${STUDIO_PORT:-8088}"

stop_studio_on_port 8088
if [[ "$STUDIO_PORT" != "8088" ]]; then
  stop_studio_on_port "$STUDIO_PORT"
fi

if [[ "$DRY_RUN" == 1 ]]; then
  say "[dry-run] 检查结束。没有停止进程，也没有启动新进程。"
  exit 0
fi

backup_database "${PREPARED_DATABASE_PATH:-}"
mkdir -p "$ROOT/logs" "$ROOT/run"

say "启动 Studio：${PY} -m uvicorn app:app --host ${STUDIO_HOST} --port ${STUDIO_PORT} --workers 1"
cd "$ROOT"
if [[ "$FOREGROUND" == 1 ]]; then
  exec "$PY" -m uvicorn app:app --host "$STUDIO_HOST" --port "$STUDIO_PORT" --workers 1
fi

nohup "$PY" -m uvicorn app:app --host "$STUDIO_HOST" --port "$STUDIO_PORT" --workers 1 >> "$ROOT/logs/studio.log" 2>&1 &
echo $! > "$ROOT/run/studio.pid"

waited=0
while [[ "$waited" -lt 30 ]]; do
  if curl -fsS --noproxy '*' --connect-timeout 2 --max-time 5 "http://127.0.0.1:${STUDIO_PORT}/api/health" 2>/dev/null; then
    printf '\n'
    say "Studio 已启动：http://127.0.0.1:${STUDIO_PORT}"
    say "日志：${ROOT}/logs/studio.log"
    say "停止 Studio：./scripts/stop.sh。8089、8189、8190 不会被改动。"
    exit 0
  fi
  if ! kill -0 "$(cat "$ROOT/run/studio.pid")" 2>/dev/null; then
    say "Studio 进程已退出。最近日志：" >&2
    tail -n 40 "$ROOT/logs/studio.log" | redact_stream >&2 || true
    exit 1
  fi
  sleep 1
  waited=$((waited + 1))
done

say "Studio 进程已启动，但 /api/health 还没有返回成功。最近日志：" >&2
tail -n 40 "$ROOT/logs/studio.log" | redact_stream >&2 || true
exit 1
