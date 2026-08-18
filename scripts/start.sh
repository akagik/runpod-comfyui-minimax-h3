#!/usr/bin/env bash
set -Eeuo pipefail

mode="${MODE_TO_RUN:-serverless}"
comfyui_dir="${COMFYUI_DIR:-/opt/ComfyUI}"
app_dir="${APP_DIR:-/opt/runpod-comfyui}"
python_bin="${VIRTUAL_ENV:-/opt/comfyui-venv}/bin/python"
host="${COMFY_HOST:-127.0.0.1}"
port="${COMFY_PORT:-8188}"

case "$mode" in
  pod|serverless) ;;
  *)
    echo "Invalid MODE_TO_RUN=$mode (expected pod or serverless)" >&2
    exit 2
    ;;
esac

if [[ -n "${RUNPOD_VOLUME_ROOT:-}" ]]; then
  volume_root="$RUNPOD_VOLUME_ROOT"
elif [[ -d /runpod-volume/models ]]; then
  volume_root=/runpod-volume
elif [[ -d /workspace/models ]]; then
  volume_root=/workspace
elif [[ "$mode" == serverless ]]; then
  volume_root=/runpod-volume
else
  volume_root=/workspace
fi

if [[ "${REQUIRE_NETWORK_VOLUME:-true}" == true && ! -d "$volume_root" ]]; then
  echo "Required Network Volume is not mounted at $volume_root" >&2
  exit 3
fi

worker_id="${RUNPOD_POD_ID:-${RUNPOD_WORKER_ID:-${HOSTNAME:-worker}}}"
worker_id="$(printf '%s' "$worker_id" | tr -cd 'A-Za-z0-9._-')"
[[ -n "$worker_id" ]] || worker_id=worker

if [[ "$mode" == serverless ]]; then
  runtime_root="${COMFY_RUNTIME_ROOT:-/tmp/comfyui/$worker_id}"
  input_dir="${COMFY_INPUT_DIR:-$runtime_root/input}"
  temp_dir="${COMFY_TEMP_DIR:-$runtime_root/temp}"
  user_dir="${COMFY_USER_DIR:-$runtime_root/user}"
  output_dir="${COMFY_OUTPUT_DIR:-$volume_root/serverless/outputs/$worker_id}"
  listen_host="127.0.0.1"
else
  runtime_root="${COMFY_RUNTIME_ROOT:-$volume_root/pods/$worker_id}"
  input_dir="${COMFY_INPUT_DIR:-$volume_root/ComfyUI/input}"
  temp_dir="${COMFY_TEMP_DIR:-$runtime_root/temp}"
  user_dir="${COMFY_USER_DIR:-$runtime_root/user}"
  output_dir="${COMFY_OUTPUT_DIR:-$runtime_root/outputs}"
  listen_host="${COMFY_LISTEN_HOST:-0.0.0.0}"
fi

database_url="${COMFY_DATABASE_URL:-sqlite:///$user_dir/comfyui.db}"
log_dir="${COMFY_LOG_DIR:-$runtime_root/logs}"

mkdir -p "$input_dir" "$temp_dir" "$user_dir/default/workflows" "$output_dir" "$log_dir"

export RUNPOD_VOLUME_ROOT="$volume_root"
export COMFY_INPUT_DIR="$input_dir"
export COMFY_TEMP_DIR="$temp_dir"
export COMFY_USER_DIR="$user_dir"
export COMFY_OUTPUT_DIR="$output_dir"
export HF_HOME="${HF_HOME_OVERRIDE:-$volume_root/hf-cache}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE_OVERRIDE:-$HF_HOME/hub}"

if [[ "${REQUIRE_MINIMAX_MODELS:-true}" == true ]]; then
  required_models=(
    "models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    "models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    "models/vae/minimax_h3_video_vae_fp16.safetensors"
    "models/vae/minimax_h3_audio_vae_fp32.safetensors"
  )
  missing=0
  for relative_path in "${required_models[@]}"; do
    if [[ ! -s "$volume_root/$relative_path" ]]; then
      echo "Missing required model: $volume_root/$relative_path" >&2
      missing=1
    fi
  done
  (( missing == 0 )) || exit 4
fi

setup_ssh() {
  [[ -n "${PUBLIC_KEY:-}" ]] || return 0
  install -d -m 700 /root/.ssh
  printf '%s\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  ssh-keygen -A
  service ssh start
  echo "SSH server started"
}

setup_ssh

comfy_args=(
  "$comfyui_dir/main.py"
  --disable-auto-launch
  --listen "$listen_host"
  --port "$port"
  --input-directory "$input_dir"
  --output-directory "$output_dir"
  --temp-directory "$temp_dir"
  --user-directory "$user_dir"
  --database-url "$database_url"
  --extra-model-paths-config "$app_dir/config/extra_model_paths.yaml"
  --cache-none
  --disable-pinned-memory
)

echo "mode=$mode"
echo "volume_root=$volume_root"
echo "input_dir=$input_dir"
echo "output_dir=$output_dir"
echo "user_dir=$user_dir"
echo "memory_safety=--cache-none --disable-pinned-memory"

if [[ "$mode" == pod ]]; then
  exec "$python_bin" -u "${comfy_args[@]}"
fi

cleanup() {
  if [[ -n "${comfy_pid:-}" ]]; then
    kill "$comfy_pid" 2>/dev/null || true
    wait "$comfy_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"$python_bin" -u "${comfy_args[@]}" &
comfy_pid=$!
printf '%s\n' "$comfy_pid" > /tmp/comfyui.pid

handler_args=()
if [[ "${SERVE_API_LOCALLY:-false}" == true ]]; then
  handler_args+=(--rp_serve_api --rp_api_host=0.0.0.0)
fi

"$python_bin" -u "$app_dir/worker/handler.py" "${handler_args[@]}"

