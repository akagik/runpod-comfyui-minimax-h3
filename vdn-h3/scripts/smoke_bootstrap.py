"""CPU-only integration test: one pinned 415-byte public file, never weights.

Run with this kit at /test-kit and an empty tmpfs mounted at /workspace.
Does not start ComfyUI, load a model, or touch any remote Pod.
"""
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts/bootstrap_models.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

volume = Path("/workspace")
if any(volume.iterdir()):
    raise SystemExit("Smoke test requires an EMPTY disposable /workspace mount")
os.environ["HF_HOME"] = str(volume / "model-bootstrap/hf")
os.environ["HF_XET_CACHE"] = str(volume / "model-bootstrap/hf/xet")
os.environ["HF_XET_CHUNK_CACHE_SIZE_BYTES"] = "0"
files = [f for f in b.load_files(ROOT, "i2va") if f["bytes"] == 415]
assert len(files) == 1
options = dict(capacity=2_000_000_000, reserve=100_000_000, accept_license=True)
b.provision(volume, files, **options)
assert (volume / "models" / files[0]["path"]).stat().st_size == 415

def no_network(*args):
    raise AssertionError("Restart must not access network")

def no_hash(*args):
    raise AssertionError("Unchanged verified file must use receipt")

b.sha256 = no_hash
b.provision(volume, files, downloader=no_network, **options)
status = json.loads((volume / "model-bootstrap/status.json").read_text())
assert status["phase"] == "MODELS_READY" and status["downloaded_files"] == 0
print("CPU_HF_SMOKE_PASS: 415-byte download/SHA256/atomic install/offline reuse")
