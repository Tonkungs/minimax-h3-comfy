FROM vastai/comfy:v0.32.0-cuda-13.2-py312@sha256:c4051247f5a415d459cddbc5012824a723c8e3bdb9afc46eb205e97984ff7e5c

USER root

ENV MODEL_ROOT=/workspace/models \
    MODEL_PRESET=5090 \
    DOWNLOAD_MODE=missing \
    MODEL_PROBE_BYTES=1048576 \
    H3_PROJECT_ROOT=/opt/minimax-h3 \
    H3_PYTHON=/venv/main/bin/python

RUN test -x "$H3_PYTHON" \
    && "$H3_PYTHON" -m pip install --no-cache-dir \
    'huggingface_hub>=1.5,<2.0'

COPY scripts /opt/minimax-h3/scripts
COPY config /opt/minimax-h3/config
COPY workflows /opt/minimax-h3/workflows
COPY custom_node /opt/minimax-h3/custom_node
COPY docker-entrypoint.sh /opt/minimax-h3/docker-entrypoint.sh

RUN chmod +x /opt/minimax-h3/docker-entrypoint.sh \
    /opt/minimax-h3/scripts/*.py \
    && mkdir -p /workspace/models /workspace/output /workspace/workflows

EXPOSE 1111 8080 8188 8288 8384

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD "$H3_PYTHON" /opt/minimax-h3/scripts/healthcheck.py || exit 1

ENTRYPOINT ["/opt/minimax-h3/docker-entrypoint.sh"]
