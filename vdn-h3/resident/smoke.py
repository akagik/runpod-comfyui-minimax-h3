"""Separate CPU-only import check. Does not submit prompts or load weights."""
import asyncio
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, '/opt/ComfyUI-vdn')
sys.argv = [sys.argv[0], '--cpu']
import server
import nodes

async def check():
    server.PromptServer(asyncio.get_running_loop())
    await nodes.init_extra_nodes(init_custom_nodes=False)
    path = Path(__file__).with_name('__init__.py')
    spec = importlib.util.spec_from_file_location('vdn_resident_smoke', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extension = await module.comfy_entrypoint()
    classes = await extension.get_node_list()
    assert classes[0].define_schema().node_id == 'VDNH3ResidentSampler'
    assert classes[0].fingerprint_inputs() != classes[0].fingerprint_inputs()
    assert module.snapshot()['models'] == []
    print('VDN_RESIDENT_CPU_SMOKE_PASSED')

asyncio.run(check())
