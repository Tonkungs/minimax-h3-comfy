#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${H3_PROJECT_ROOT:-/opt/minimax-h3}"
PYTHON_BIN="${H3_PYTHON:-/venv/main/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[h3][error] Python runtime not found at $PYTHON_BIN" >&2
  exit 1
fi

echo "[h3] model_root=${MODEL_ROOT:-/workspace/models} preset=${MODEL_PRESET:-5090} mode=${DOWNLOAD_MODE:-missing}"

"$PYTHON_BIN" "${PROJECT_ROOT}/scripts/download_models.py"

"$PYTHON_BIN" "${PROJECT_ROOT}/scripts/fetch_workflows.py"
mkdir -p /workspace/output /workspace/workflows

# Install the frontend extension and serve the fetched official workflow from
# ComfyUI's extension directory. This makes the I2V graph load on page open.
COMFYUI_DIR="${COMFYUI_DIR:-/workspace/ComfyUI}"
AUTOLOAD_DIR="${COMFYUI_DIR}/custom_nodes/minimax_h3_autoload"
mkdir -p "${AUTOLOAD_DIR}/web"
cp -f "${PROJECT_ROOT}/custom_node/__init__.py" "${AUTOLOAD_DIR}/__init__.py"
cp -f "${PROJECT_ROOT}/custom_node/web/minimax_h3_autoload.js" "${AUTOLOAD_DIR}/web/minimax_h3_autoload.js"
if [[ -s /workspace/workflows/video_minimax_h3_i2v.json ]]; then
  cp -f /workspace/workflows/video_minimax_h3_i2v.json "${AUTOLOAD_DIR}/web/video_minimax_h3_i2v.json"
fi

# Vast's base supervisor starts tunnel_manager and ComfyUI in parallel. Replace
# only the tunnel command with a small gate so Cloudflare cannot connect before
# ComfyUI is accepting HTTP requests.
if [[ -f /etc/supervisor/conf.d/tunnel_manager.conf ]]; then
  cp -f "${PROJECT_ROOT}/scripts/start_tunnel_after_comfyui.sh" \
    /opt/minimax-h3/start_tunnel_after_comfyui.sh
  chmod +x /opt/minimax-h3/start_tunnel_after_comfyui.sh
  sed -i \
    's#command=/opt/supervisor-scripts/tunnel_manager.sh#command=/opt/minimax-h3/start_tunnel_after_comfyui.sh#' \
    /etc/supervisor/conf.d/tunnel_manager.conf
fi

# The Vast base image owns the portal, Jupyter and ComfyUI startup contract.
# Keep that entrypoint so the supplied PORTAL_CONFIG and COMFYUI_ARGS continue
# to work. Fall back to direct ComfyUI startup for local debugging only.
if [[ -x /opt/instance-tools/bin/entrypoint.sh ]]; then
  exec /opt/instance-tools/bin/entrypoint.sh "$@"
fi

exec "$PYTHON_BIN" "${COMFYUI_DIR}/main.py" ${COMFYUI_ARGS:-"--disable-auto-launch --disable-xformers --port 18188 --enable-cors-header"}
