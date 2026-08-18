# Version matrix

Reference environment: RunPod Pod `l91qdpep325vlx`, inspected read-only on
2026-08-19 (Asia/Seoul).

| Component | Pinned version |
|---|---|
| Base | `nvidia/cuda:13.0.0-runtime-ubuntu24.04` amd64 digest `sha256:4757e6477d94d56fd8230e15b7d59956750c983a47a42584f44dbe143090da82` |
| Ubuntu | 24.04 |
| Python | 3.12 |
| PyTorch | 2.13.0 (CUDA 13.0) |
| torchvision | 0.28.0 |
| torchaudio | 2.11.0 |
| triton | 3.7.1 (resolved dependency of PyTorch) |
| C/C++ toolchain | Ubuntu 24.04 `build-essential` + `python3.12-dev` (required for Triton runtime JIT) |
| ComfyUI | 0.30.2, commit `dec5d9450a5290bcf63430409ea41018e67f41c3` |
| ComfyUI frontend | 1.47.12 |
| ComfyUI workflow templates | 0.11.31 |
| comfy-kitchen | 0.2.26 |
| comfy-aimdo | 0.4.11 |
| RunPod Python SDK | 1.7.12 |
| ffmpeg | Ubuntu 24.04 package (6.1.x at reference time) |

Deliberately absent: `xformers`, `flash-attn`, and
`ComfyUI-Spectrum-MiniMax-H3`. None is required by the current MiniMax H3
native workflow.
