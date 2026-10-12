#!/usr/bin/env bash
set -Ee

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

if [[ "${SERVERLESS:-false}" != "true" ]]; then
  . "${utils}/exit_portal.sh" "ComfyUI"
fi

COMFYUI_DIR="${COMFYUI_DIR:-/opt/workspace-internal/ComfyUI}"
. /venv/main/bin/activate

if [[ ! -f /.provisioning && -f "${COMFYUI_DIR}/requirements.txt" ]]; then
  cd "${COMFYUI_DIR}"
  uv pip --no-cache-dir install -r requirements.txt
fi

# Start control service + auth proxy on every start (idempotent).
/opt/minimax-h3/scripts/h3_services.sh

# Run H3 preparation from the service that is guaranteed to be loaded by the
# Vast base image. The manifest/version stamp prevents stale template model
# names or a previous image's marker from skipping preparation.
if ! /venv/main/bin/python /opt/minimax-h3/scripts/h3_control.py is-prepared; then
  /opt/minimax-h3/docker-entrypoint.sh
fi

while [[ -f /.provisioning ]]; do
  echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
  sleep 5
done

COMFYUI_ARGS="${COMFYUI_ARGS:---disable-auto-launch --disable-xformers --port 18188 --enable-cors-header}"
cd "${COMFYUI_DIR}"
LD_PRELOAD=libtcmalloc_minimal.so.4 pty python main.py ${COMFYUI_ARGS} 2>&1
