#!/usr/bin/env bash
# Show Studio, Native API, and both ComfyUI endpoints without changing them.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cd "$ROOT"
STUDIO_PORT=8088
if [[ -f "$ROOT/.env" ]]; then
  found="$(grep -E '^STUDIO_PORT=' "$ROOT/.env" | tail -n 1 | cut -d= -f2- | tr -d "\"'" || true)"
  [[ -n "$found" ]] && STUDIO_PORT="$found"
fi

say "Studio ${STUDIO_PORT}："
if curl -fsS --noproxy '*' --connect-timeout 2 --max-time 5 "http://127.0.0.1:${STUDIO_PORT}/api/health" 2>/dev/null; then
  printf '\n'
else
  say "未响应。"
fi

probe_independent_services
