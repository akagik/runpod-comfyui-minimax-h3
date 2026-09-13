"""VDN-only residency guard and transparent, always-executed sampler.

Inference mathematics and input values are delegated unchanged to the pinned
ComfyUI sampler. This is not a different model or an optimized VDN runtime.
"""
import json
import logging
import os
import time
from pathlib import Path

import torch
from aiohttp import web
from comfy_api.latest import ComfyExtension
from comfy_extras.nodes_custom_sampler import SamplerCustomAdvanced
import comfy.model_management as mm
from server import PromptServer

STATE = {"generation_started": False, "sampler_runs": 0, "loader_signature": None}
LOADER_CLASSES = {"UNETLoader", "CLIPLoader", "VAELoader", "ApplyVDNH3"}


def loader_signature(prompt):
    return sorted(
        json.dumps({"class_type": node["class_type"], "inputs": node["inputs"]}, sort_keys=True)
        for node in prompt.values() if node.get("class_type") in LOADER_CLASSES
    )


def snapshot():
    models = []
    for loaded in list(mm.current_loaded_models):
        patcher = loaded.model
        if patcher is not None:
            models.append({"class": type(patcher.model).__name__,
                           "object_id": id(patcher.model),
                           "loaded_bytes": int(patcher.loaded_size()),
                           "total_bytes": int(patcher.model_size())})
    return {"pid": os.getpid(), "sampler_runs": STATE["sampler_runs"],
            "unload_protected": STATE["generation_started"], "models": models,
            "cuda_allocated_bytes": torch.cuda.memory_allocated() if torch.cuda.is_available() else 0}


def record(event, **data):
    row = {"event": event, "unix_time": time.time(), **snapshot(), **data}
    logging.info("VDN_RESIDENT %s", json.dumps(row, sort_keys=True))
    destination = os.environ.get("VDN_RESIDENT_LOG")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as log:
            log.write(json.dumps(row, sort_keys=True) + "\n")


class VDNH3ResidentSampler(SamplerCustomAdvanced):
    @classmethod
    def define_schema(cls):
        schema = super().define_schema()
        schema.node_id = "VDNH3ResidentSampler"
        schema.display_name = "VDN resident sampler (always execute)"
        return schema

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # A fresh execution even with identical image/prompt/seed. Loader nodes
        # remain cacheable; only sampling and its descendants are invalidated.
        return float("nan")

    @classmethod
    def execute(cls, noise, guider, sampler, sigmas, latent_image):
        STATE["generation_started"] = True
        STATE["sampler_runs"] += 1
        torch.cuda.synchronize()
        started = time.perf_counter()
        record("sampler_start", seed=noise.seed, steps=len(sigmas) - 1,
               diffusion_object_id=id(guider.model_patcher.model))
        try:
            result = super().execute(noise, guider, sampler, sigmas, latent_image)
            torch.cuda.synchronize()
            record("sampler_end", sampler_seconds=time.perf_counter() - started,
                   diffusion_object_id=id(guider.model_patcher.model))
            return result
        except Exception as exc:
            record("sampler_error", error_type=type(exc).__name__)
            raise


@web.middleware
async def resident_guard(request, handler):
    if request.method == "POST" and request.path == "/free" and STATE["generation_started"]:
        record("unload_request_blocked")
        return web.json_response({"error": "VDN resident is protected; unloading requires an explicitly approved restart."}, status=409)
    if request.method == "POST" and request.path == "/prompt":
        body = await request.json()
        prompt = body.get("prompt", {})
        signature = loader_signature(prompt)
        if not any(node.get("class_type") == "VDNH3ResidentSampler" for node in prompt.values()):
            return web.json_response({"error": "This resident worker requires the VDN resident workflow."}, status=409)
        if STATE["loader_signature"] is not None and signature != STATE["loader_signature"]:
            return web.json_response({"error": "Resident loader configuration is locked; model switching requires approval."}, status=409)
        response = await handler(request)
        if response.status < 300:
            STATE["loader_signature"] = signature
            STATE["generation_started"] = True
        return response
    return await handler(request)


class VDNResidentExtension(ComfyExtension):
    async def get_node_list(self):
        return [VDNH3ResidentSampler]


async def comfy_entrypoint():
    if os.environ.get("VDN_RESIDENT") == "1":
        PromptServer.instance.app.middlewares.append(resident_guard)

        @PromptServer.instance.routes.get("/vdn-resident/status")
        async def resident_status(request):
            return web.json_response(snapshot())

        logging.info("VDN resident guard installed; no automatic model unloading")
    return VDNResidentExtension()
