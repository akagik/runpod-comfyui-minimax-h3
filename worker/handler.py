"""RunPod Serverless queue handler for a local ComfyUI process.

The worker accepts ComfyUI API-format workflows. Large models and generated
artifacts remain on the attached Network Volume; responses contain metadata and
volume paths instead of base64-encoding video output.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import time
from typing import Any
import uuid

import requests
import runpod


COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"
COMFY_PID_FILE = Path("/tmp/comfyui.pid")
VOLUME_ROOT = Path(os.environ.get("RUNPOD_VOLUME_ROOT", "/runpod-volume"))
INPUT_ROOT = Path(os.environ.get("COMFY_INPUT_DIR", "/tmp/comfyui/input"))
OUTPUT_ROOT = Path(os.environ.get("COMFY_OUTPUT_DIR", "/runpod-volume/serverless/outputs"))
TEMP_ROOT = Path(os.environ.get("COMFY_TEMP_DIR", "/tmp/comfyui/temp"))
READY_TIMEOUT = int(os.environ.get("COMFY_READY_TIMEOUT_SECONDS", "300"))
JOB_TIMEOUT = int(os.environ.get("COMFY_JOB_TIMEOUT_SECONDS", "7200"))
POLL_SECONDS = float(os.environ.get("COMFY_POLL_SECONDS", "1"))
MAX_INPUT_BYTES = int(os.environ.get("MAX_INPUT_BYTES", str(20 * 1024 * 1024)))
IMAGE_VERSION = os.environ.get("RUNPOD_COMFYUI_IMAGE_VERSION", "unknown")


class WorkerInputError(ValueError):
    """An invalid job payload that should be returned as a user error."""


def _pid_is_alive() -> bool | None:
    try:
        pid = int(COMFY_PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_for_comfyui() -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    last_error = "not checked"
    while time.monotonic() < deadline:
        if _pid_is_alive() is False:
            raise RuntimeError("ComfyUI process exited during startup")
        try:
            response = requests.get(f"{COMFY_URL}/system_stats", timeout=5)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise TimeoutError(f"ComfyUI did not become ready: {last_error}")


def _safe_child(root: Path, relative_name: str) -> Path:
    if not isinstance(relative_name, str) or not relative_name.strip():
        raise WorkerInputError("Input filename/path must be a non-empty string")
    relative = Path(relative_name)
    if relative.is_absolute():
        raise WorkerInputError(f"Absolute paths are not allowed: {relative_name}")
    root_resolved = root.resolve()
    candidate = (root / relative).resolve(strict=False)
    if not candidate.is_relative_to(root_resolved):
        raise WorkerInputError(f"Path escapes its allowed root: {relative_name}")
    return candidate


def _decode_image(encoded: str) -> bytes:
    if not isinstance(encoded, str):
        raise WorkerInputError("images[].image must be a base64 string")
    if encoded.startswith("data:"):
        try:
            encoded = encoded.split(",", 1)[1]
        except IndexError as exc:
            raise WorkerInputError("Malformed base64 data URI") from exc
    if len(encoded) > ((MAX_INPUT_BYTES + 2) // 3) * 4 + 16:
        raise WorkerInputError(f"Encoded input exceeds MAX_INPUT_BYTES={MAX_INPUT_BYTES}")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WorkerInputError("Invalid base64 image") from exc
    if len(data) > MAX_INPUT_BYTES:
        raise WorkerInputError(f"Decoded input exceeds MAX_INPUT_BYTES={MAX_INPUT_BYTES}")
    return data


def _stage_inputs(job_input: dict[str, Any]) -> list[Path]:
    """Stage request inputs into ComfyUI's worker-local input directory."""
    staged: list[Path] = []
    try:
        images = job_input.get("images", [])
        if not isinstance(images, list):
            raise WorkerInputError("images must be a list")
        for image in images:
            if not isinstance(image, dict) or "name" not in image or "image" not in image:
                raise WorkerInputError("Each images entry needs name and image")
            destination = _safe_child(INPUT_ROOT, image["name"])
            if destination.exists() or destination.is_symlink():
                raise WorkerInputError(f"Input destination already exists: {image['name']}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_decode_image(image["image"]))
            staged.append(destination)

        volume_inputs = job_input.get("volume_inputs", [])
        if not isinstance(volume_inputs, list):
            raise WorkerInputError("volume_inputs must be a list")
        for item in volume_inputs:
            if not isinstance(item, dict) or "path" not in item:
                raise WorkerInputError("Each volume_inputs entry needs path")
            source = _safe_child(VOLUME_ROOT, item["path"])
            if not source.is_file():
                raise WorkerInputError(f"Network Volume input does not exist: {item['path']}")
            destination_name = item.get("name") or source.name
            destination = _safe_child(INPUT_ROOT, destination_name)
            if destination.exists() or destination.is_symlink():
                raise WorkerInputError(f"Input destination already exists: {destination_name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source)
            staged.append(destination)

        return staged
    except Exception:
        _cleanup_staged(staged)
        raise


def _cleanup_staged(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
        except OSError as exc:
            print(f"warning: failed to clean staged input {path}: {exc}", flush=True)


def _parse_workflow(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise WorkerInputError("workflow is not valid JSON") from exc
    if not isinstance(value, dict) or not value:
        raise WorkerInputError("workflow must be a non-empty ComfyUI API-format object")
    return value


def _queue_workflow(workflow: dict[str, Any]) -> str:
    client_id = str(uuid.uuid4())
    response = requests.post(
        f"{COMFY_URL}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=30,
    )
    if response.status_code >= 400:
        raise WorkerInputError(
            f"ComfyUI rejected workflow (HTTP {response.status_code}): "
            f"{response.text[:2000]}"
        )
    payload = response.json()
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI response has no prompt_id: {payload}")
    return str(prompt_id)


def _wait_for_history(prompt_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + JOB_TIMEOUT
    while time.monotonic() < deadline:
        if _pid_is_alive() is False:
            raise RuntimeError(
                "ComfyUI process exited while executing the workflow; check cgroup OOM metrics"
            )
        response = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=30)
        response.raise_for_status()
        history = response.json()
        if prompt_id in history:
            record = history[prompt_id]
            status = record.get("status", {})
            if status.get("status_str") == "error":
                messages = status.get("messages", [])
                raise RuntimeError(
                    "ComfyUI execution failed: "
                    + json.dumps(messages, ensure_ascii=False)[:4000]
                )
            return record
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"Workflow exceeded COMFY_JOB_TIMEOUT_SECONDS={JOB_TIMEOUT}")


def _artifact_root(artifact_type: str) -> Path:
    return {
        "output": OUTPUT_ROOT,
        "temp": TEMP_ROOT,
        "input": INPUT_ROOT,
    }.get(artifact_type, OUTPUT_ROOT)


def _collect_artifacts(record: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for node_id, node_output in record.get("outputs", {}).items():
        if not isinstance(node_output, dict):
            continue
        for output_name, values in node_output.items():
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict) or "filename" not in value:
                    continue
                artifact_type = str(value.get("type", "output"))
                subfolder = str(value.get("subfolder", ""))
                relative = str(Path(subfolder) / str(value["filename"]))
                path = _safe_child(_artifact_root(artifact_type), relative)
                item: dict[str, Any] = {
                    "node_id": str(node_id),
                    "output_name": str(output_name),
                    "filename": str(value["filename"]),
                    "subfolder": subfolder,
                    "type": artifact_type,
                    "path": str(path),
                    "exists": path.is_file(),
                }
                if path.is_file():
                    item["size_bytes"] = path.stat().st_size
                try:
                    item["volume_relative_path"] = str(path.resolve().relative_to(VOLUME_ROOT.resolve()))
                except ValueError:
                    pass
                artifacts.append(item)
    return artifacts


def _read_first(paths: list[str]) -> str | None:
    for name in paths:
        try:
            return Path(name).read_text().strip()
        except OSError:
            continue
    return None


def _cgroup_memory() -> dict[str, str | None]:
    return {
        "limit": _read_first(
            ["/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory.max"]
        ),
        "usage": _read_first(
            ["/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory.current"]
        ),
        "peak": _read_first(
            ["/sys/fs/cgroup/memory/memory.max_usage_in_bytes", "/sys/fs/cgroup/memory.peak"]
        ),
        "events": _read_first(
            ["/sys/fs/cgroup/memory/memory.oom_control", "/sys/fs/cgroup/memory.events"]
        ),
    }


def _runtime_diagnostics() -> dict[str, Any]:
    """Return bounded, non-secret runtime information for a paid smoke test."""
    import torch

    required_models = [
        "models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "models/vae/minimax_h3_video_vae_fp16.safetensors",
        "models/vae/minimax_h3_audio_vae_fp32.safetensors",
    ]
    models = []
    for relative_name in required_models:
        path = _safe_child(VOLUME_ROOT, relative_name)
        models.append(
            {
                "path": relative_name,
                "exists": path.is_file(),
                "size_bytes": path.stat().st_size if path.is_file() else None,
            }
        )

    cuda_available = torch.cuda.is_available()
    gpu: dict[str, Any] = {
        "cuda_available": cuda_available,
        "device_count": torch.cuda.device_count(),
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
    }
    if cuda_available:
        properties = torch.cuda.get_device_properties(0)
        gpu.update(
            {
                "name": properties.name,
                "total_memory_bytes": properties.total_memory,
                "compute_capability": f"{properties.major}.{properties.minor}",
            }
        )

    return {
        "operation": "diagnostics",
        "image_version": IMAGE_VERSION,
        "comfyui_ready": True,
        "gpu": gpu,
        "network_volume": {
            "mounted": VOLUME_ROOT.is_dir(),
            "required_models": models,
        },
        "cgroup_memory": _cgroup_memory(),
    }


def handler(event: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    job_input = event.get("input")
    if not isinstance(job_input, dict):
        raise WorkerInputError("event.input must be an object")

    operation = job_input.get("operation", "generate")
    if operation not in {"diagnostics", "generate"}:
        raise WorkerInputError("input.operation must be diagnostics or generate")

    _wait_for_comfyui()
    if operation == "diagnostics":
        return _runtime_diagnostics()

    staged: list[Path] = []
    try:
        staged = _stage_inputs(job_input)
        workflow = _parse_workflow(job_input.get("workflow"))
        prompt_id = _queue_workflow(workflow)
        record = _wait_for_history(prompt_id)
        artifacts = _collect_artifacts(record)
        return {
            "operation": "generate",
            "runpod_job_id": event.get("id"),
            "prompt_id": prompt_id,
            "execution_seconds": round(time.monotonic() - started, 3),
            "artifacts": artifacts,
            "cgroup_memory": _cgroup_memory(),
        }
    finally:
        _cleanup_staged(staged)


if __name__ == "__main__":
    runpod.serverless.start(
        {
            "handler": handler,
            "concurrency_modifier": lambda current: 1,
        }
    )
