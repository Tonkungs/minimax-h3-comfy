#!/usr/bin/env bash
set -Ee

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

if [[ "${SERVERLESS:-false}" != "true" ]]; then
  . "${utils}/exit_portal.sh" "API Wrapper"
fi

while [[ -f /.provisioning ]]; do
  echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
  sleep 5
done

if [[ -x /opt/instance-tools/bin/convert-workflows.sh ]]; then
  /opt/instance-tools/bin/convert-workflows.sh || true
fi

cd /opt/comfyui-api-wrapper
. .venv/bin/activate
pty uvicorn main:app --port 18288 2>&1
