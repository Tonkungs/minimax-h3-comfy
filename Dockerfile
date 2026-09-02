FROM vastai/comfy:v0.30.0-cuda-13.2-py312

USER root

ENV MODEL_ROOT=/workspace/models \
    MODEL_PRESET=5090 \
    DOWNLOAD_MODE=missing \
    MODEL_PROBE_BYTES=1048576 \
    H3_PROJECT_ROOT=/opt/minimax-h3 \
    H3_PYTHON=/venv/main/bin/python \
    COMFYUI_DIR=/opt/workspace-internal/ComfyUI \
    COMFYUI_ARGS="--disable-auto-launch --disable-xformers --port 18188 --enable-cors-header"

RUN set -Eeuo pipefail \
    && mkdir -p /workspace/models /workspace/output /workspace/workflows

COPY vast_overlay /
COPY scripts /opt/minimax-h3/scripts
COPY config /opt/minimax-h3/config
COPY workflows /opt/minimax-h3/workflows
COPY custom_node /opt/minimax-h3/custom_node
COPY docker-entrypoint.sh /opt/minimax-h3/docker-entrypoint.sh

RUN chmod +x /opt/minimax-h3/docker-entrypoint.sh \
    /opt/minimax-h3/scripts/*.py \
    /opt/supervisor-scripts/comfyui.sh \
    /opt/supervisor-scripts/api-wrapper.sh

EXPOSE 1111 8080 8188 8288 8384 19080 19111 19188 19288 19384

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=5 \
  CMD "$H3_PYTHON" /opt/minimax-h3/scripts/healthcheck.py || exit 1
