"""CPU import/schema smoke test. Does not load weights or generate anything."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("COMFYUI_DIR", "/opt/ComfyUI-vdn"))
import comfy.options
comfy.options.enable_args_parsing()
import nodes
import torch


async def main():
    import server
    server.PromptServer(asyncio.get_running_loop())
    await nodes.init_extra_nodes()
    required = ("ApplyVDNH3", "MiniMaxH3ImageToVideo", "VAEDecodeTiled",
                "VAEDecodeAudio", "CreateVideo", "SaveVideo")
    missing = [name for name in required if name not in nodes.NODE_CLASS_MAPPINGS]
    if missing:
        raise RuntimeError(f"Missing required nodes: {missing}")
    schema = nodes.NODE_CLASS_MAPPINGS["ApplyVDNH3"].INPUT_TYPES()["required"]
    assert schema["lora_mode"][1]["default"] == "merge"
    assert schema["apply_turbo_adapter"][1]["default"] is True
    assert "cache_gpu" in schema["branch_weights"][0]
    workflow = json.loads(Path("/opt/vdn-h3/workflows/minimax-h3-i2va-vdn-bf16-8step-v1.json").read_text())
    graph = workflow["apiWorkflow"]
    for node_id, node in graph.items():
        cls = nodes.NODE_CLASS_MAPPINGS[node["class_type"]]
        input_types = cls.INPUT_TYPES()
        declared = {**input_types.get("required", {}), **input_types.get("optional", {})}
        for name in input_types.get("required", {}):
            assert name in node["inputs"], (node_id, "missing required input", name)
        for name, value in node["inputs"].items():
            assert name in declared, (node_id, "unknown input", name)
            if isinstance(value, list):
                assert value[0] in graph, (node_id, "bad link", value)
                continue
            kind = declared[name][0]
            # Model/image enums cannot be checked without the actual files.
            if isinstance(kind, list) and not (name.endswith("_name") or name in ("image", "vdn_checkpoint")):
                assert value in kind, (node_id, name, value, kind)
            elif kind == "INT":
                assert isinstance(value, int) and not isinstance(value, bool), (node_id, name)
    assert graph["9"]["inputs"]["steps"] == 8
    assert graph["9"]["inputs"]["model"] == ["16", 0]
    assert graph["10"]["inputs"]["model"] == ["16", 0]
    print(json.dumps({"cpu_import_smoke": "passed", "torch": torch.__version__,
                      "cuda_build": torch.version.cuda, "nodes": required,
                      "workflow_schema": "passed (files/GPU not checked)"}))


if __name__ == "__main__":
    asyncio.run(main())
