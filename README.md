# RunPod ComfyUI + MiniMax H3

Model-free, reproducible Docker image for the MiniMax H3 native ComfyUI
workflows used on RunPod. One image supports two modes:

- `MODE_TO_RUN=pod`: exposes the ComfyUI Web UI/API on port 8188.
- `MODE_TO_RUN=serverless`: starts ComfyUI internally and exposes a RunPod
  queue handler.

The image intentionally contains no checkpoint, diffusion model, text
encoder, VAE, input media, output media, Hugging Face cache, or secret.

## Status and scope

Phase 1 (reproducible ComfyUI runtime) and the Phase 2 queue handler are in
this repository. Before production rollout, the published image still needs
one paid GPU smoke test as a normal Pod and one Serverless endpoint test.

The reference Pod was inspected read-only. Dockerization did not stop,
restart, upgrade, or modify its ComfyUI process, venv, Network Volume, models,
inputs, outputs, or queue.

## Build

RunPod uses x86-64 Linux workers, so always build `linux/amd64`:

```bash
docker build \
  --platform linux/amd64 \
  --tag ghcr.io/akagik/runpod-comfyui-minimax-h3:0.1.2 \
  .
```

The base image and application versions are pinned in `Dockerfile` and
`VERSION_MATRIX.md`. `requirements.lock.txt` is generated from
`requirements.in` for Python 3.12 on manylinux x86-64.

## Local / Pod test

CPU-only inspection (ComfyUI generation will not work):

```bash
docker run --rm \
  --entrypoint /opt/runpod-comfyui/scripts/image-smoke-test.sh \
  ghcr.io/akagik/runpod-comfyui-minimax-h3:0.1.2
```

GPU Pod/UI mode with a volume mounted as `/workspace`:

```bash
docker run --rm --gpus all \
  -e MODE_TO_RUN=pod \
  -e RUNPOD_VOLUME_ROOT=/workspace \
  -e REQUIRE_NETWORK_VOLUME=true \
  -e REQUIRE_MINIMAX_MODELS=true \
  -p 8188:8188 \
  -v /path/to/network-volume:/workspace \
  ghcr.io/akagik/runpod-comfyui-minimax-h3:0.1.2
```

For a RunPod Pod:

- Container image: `ghcr.io/akagik/runpod-comfyui-minimax-h3:0.1.2`
- Network Volume mount: `/workspace`
- HTTP port: `8188/http`
- Optional SSH port: `22/tcp`
- Required environment: `MODE_TO_RUN=pod`
- Optional SSH environment: `PUBLIC_KEY=<public key only>`

Never put a private SSH key in `PUBLIC_KEY`.

## Network Volume model layout

The same existing files work in both modes. RunPod mounts the volume at
`/workspace` for Pods and `/runpod-volume` for Serverless; both are declared
in `config/extra_model_paths.yaml`.

```text
<volume-root>/
├── models/
│   ├── diffusion_models/
│   │   └── minimax_h3_ref2va_pruned_int8_convrot.safetensors
│   ├── text_encoders/
│   │   └── qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
│   └── vae/
│       ├── minimax_h3_video_vae_fp16.safetensors
│       └── minimax_h3_audio_vae_fp32.safetensors
├── ComfyUI/input/                 # existing Pod-side input library
├── hf-cache/
├── pods/<pod-id>/outputs/         # Pod mode
└── serverless/outputs/<worker-id>/ # Serverless mode
```

Startup only checks that the four model files exist and are non-empty. It does
not download, copy, rewrite, hash, or delete them.

## RunPod Serverless

Template settings:

- Container image: use an immutable version tag or digest, never `latest`.
- Network Volume: attach the existing volume; it appears at `/runpod-volume`.
- Environment: `MODE_TO_RUN=serverless`.
- Environment: `REQUIRE_NETWORK_VOLUME=true`.
- Environment: `REQUIRE_MINIMAX_MODELS=true`.
- GPUs/worker: 1.
- Concurrency/worker: 1.
- Active workers: 0 if scale-to-zero is desired.
- FlashBoot: enable after the image passes a real endpoint smoke test.

The queue handler accepts a ComfyUI **API-format** workflow. Because MiniMax
H3 video jobs take minutes, use `/run`, then poll `/status`; do not depend on a
long-lived `/runsync` request.

Example payload using an existing Network Volume reference image:

```json
{
  "input": {
    "workflow": { "replace": "with workflows/minimax_h3_r2va_api_1344x768.json" },
    "volume_inputs": [
      {
        "path": "references/reference.png",
        "name": "reference.png"
      }
    ]
  }
}
```

Submit it:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @request.json \
  "https://api.runpod.ai/v2/$RUNPOD_ENDPOINT_ID/run"
```

The response contains the ComfyUI `prompt_id`, execution time, cgroup memory
snapshot, and output metadata such as:

```json
{
  "artifacts": [
    {
      "filename": "minimax-h3-r2va_00001_.mp4",
      "type": "output",
      "volume_relative_path": "serverless/outputs/<worker>/video/minimax-h3-r2va_00001_.mp4",
      "size_bytes": 12345678
    }
  ]
}
```

Video bytes are not returned as base64. This avoids Serverless response limits
and keeps large outputs on the Network Volume. Retrieve them via a temporary
Pod or the Network Volume S3-compatible API.

Small references can instead be sent in `input.images` as
`{"name":"reference.png","image":"<base64>"}`. The default decoded limit is
20 MiB (`MAX_INPUT_BYTES`). `volume_inputs` is preferred.

### Local queue-handler test

On a GPU machine, set `SERVE_API_LOCALLY=true` to make the RunPod SDK expose
its local test API on port 8000:

```bash
docker run --rm --gpus all \
  -e MODE_TO_RUN=serverless \
  -e SERVE_API_LOCALLY=true \
  -e RUNPOD_VOLUME_ROOT=/runpod-volume \
  -p 8000:8000 \
  -v /path/to/network-volume:/runpod-volume \
  ghcr.io/akagik/runpod-comfyui-minimax-h3:0.1.2
```

## GHCR publish

GitHub Actions builds and pushes only `linux/amd64`. A release tag creates a
semantic GHCR tag:

```bash
git tag v0.1.2
git push origin main v0.1.2
```

The workflow authenticates with GitHub's short-lived `GITHUB_TOKEN`; no PAT,
RunPod API key, Hugging Face token, or SSH key is stored in the repository or
image. Main-branch builds also receive `edge` and immutable `sha-...` tags.

If the GHCR package is private, create a RunPod Container Registry Credential
with a GitHub token that has only `read:packages`, then select it in the
template. A public package needs no registry credential.

## Runtime settings

| Variable | Default | Purpose |
|---|---|---|
| `MODE_TO_RUN` | `serverless` | `serverless` or `pod` |
| `RUNPOD_VOLUME_ROOT` | auto | Override `/runpod-volume` or `/workspace` |
| `REQUIRE_NETWORK_VOLUME` | `true` | Fail fast without the volume |
| `REQUIRE_MINIMAX_MODELS` | `true` | Fail fast if any required model is absent |
| `COMFY_JOB_TIMEOUT_SECONDS` | `7200` | Maximum workflow runtime |
| `COMFY_READY_TIMEOUT_SECONDS` | `300` | Maximum ComfyUI boot time |
| `MAX_INPUT_BYTES` | `20971520` | Per base64 input limit |
| `SERVE_API_LOCALLY` | `false` | RunPod SDK local API on port 8000 |

Both modes always use `--cache-none --disable-pinned-memory`. This is required
because the reference A100 Pod has a 125 GB cgroup RAM limit, while host-level
RAM metrics can show about 1 TiB. The reference container had one historical
cgroup OOM kill before these flags were applied.

## Versions

See `VERSION_MATRIX.md`. Important pins:

- Ubuntu 24.04 / CUDA runtime 13.0
- Python 3.12
- PyTorch 2.13.0
- ComfyUI 0.30.2 at commit
  `dec5d9450a5290bcf63430409ea41018e67f41c3`
- RunPod SDK 1.7.12
- Local node `MiniMaxH3FirstLastAudioToVideo`

`ComfyUI-Spectrum-MiniMax-H3`, xformers, and flash-attn are deliberately not
installed.

## Known issues and remaining validation

- Do not deploy `0.1.0` or `0.1.1`: paid RTX PRO 6000 smoke tests found that
  Triton's first-use JIT requires both a C compiler and Python development
  headers. `0.1.2` includes Ubuntu `build-essential` plus `python3.12-dev`, and
  checks both `cc` and `Python.h` in the image smoke test.
- CUDA 13/PyTorch 2.13 requires a sufficiently new NVIDIA host driver. The
  reference A100 uses driver 580.159.04. Fail the GPU smoke test rather than
  silently falling back to CPU.
- A100 is the reference environment. B200/B300 compatibility is intended by
  the CUDA/PyTorch choice but must be verified on those GPUs before production.
- The first worker cold start still includes image pull, ComfyUI import, model
  reads from the Network Volume, and VRAM loading. Docker cannot eliminate
  model-load time.
- One worker handles one job at a time. This avoids VRAM/RAM contention and
  matches the current ComfyUI usage.
- `workflows/minimax_h3_r2va_api_1344x768.json` is a neutral 1344x768, 124-frame
  no-dialogue smoke template. For dialogue plus action, use 226 frames or more.
- Do not run multiple workers against a shared ComfyUI SQLite database. This
  image uses worker-local DB/user/temp paths and worker-specific output roots.

## Licenses and provenance

ComfyUI is fetched from its official repository at a fixed commit during the
image build and retains its upstream GPL-3.0 license. Third-party Python and
NVIDIA components retain their own licenses. The RunPod queue protocol follows
the official `runpod-workers/worker-comfyui` design, but its AGPL handler source
is not copied into this repository.
