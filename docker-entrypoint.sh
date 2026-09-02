#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${H3_PROJECT_ROOT:-/opt/minimax-h3}"
PYTHON_BIN="${H3_PYTHON:-/venv/main/bin/python}"
AUTH_PROXY="${PROJECT_ROOT}/scripts/auth_proxy.py"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[h3][error] Python runtime not found at $PYTHON_BIN" >&2
  exit 1
fi

echo "[h3] model_root=${MODEL_ROOT:-/workspace/models} preset=${MODEL_PRESET:-5090} mode=${DOWNLOAD_MODE:-missing}"

# The Vast Comfy image includes a Caddy basic-auth listener on 8188. Remove it
# so our token proxy can own the published ComfyUI route.
find /etc/supervisor/conf.d -maxdepth 1 -type f -iname '*caddy*.conf' -delete 2>/dev/null || true

"$PYTHON_BIN" "${PROJECT_ROOT}/scripts/download_models.py"

"$PYTHON_BIN" "${PROJECT_ROOT}/scripts/fetch_workflows.py"
mkdir -p /workspace/output /workspace/workflows

# Install the frontend extension and serve the fetched official workflow from
# ComfyUI's extension directory. This makes the I2V graph load on page open.
COMFYUI_DIR="${COMFYUI_DIR:-/opt/workspace-internal/ComfyUI}"
AUTOLOAD_DIR="${COMFYUI_DIR}/custom_nodes/minimax_h3_autoload"
mkdir -p "${AUTOLOAD_DIR}/web"
cp -f "${PROJECT_ROOT}/custom_node/__init__.py" "${AUTOLOAD_DIR}/__init__.py"
cp -f "${PROJECT_ROOT}/custom_node/web/minimax_h3_autoload.js" "${AUTOLOAD_DIR}/web/minimax_h3_autoload.js"
if [[ -s /workspace/workflows/video_minimax_h3_i2v.json ]]; then
  cp -f /workspace/workflows/video_minimax_h3_i2v.json "${AUTOLOAD_DIR}/web/video_minimax_h3_i2v.json"
fi

# Keep Cloudflare's tunnel credential separate from the HTTP auth boundary.
# Fall back to CF_TOKEN for backwards compatibility with existing templates.
AUTH_TOKEN="${H3_AUTH_TOKEN:-${CF_TOKEN:-}}"
if [[ -z "$AUTH_TOKEN" ]]; then
  echo "[h3][error] H3_AUTH_TOKEN (or CF_TOKEN fallback) is required" >&2
  exit 1
fi
export H3_AUTH_TOKEN="$AUTH_TOKEN"
nohup "$PYTHON_BIN" "$AUTH_PROXY" 18189 18188 \
  >>/var/log/portal/h3-auth-proxy-18189.log 2>&1 &

# Wait for ComfyUI before letting Vast's existing tunnel manager connect.
if [[ -f /etc/supervisor/conf.d/tunnel_manager.conf ]]; then
  cp -f "${PROJECT_ROOT}/scripts/start_tunnel_after_comfyui.sh" \
    /opt/minimax-h3/start_tunnel_after_comfyui.sh
  chmod +x /opt/minimax-h3/start_tunnel_after_comfyui.sh
  sed -i \
    's#command=/opt/supervisor-scripts/tunnel_manager.sh#command=/opt/minimax-h3/start_tunnel_after_comfyui.sh#' \
    /etc/supervisor/conf.d/tunnel_manager.conf
fi

echo "[h3] preparation complete; Vast base entrypoint owns service startup"
