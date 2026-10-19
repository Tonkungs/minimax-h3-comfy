#!/usr/bin/env bash
# Idempotently start the H3 control service (18189) and token proxy (8188). Run on every start.
set -Eeuo pipefail

PROJECT_ROOT="${H3_PROJECT_ROOT:-/opt/minimax-h3}"
PYTHON_BIN="${H3_PYTHON:-/venv/main/bin/python}"
AUTH_PROXY="${PROJECT_ROOT}/scripts/auth_proxy.py"
CONTROL_SCRIPT="${PROJECT_ROOT}/scripts/h3_control.py"

AUTH_TOKEN="${CF_TOKEN:-${H3_AUTH_TOKEN:-}}"
if [[ -z "$AUTH_TOKEN" ]]; then
  echo "[h3][error] CF_TOKEN is required (H3_AUTH_TOKEN is accepted for legacy templates)" >&2
  exit 1
fi
export H3_AUTH_TOKEN="$AUTH_TOKEN"
unset AUTH_TOKEN
mkdir -p /var/log/portal

# The Vast Comfy image includes a Caddy basic-auth listener on 8188. Remove it
# so our token proxy can own the published ComfyUI route.
find /etc/supervisor/conf.d -maxdepth 1 -type f -iname '*caddy*.conf' -delete 2>/dev/null || true
# A base-image Caddy process may already have been spawned by the time this
# hook runs. Stop it explicitly; otherwise it can loop on :8080 and prevent
# the mapped services from becoming reachable.
pkill -TERM -x caddy 2>/dev/null || true

if ! (echo >/dev/tcp/127.0.0.1/18189) >/dev/null 2>&1; then
  setsid nohup "$PYTHON_BIN" "$CONTROL_SCRIPT" serve \
    >>/var/log/portal/h3-control.log 2>&1 9>&- &
  echo "$!" >/workspace/.h3-control.pid
fi
for _ in $(seq 1 30); do
  if (echo >/dev/tcp/127.0.0.1/18189) >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! (echo >/dev/tcp/127.0.0.1/18189) >/dev/null 2>&1; then
  echo "[h3][error] H3 control service did not start" >&2
  exit 1
fi

if ! (echo >/dev/tcp/127.0.0.1/8188) >/dev/null 2>&1; then
  setsid nohup "$PYTHON_BIN" "$AUTH_PROXY" 8188 18188 18189 \
    >>/var/log/portal/h3-auth-proxy-8188.log 2>&1 9>&- &
  echo "$!" >/workspace/.h3-auth-proxy.pid
fi
for _ in $(seq 1 30); do
  if (echo >/dev/tcp/127.0.0.1/8188) >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! (echo >/dev/tcp/127.0.0.1/8188) >/dev/null 2>&1; then
  echo "[h3][error] authenticated TLS proxy did not start" >&2
  exit 1
fi
