#!/usr/bin/env bash
set -Eeuo pipefail

URL="https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors"
TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

measure() {
  local elapsed
  elapsed="$(curl -fsL --max-time 60 -o "$TMP_FILE" -w '%{time_total}' "$URL" 2>/dev/null || true)"
  local bytes
  bytes="$(wc -c < "$TMP_FILE" | tr -d '[:space:]')"
  printf '%s %s\n' "${bytes:-0}" "${elapsed:-0}"
}

read -r bytes seconds < <(measure)
if [[ "${bytes:-0}" -eq 0 ]]; then
  sleep "$((60 + RANDOM % 31))"
  read -r bytes seconds < <(measure)
fi

if [[ "${bytes:-0}" -gt 0 ]] && awk -v seconds="${seconds:-0}" 'BEGIN { exit !(seconds > 0) }'; then
  mbps="$(awk -v bytes="$bytes" -v seconds="$seconds" 'BEGIN { printf "%.2f", bytes * 8 / seconds / 1000000 }')"
  printf 'PROBE_RESULT bytes=%s seconds=%s mbps=%s\n' "$bytes" "$seconds" "$mbps"
else
  printf 'PROBE_RESULT bytes=0 seconds=%s mbps=0.00 blocked=1\n' "${seconds:-0}"
fi
