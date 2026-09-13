# VDN-H3 8-step / RunPod ComfyUI

## Startup model provisioning (0.1.2)

See [AUTO_MODELS.md](AUTO_MODELS.md) for the new pinned, missing-only model
bootstrap and required template settings. It inherits the 0.1.1 resident image,
checks mounted storage and contractual capacity, and gates ComfyUI startup on
verification. It does not change inference settings or restart existing Pods.
The original 0.1.0 instructions below are historical.

## Status and boundaries

Prepared 2026-09-13. See `VERIFICATION.md` for actual completed checks.
This is the **ComfyUI port**, not OpenVDN's tuned Diffusers/FP8/FlashAttention4
runtime. No speed or quality claim is made before a real GPU test.
No WAN, Anima, Sol-H3 or FastH3 weights/adapters are used.
The image contains no weights, private prompts, source images, keys or tokens.

## Pinned stack

- Parent: `ghcr.io/akagik/runpod-comfyui-minimax-h3:0.1.4@sha256:09e695790b1eb815a92c382f384313712c621172e031dd2cf136a0a7a9ea053e`
- ComfyUI `a20738f1d345e2695429544b5defea672dd74486` (2026-09-12),
  installed separately at `/opt/ComfyUI-vdn`. The parent's August ComfyUI lacks
  `AttentionTensorContainer` and cannot import the current VDN port.
- Required package deltas: frontend 1.52.7, workflow templates 0.11.59,
  embedded docs 0.5.11, comfy-kitchen 0.2.33, comfy-aimdo 0.5.3; inherited av 18.0.0.
  Template transitive dependencies are pinned: core 0.3.339, json 0.1.74,
  media-assets-01 0.1.44. Updating only the templates meta-package fails pip check.
- Python 3.12, PyTorch 2.13.0 / CUDA 13.0 (inherited lock).
- [ComfyUI-VDN-H3](https://github.com/Saganaki22/ComfyUI-VDN-H3)
  `3eb63496c24ca70faaf8a14b6c75fcb480e34bf1` (v1.5.2).
- [VDN weights](https://huggingface.co/OpenVDN/vdn-minimax-h3):
  `51eeecefdb5b524c0df5539446d1dd54a17aa439`, stage-dmd-step-250.
- [Comfy base models](https://huggingface.co/Comfy-Org/MiniMax-H3):
  `a98869194787969724c7425d95d0ed73ce9202af`.
- FL2VA base and Qwen text encoder: BF16. Video VAE: FP16; audio VAE: FP32.
- VDN linear branch + default and turbo adapters. Strength 1, `merge`,
  `cache_gpu`, grouped attention. No INT8/FP8 fallback. No Sol attention patch.
- `er_sde` / beta / 8 ComfyUI sampler steps. Official Diffusers examples use
  `num_inference_steps=9` for 8 evaluations; do not copy 9 into this workflow.
- I2VA 1344x768, 124 frames, 24 fps (~5.17 s); one first-frame image.
  This is an idle-motion test, not a guarantee of a seamless last-to-first loop.

## Build (no paid GPU)

```bash
node scripts/lock_models.mjs  # metadata only; normally reuse committed lock
docker buildx build --platform linux/amd64 --load \
  -t ghcr.io/akagik/runpod-comfyui-vdn-h3:0.1.0 .
```

`models.lock.json` is the reusable download index: exact repository revision,
destination, size, SHA256 and download URL for each of 12 files. It omits
the unused official h3-base, stage-b, and Diffusers Python runtime.
Do not re-inventory or download complete model repositories next time.

## Before creating a Pod

Obtain explicit approval for current GPU/DC, pricing and storage. Recheck live
stock. Keep existing Pods/Volumes untouched. Do not change quantization or use
large CPU offload without approval. Confirm host driver supports CUDA 13.0.

Review the [model license](https://huggingface.co/OpenVDN/vdn-minimax-h3/blob/main/LICENSE):
EU, UK, South Korea and USA are excluded. Do not deploy there without a separate
license. Iceland is not an EU member; the data center's actual country matters.
This is not legal advice; the license holder must confirm their own eligibility.

Use a model-capable GPU with sufficient VRAM and cgroup RAM. H200 is a candidate
for this ComfyUI BF16 test, but is not claimed tested. Budget at least 200 GB
model storage, plus separate image/container storage. A network volume can only
attach in its own DC. Existing EUR-IS-1 storage cannot attach in EUR-IS-4/5.

Mount model storage at `/workspace`. Pod env: `MODE_TO_RUN=pod`,
`RUNPOD_VOLUME_ROOT=/workspace`, `REQUIRE_MINIMAX_MODELS=false`.
Ports: 8188/http and 22/tcp. Supply the account public SSH key through RunPod,
not a Docker layer. No secrets belong in versioned template examples.

## On the approved Pod

The inherited entrypoint starts SSH and ComfyUI before model files are present.
The parent's old INT8 gate is disabled, not satisfied with substitute weights.
Manager loader checks and the SHA256 preflight below guard actual readiness.

```bash
python /opt/vdn-h3/scripts/download_models.py --download --accept-model-license
python /opt/vdn-h3/scripts/download_models.py
```

Run downloads inside a persistent session, retain the log on `/workspace` and
inspect cgroup RAM limit/usage/peak/oom events, not host `free` alone.
The downloader resumes missing files, verifies SHA256, and refuses to overwrite
unexpected existing files. It avoids a second full Hugging Face model cache.
ComfyUI discovers `/workspace/models/vdn/stage-dmd-step-250` from its model roots.

## Manager

```bash
rcmctl workflow-import workflows/minimax-h3-i2va-vdn-bf16-8step-v1.json --json
rcmctl health --json
rcmctl pods --json
rcmctl workflow minimax-h3-i2va-vdn-bf16-8step 1 --json
```

Use profile `minimax-h3-vdn-bf16-8step`, mode `i2va`. This is a separate immutable
workflow; do not overwrite `minimax-h3-i2va-native` or carry its 20 steps over.
Validate and preview an approved request MD, then submit through `rcmctl` and
watch the returned job to `COMPLETED`. Direct SSH/ComfyUI generation is not the
Manager path. Workflow registration alone does not start generation.

## Known pitfalls / test plan

1. VDN is hybrid attention + branch weights + TWO adapters, not turbo LoRA alone.
2. 8-step DMD requires merge; bypass causes rounding drift. Keep advanced
   `fast_kernels` off. Do not stack FastH3, LightX or ScheduledSolAttention.
3. The upstream UI example has quantized filenames and optional KJ nodes;
   this workflow deliberately uses BF16 and only required VDN/core nodes.
4. Adapters use `adapter_spec.json`; older ports looking for adapter_config.json
   fail. Pin v1.5.2. Do not float custom nodes to main during paid testing.
5. First run includes model load and initialization. Record wall-clock job time,
   sampling time, save time, peak VRAM and cgroup RAM; repeat the identical seed
   once warm only within the approved test budget.
6. Check output size, duration, video/audio streams, face/background stability
   and idle-loop continuity. No music is a prompt constraint, not a guarantee;
   listen to the produced audio before reporting it satisfied.
7. For a short 124f clip, the softmax window may cover the full latent sequence.
   Do not infer long-video speedups from the headline 8-GPU benchmark.
8. Keep the Pod and loaded model available after testing. No automatic Stop,
   Terminate, model unload, Volume removal or unapproved retry.

Source research: [OpenVDN implementation](https://github.com/OpenVDN/vdn-minimax-h3),
[Comfy port](https://github.com/Saganaki22/ComfyUI-VDN-H3).
