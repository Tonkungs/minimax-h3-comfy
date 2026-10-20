FROM vastai/comfy:v0.36.0-cuda-13.2-py312

USER root

ARG H3_IMAGE_VERSION=1.7.1

ENV MODEL_ROOT=/workspace/models \
    MODEL_PRESET=5090 \
    DOWNLOAD_MODE=missing \
    MODEL_PROBE_BYTES=1048576 \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
    H3_IMAGE_VERSION=${H3_IMAGE_VERSION} \
    H3_PROJECT_ROOT=/opt/minimax-h3 \
    H3_PYTHON=/venv/main/bin/python \
    COMFYUI_DIR=/opt/workspace-internal/ComfyUI \
    COMFYUI_ARGS="--disable-auto-launch --disable-xformers --port 18188 --enable-cors-header --fast fp16_accumulation --use-sage-attention"

RUN set -Eeuo pipefail \
    && mkdir -p /workspace/models /workspace/output /workspace/workflows

COPY vast_overlay /
COPY scripts /opt/minimax-h3/scripts
COPY config /opt/minimax-h3/config
COPY VERSION /opt/minimax-h3/VERSION
COPY workflows /opt/minimax-h3/workflows
COPY custom_node /opt/minimax-h3/custom_node
COPY docker-entrypoint.sh /opt/minimax-h3/docker-entrypoint.sh

# Custom nodes pinned to reviewed commits; their requirements are not installed
# (comfy-kitchen and einops already ship in the base image).
RUN set -Eeuo pipefail \
    && cd "$COMFYUI_DIR/custom_nodes" \
    && git clone https://github.com/Kablex/ComfyUI-Ref2VA-VSA \
    && git -C ComfyUI-Ref2VA-VSA checkout 323a0a5f7c636cc70f95464145732f81f04a72e9 \
    && git clone https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler \
    && git -C Comfyui_Minimax_h3_latent_Upscaler checkout 40316cf008b2fd8663263270669eb4da23f89d2c

RUN chmod +x /opt/minimax-h3/docker-entrypoint.sh \
    /opt/minimax-h3/scripts/*.py \
    /opt/minimax-h3/scripts/h3_download_probe.sh \
    /opt/minimax-h3/scripts/h3_services.sh \
    /opt/supervisor-scripts/comfyui.sh \
    /opt/supervisor-scripts/api-wrapper.sh \
    && mkdir -p /opt/minimax-h3/assets \
    && "$H3_PYTHON" /opt/minimax-h3/scripts/create_selftest_reference.py \
      /opt/minimax-h3/assets/selftest-reference.png \
    && command -v ffprobe >/dev/null \
    && command -v ffmpeg >/dev/null \
    && command -v openssl >/dev/null

LABEL org.opencontainers.image.version="${H3_IMAGE_VERSION}" \
      org.opencontainers.image.title="MiniMax H3 ComfyUI for Vast.ai"

EXPOSE 1111 8080 8188 8288 8384 19080 19111 19188 19288 19384

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=5 \
  CMD "$H3_PYTHON" /opt/minimax-h3/scripts/healthcheck.py || exit 1
