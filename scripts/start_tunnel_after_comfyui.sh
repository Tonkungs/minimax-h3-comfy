#!/usr/bin/env bash
set -Eeuo pipefail

COMFYUI_PORT="${COMFYUI_PORT:-18188}"
COMFYUI_URL="${COMFYUI_URL:-http://127.0.0.1:${COMFYUI_PORT}/}"
TUNNEL_MANAGER="/opt/supervisor-scripts/tunnel_manager.sh"
PORT_PROXY="${H3_PROJECT_ROOT:-/opt/minimax-h3}/scripts/port_proxy.py"

echo "[h3] Waiting for ComfyUI before starting Cloudflare tunnel: ${COMFYUI_URL}"

while true; do
  if curl --fail --silent --show-error --max-time 2 "$COMFYUI_URL" >/dev/null 2>&1; then
    echo "[h3] ComfyUI is ready; starting Cloudflare tunnel manager"
    # Vast's tunnel manager reads external_port from portal.yaml. In Docker,
    # that is the published host port, so bridge it to the service's internal
    # port before cloudflared starts.
    for mapping in "1111 19111" "8188 19188" "8288 19288" "8080 19080" "8384 19384"; do
      read -r listen target <<<"$mapping"
      if ! (echo >/dev/tcp/127.0.0.1/${listen}) >/dev/null 2>&1; then
        nohup "$H3_PYTHON" "$PORT_PROXY" "$listen" "$target" \
          >>/var/log/portal/h3-port-proxy-${listen}.log 2>&1 &
      fi
    done
    sleep 1
    exec "$TUNNEL_MANAGER"
  fi
  sleep 2
done
