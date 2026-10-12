#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${H3_PROJECT_ROOT:-/opt/minimax-h3}"
PYTHON_BIN="${H3_PYTHON:-/venv/main/bin/python}"
AUTH_PROXY="${PROJECT_ROOT}/scripts/auth_proxy.py"
CONTROL_SCRIPT="${PROJECT_ROOT}/scripts/h3_control.py"
COMFYUI_DIR="${COMFYUI_DIR:-/opt/workspace-internal/ComfyUI}"
PREPARE_LOCK="/workspace/.h3-prepare.lock"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[h3][error] Python runtime not found at $PYTHON_BIN" >&2
  exit 1
fi

echo "[h3] model_root=${MODEL_ROOT:-/workspace/models} preset=${MODEL_PRESET:-5090} mode=${DOWNLOAD_MODE:-missing}"

AUTH_TOKEN="${CF_TOKEN:-${H3_AUTH_TOKEN:-}}"
if [[ -z "$AUTH_TOKEN" ]]; then
  echo "[h3][error] CF_TOKEN is required (H3_AUTH_TOKEN is accepted for legacy templates)" >&2
  exit 1
fi
export H3_AUTH_TOKEN="$AUTH_TOKEN"
unset AUTH_TOKEN

mkdir -p /workspace /var/log/portal /workspace/output /workspace/workflows
exec 9>"$PREPARE_LOCK"
flock -x 9

STAGE_ERR="$(mktemp)"
# Run a stage, mirroring its stderr into $STAGE_ERR so the ERR trap can report it.
stage() {
  : >"$STAGE_ERR"
  "$@" 2> >(tee -a "$STAGE_ERR" >&2)
}

on_error() {
  local exit_code=$?
  trap - ERR
  sleep 0.3 # let the tee process substitution flush
  local detail
  detail="$(grep -v '^[[:space:]]*$' "$STAGE_ERR" 2>/dev/null | tail -n 1 | cut -c1-300 || true)"
  "$PYTHON_BIN" "$CONTROL_SCRIPT" phase error "preparation failed (exit status ${exit_code})${detail:+: ${detail}}" >/dev/null 2>&1 || true
  exit "$exit_code"
}
trap on_error ERR
trap 'rm -f "$STAGE_ERR"' EXIT

"${PROJECT_ROOT}/scripts/h3_services.sh"

MODE="${DOWNLOAD_MODE:-missing}"
PREPARED=true
if [[ "$MODE" == "skip" || "$MODE" == "probe" ]]; then
  PREPARED=false
fi

# Clear any stale prepared flag while this boot re-checks links and files.
"$PYTHON_BIN" "$CONTROL_SCRIPT" phase booting "" false

if ! "$PYTHON_BIN" "$CONTROL_SCRIPT" is-prepared; then
  # Vast templates may retain MODEL_FILES from an older image. Derive the exact
  # selected names from this image's manifest and pass those to the downloader.
  MANIFEST_MODEL_FILES="$(stage "$PYTHON_BIN" "$CONTROL_SCRIPT" model-files)"
  if [[ -z "$MANIFEST_MODEL_FILES" ]]; then
    echo "[h3][error] image model manifest selected no files" >&2
    "$PYTHON_BIN" "$CONTROL_SCRIPT" phase error "image model manifest selected no files" || true
    exit 1
  fi
  if [[ -n "${MODEL_FILES:-}" && "$MODEL_FILES" != "$MANIFEST_MODEL_FILES" ]]; then
    echo "[h3] ignoring template MODEL_FILES; using image model-manifest.json"
  fi
  export MODEL_FILES="$MANIFEST_MODEL_FILES"

  "$PYTHON_BIN" "$CONTROL_SCRIPT" phase downloading
  stage "$PYTHON_BIN" "${PROJECT_ROOT}/scripts/download_models.py"
else
  echo "[h3] model preparation already complete for this image manifest"
fi

LINK_RESULT="$(stage "$PYTHON_BIN" "$CONTROL_SCRIPT" link-models)"
echo "[h3] live ComfyUI model links: $LINK_RESULT"

stage "$PYTHON_BIN" "${PROJECT_ROOT}/scripts/fetch_workflows.py"

# Install the frontend extension and serve the fetched official workflow from
# ComfyUI's extension directory. This makes the I2V graph load on page open.
AUTOLOAD_DIR="${COMFYUI_DIR}/custom_nodes/minimax_h3_autoload"
mkdir -p "${AUTOLOAD_DIR}/web"
cp -f "${PROJECT_ROOT}/custom_node/__init__.py" "${AUTOLOAD_DIR}/__init__.py"
cp -f "${PROJECT_ROOT}/custom_node/web/minimax_h3_autoload.js" "${AUTOLOAD_DIR}/web/minimax_h3_autoload.js"
if [[ -s /workspace/workflows/video_minimax_h3_i2v.json ]]; then
  cp -f /workspace/workflows/video_minimax_h3_i2v.json "${AUTOLOAD_DIR}/web/video_minimax_h3_i2v.json"
fi

# Wait for ComfyUI before letting Vast's existing tunnel manager connect.
for _ in $(seq 1 120); do
  [[ -f /etc/supervisor/conf.d/tunnel_manager.conf ]] && break
  sleep 1
done
if [[ -f /etc/supervisor/conf.d/tunnel_manager.conf ]]; then
  cp -f "${PROJECT_ROOT}/scripts/start_tunnel_after_comfyui.sh" \
    /opt/minimax-h3/start_tunnel_after_comfyui.sh
  chmod +x /opt/minimax-h3/start_tunnel_after_comfyui.sh
  sed -i \
    's#command=/opt/supervisor-scripts/tunnel_manager.sh#command=/opt/minimax-h3/start_tunnel_after_comfyui.sh#' \
    /etc/supervisor/conf.d/tunnel_manager.conf
fi

"$PYTHON_BIN" "$CONTROL_SCRIPT" mark-prepared
# Never restart from inside the comfyui supervisor program itself.
if [[ "$PREPARED" == true && "$LINK_RESULT" == *'"changed": true'* \
  && "${SUPERVISOR_PROCESS_NAME:-}" != "comfyui" ]] && command -v supervisorctl >/dev/null 2>&1; then
  COMFY_STATUS="$(supervisorctl status comfyui 2>/dev/null || true)"
  if [[ "$COMFY_STATUS" == *RUNNING* ]]; then
    echo "[h3] model links changed; restarting running ComfyUI to rescan"
    supervisorctl restart comfyui
  fi
fi

"$PYTHON_BIN" "$CONTROL_SCRIPT" phase starting_comfy "" "$PREPARED"

echo "[h3] preparation complete; Vast base entrypoint owns service startup"
