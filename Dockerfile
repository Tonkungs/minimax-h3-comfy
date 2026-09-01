ARG COMFY_SOURCE_IMAGE=vastai/comfy:v0.32.0-cuda-13.2-py312

# Build-only source: this supplies the already-tested ComfyUI/PyTorch runtime.
# It is never the final base and its baked-in checkpoint is not copied.
FROM ${COMFY_SOURCE_IMAGE} AS comfy_source

FROM vastai/base-image:cuda-13.2-mini-py312-2026-06-05@sha256:f88076fc8153f2ecf6ca46cb8a014127bbe8ca18e4c2503c45cc01a22ff568e0

USER root

ENV MODEL_ROOT=/workspace/models \
    MODEL_PRESET=5090 \
    DOWNLOAD_MODE=missing \
    MODEL_PROBE_BYTES=1048576 \
    H3_PROJECT_ROOT=/opt/minimax-h3 \
    H3_PYTHON=/venv/main/bin/python \
    COMFYUI_DIR=/opt/workspace-internal/ComfyUI \
    COMFYUI_ARGS="--disable-auto-launch --disable-xformers --port 18188 --enable-cors-header"

# Keep Vast's Portal/Supervisor stack and copy only the tested GPU runtime and
# ComfyUI components. The source image's model_store is intentionally omitted.
RUN set -Eeuo pipefail \
    && apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /workspace/models /workspace/output /workspace/workflows

COPY --from=comfy_source /venv/main /venv/main
COPY --from=comfy_source /opt/workspace-internal/ComfyUI /opt/workspace-internal/ComfyUI
COPY --from=comfy_source /opt/comfyui-api-wrapper /opt/comfyui-api-wrapper

COPY vast_overlay /
COPY scripts /opt/minimax-h3/scripts
COPY config /opt/minimax-h3/config
COPY workflows /opt/minimax-h3/workflows
COPY custom_node /opt/minimax-h3/custom_node
COPY docker-entrypoint.sh /opt/minimax-h3/docker-entrypoint.sh

RUN chmod +x /opt/minimax-h3/docker-entrypoint.sh \
    /opt/minimax-h3/scripts/*.py \
    /opt/supervisor-scripts/comfyui.sh \
    /opt/supervisor-scripts/api-wrapper.sh \
    && mkdir -p /workspace/models /workspace/output /workspace/workflows

EXPOSE 1111 8080 8188 8288 8384 19080 19111 19188 19288 19384

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=5 \
  CMD "$H3_PYTHON" /opt/minimax-h3/scripts/healthcheck.py || exit 1

ENTRYPOINT ["/opt/minimax-h3/docker-entrypoint.sh"]
