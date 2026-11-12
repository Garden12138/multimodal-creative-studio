#!/usr/bin/env bash
# Stop only the Studio process on port 8088.
# The Native API and both ComfyUI deployments are left running.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
elif [[ -n "${1:-}" ]]; then
  die "未知参数：$1。可用 --dry-run。"
fi

cd "$ROOT"
STUDIO_PORT=8088
if [[ -f "$ROOT/.env" ]]; then
  found="$(grep -E '^STUDIO_PORT=' "$ROOT/.env" | tail -n 1 | cut -d= -f2- | tr -d "\"'" || true)"
  [[ -n "$found" ]] && STUDIO_PORT="$found"
fi

stop_studio_on_port 8088
if [[ "$STUDIO_PORT" != "8088" ]]; then
  stop_studio_on_port "$STUDIO_PORT"
fi

if [[ "$DRY_RUN" != 1 ]]; then
  rm -f "$ROOT/run/studio.pid"
fi
say "操作完成。8089、8189、8190 保持原状。"
