#!/usr/bin/env bash
set -Eeuo pipefail

# SSH first: download failures must never lock operators out of a paid Pod.
if [[ -n "${PUBLIC_KEY:-}" ]]; then
  install -d -m 700 /root/.ssh
  printf '%s\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  ssh-keygen -A
  service ssh start
fi

bootstrap_pid=''
cleanup() {
  if [[ -n "$bootstrap_pid" ]] && kill -0 "$bootstrap_pid" 2>/dev/null; then
    kill -TERM "$bootstrap_pid"
    wait "$bootstrap_pid" || true
  fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT

volume_root="${RUNPOD_VOLUME_ROOT:-/workspace}"
bootstrap_log=/tmp/vdn-model-bootstrap.log
# Do not create /workspace when the persistent mount is absent.
if mountpoint -q "$volume_root" && [[ ! -L "$volume_root" ]]; then
  if [[ ! -L "$volume_root/model-bootstrap" ]]; then
    if mkdir -p "$volume_root/model-bootstrap"; then
      bootstrap_log="$volume_root/model-bootstrap/boot-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
    fi
  fi
fi
echo "Preparing pinned models; progress: $bootstrap_log"
/opt/comfyui-venv/bin/python -u /opt/vdn-h3/scripts/bootstrap_models.py \
  >"$bootstrap_log" 2>&1 &
bootstrap_pid=$!
result=0
wait "$bootstrap_pid" || result=$?
bootstrap_pid=''
if [[ "$result" == 0 ]]; then
  echo 'Models verified. Starting unchanged resident ComfyUI (no automatic generation).'
  exec /opt/vdn-h3/resident/start.sh
fi
echo "Model bootstrap failed ($result). ComfyUI NOT started. SSH remains available."
echo "Inspect $bootstrap_log; correct configuration/capacity and explicitly retry startup."
tail -n 5 "$bootstrap_log" || true
while true; do sleep 60 & wait $! || true; done
