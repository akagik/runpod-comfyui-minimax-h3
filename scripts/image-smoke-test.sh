#!/usr/bin/env bash
set -Eeuo pipefail

python_bin="${VIRTUAL_ENV:-/opt/comfyui-venv}/bin/python"
comfyui_dir="${COMFYUI_DIR:-/opt/ComfyUI}"

"$python_bin" - <<'PY'
import importlib
import torch

expected = {
    "torch": "2.13.0",
    "torchvision": "0.28.0",
    "torchaudio": "2.11.0",
}
for name, version in expected.items():
    module = importlib.import_module(name)
    actual = module.__version__.split("+")[0]
    assert actual == version, (name, actual, version)

print("torch", torch.__version__)
print("torch.version.cuda", torch.version.cuda)
print("torch.cuda.is_available", torch.cuda.is_available())
if torch.cuda.is_available():
    tensor = (torch.zeros(8, device="cuda") + 1).sum()
    torch.cuda.synchronize()
    print("gpu", torch.cuda.get_device_name(0), "kernel_sum", tensor.item())
PY

test -f "$comfyui_dir/main.py"
test -f "$comfyui_dir/custom_nodes/minimax_h3_flref_audio.py"
ffmpeg -version | head -1
python -m pip check

if find "$comfyui_dir/models" -type f -size +1M -print -quit | grep -q .; then
  echo "Unexpected large model file is present in the image" >&2
  exit 1
fi

echo "image smoke test: OK"

