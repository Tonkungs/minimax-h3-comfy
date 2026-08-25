#!/usr/bin/env bash
set -Ee

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

if [[ "${SERVERLESS:-false}" != "true" ]]; then
  . "${utils}/exit_portal.sh" "ComfyUI"
fi

COMFYUI_DIR="${WORKSPACE:-/workspace}/ComfyUI"
. /venv/main/bin/activate

if [[ ! -f /.provisioning && -f "${COMFYUI_DIR}/requirements.txt" ]]; then
  cd "${COMFYUI_DIR}"
  uv pip --no-cache-dir install -r requirements.txt
fi

while [[ -f /.provisioning ]]; do
  echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
  sleep 5
done

COMFYUI_ARGS="${COMFYUI_ARGS:---disable-auto-launch --disable-xformers --port 18188 --enable-cors-header}"
cd "${COMFYUI_DIR}"
LD_PRELOAD=libtcmalloc_minimal.so.4 pty python main.py ${COMFYUI_ARGS} 2>&1
