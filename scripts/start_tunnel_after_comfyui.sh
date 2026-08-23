#!/usr/bin/env bash
set -Eeuo pipefail

COMFYUI_PORT="${COMFYUI_PORT:-18188}"
COMFYUI_URL="${COMFYUI_URL:-http://127.0.0.1:${COMFYUI_PORT}/}"
TUNNEL_MANAGER="/opt/supervisor-scripts/tunnel_manager.sh"

echo "[h3] Waiting for ComfyUI before starting Cloudflare tunnel: ${COMFYUI_URL}"

while true; do
  if curl --fail --silent --show-error --max-time 2 "$COMFYUI_URL" >/dev/null 2>&1; then
    echo "[h3] ComfyUI is ready; starting Cloudflare tunnel manager"
    exec "$TUNNEL_MANAGER"
  fi
  sleep 2
done
