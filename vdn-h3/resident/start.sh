#!/usr/bin/env bash
set -Eeuo pipefail

# Pod-only supervisor: losing ComfyUI must not lose the SSH/container lifecycle.
# No automatic model downloads, generation, restart, unload, or GPU release.
[[ "${MODE_TO_RUN:-pod}" == pod ]] || { echo 'Resident image is Pod-only'; exit 2; }
volume_root="${RUNPOD_VOLUME_ROOT:-/workspace}"
[[ -d "$volume_root" ]] || { echo 'Persistent storage is missing'; exit 3; }
worker_id="${RUNPOD_POD_ID:?RUNPOD_POD_ID must be set}"
[[ "$worker_id" =~ ^[A-Za-z0-9]+$ ]] || exit 3
runtime_root="$volume_root/pods/$worker_id"
export COMFY_INPUT_DIR="${COMFY_INPUT_DIR:-$volume_root/ComfyUI/input}"
export COMFY_OUTPUT_DIR="${COMFY_OUTPUT_DIR:-$runtime_root/outputs}"
export COMFY_TEMP_DIR="${COMFY_TEMP_DIR:-$runtime_root/temp}"
export COMFY_USER_DIR="${COMFY_USER_DIR:-$runtime_root/user}"
export HF_HOME="${HF_HOME_OVERRIDE:-$volume_root/hf-cache}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE_OVERRIDE:-$HF_HOME/hub}"
export VDN_RESIDENT_LOG="$volume_root/vdn-h3/logs/resident-sampler.jsonl"
log_dir="$volume_root/vdn-h3/logs"
mkdir -p "$COMFY_INPUT_DIR" "$COMFY_OUTPUT_DIR" "$COMFY_TEMP_DIR" \
  "$COMFY_USER_DIR/default/workflows" "$log_dir"

if [[ -n "${PUBLIC_KEY:-}" ]]; then
  install -d -m 700 /root/.ssh
  printf '%s\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  ssh-keygen -A
  service ssh start
fi

comfy_log="$log_dir/comfy-resident-$(date -u +%Y%m%dT%H%M%SZ).log"
comfy_pid=''
cleanup() {
  if [[ -n "$comfy_pid" ]] && kill -0 "$comfy_pid" 2>/dev/null; then
    kill -TERM "$comfy_pid"
    wait "$comfy_pid" || true
  fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT

/opt/comfyui-venv/bin/python -u "${COMFYUI_DIR:-/opt/ComfyUI-vdn}/main.py" \
  --disable-auto-launch --listen "${COMFY_LISTEN_HOST:-0.0.0.0}" \
  --port "${COMFY_PORT:-8188}" \
  --input-directory "$COMFY_INPUT_DIR" --output-directory "$COMFY_OUTPUT_DIR" \
  --temp-directory "$COMFY_TEMP_DIR" --user-directory "$COMFY_USER_DIR" \
  --database-url "${COMFY_DATABASE_URL:-sqlite:///$COMFY_USER_DIR/comfyui.db}" \
  --extra-model-paths-config /opt/runpod-comfyui/config/extra_model_paths.yaml \
  --cache-classic --disable-pinned-memory >>"$comfy_log" 2>&1 &
comfy_pid=$!
printf '%s\n' "$comfy_pid" > "$runtime_root/comfy-resident.pid"
echo "VDN resident ComfyUI PID=$comfy_pid log=$comfy_log"
echo 'cache=classic; pinned_memory=disabled; VRAM policy and dtypes unchanged'
result=0
wait "$comfy_pid" || result=$?
echo "ComfyUI exited ($result). Container and SSH remain up; no automatic restart."
comfy_pid=''
while true; do sleep 60 & wait $! || true; done
