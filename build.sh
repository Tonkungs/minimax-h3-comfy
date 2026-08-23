#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE_NAME="${IMAGE_NAME:-minimax-h3-comfy}"
IMAGE_TAG="${IMAGE_TAG:-0.1.0}"
CONTAINER_NAME="${CONTAINER_NAME:-minimax-h3-comfy}"
IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gpu_flags=(--env NVIDIA_VISIBLE_DEVICES=void)
if [[ "${USE_GPU:-1}" == "1" ]]; then
  gpu_flags=(--gpus all)
fi

comfy_args="${COMFYUI_ARGS:---disable-auto-launch --disable-xformers --port 18188 --enable-cors-header}"
if [[ " $comfy_args " != *" --listen "* ]]; then
  comfy_args+=" --listen 0.0.0.0"
fi
if [[ "${USE_GPU:-1}" != "1" && " $comfy_args " != *" --cpu "* ]]; then
  comfy_args+=" --cpu"
fi

ports=(
  -p 1111:11111 -p 8080:18080 -p 8384:18384
  -p 8188:18188 -p 8288:18288
)

common_env=(
  -e "COMFYUI_ARGS=${comfy_args}"
  -e "COMFYUI_API_BASE=${COMFYUI_API_BASE:-http://localhost:18188}"
  -e "OPEN_BUTTON_PORT=${OPEN_BUTTON_PORT:-1111}"
  -e "OPEN_BUTTON_TOKEN=${OPEN_BUTTON_TOKEN:-1}"
  -e "JUPYTER_DIR=${JUPYTER_DIR:-/}"
  -e "DATA_DIRECTORY=${DATA_DIRECTORY:-/workspace/}"
  -e "MODEL_ROOT=${MODEL_ROOT:-/workspace/models}"
  -e "MODEL_PRESET=${MODEL_PRESET:-5090}"
  -e "DOWNLOAD_MODE=${DOWNLOAD_MODE:-missing}"
  -e "H3_PYTHON=${H3_PYTHON:-/venv/main/bin/python}"
  -e "PORTAL_CONFIG=${PORTAL_CONFIG:-localhost:1111:11111:/:Instance Portal|localhost:8188:18188:/:ComfyUI|localhost:8288:18288:/docs:API Wrapper|localhost:8188:18188:/:ComfyUI|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal|localhost:8384:18384:/:Syncthing}"
)

usage() {
  echo "Usage: $0 {build|probe|run|stop|push}"
}

if [[ "$IMAGE" =~ [A-Z] || "$IMAGE" =~ [[:space:]] ]]; then
  echo "IMAGE_NAME/IMAGE_TAG must be lowercase and contain no spaces: $IMAGE" >&2
  exit 2
fi

case "${1:-}" in
  build)
    docker build --platform linux/amd64 -t "$IMAGE" "$ROOT_DIR"
    ;;
  probe)
    : "${HF_TOKEN:?Set HF_TOKEN in the shell; do not put it in this repository}"
    docker run --rm "${gpu_flags[@]}" --name "$CONTAINER_NAME-probe" \
      "${ports[@]}" "${common_env[@]}" \
      -e HF_TOKEN \
      -e MODEL_PRESET="${MODEL_PRESET:-5090}" \
      -e DOWNLOAD_MODE=probe \
      -v "${ROOT_DIR}/runtime-workspace:/workspace" \
      "$IMAGE"
    ;;
  run)
    if [[ "${DOWNLOAD_MODE:-missing}" != "skip" ]]; then
      : "${HF_TOKEN:?Set HF_TOKEN in the shell; do not put it in this repository}"
    fi
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker run -d "${gpu_flags[@]}" --name "$CONTAINER_NAME" \
      "${ports[@]}" "${common_env[@]}" -e HF_TOKEN \
      -v "${ROOT_DIR}/runtime-workspace:/workspace" \
      "$IMAGE"
    echo "Started $CONTAINER_NAME; use: docker logs -f $CONTAINER_NAME"
    ;;
  stop)
    docker rm -f "$CONTAINER_NAME"
    ;;
  push)
    docker push "$IMAGE"
    ;;
  *)
    usage
    exit 2
    ;;
esac
