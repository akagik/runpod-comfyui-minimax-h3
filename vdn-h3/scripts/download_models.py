"""Download only pinned BF16 VDN models; retain existing files and verify SHA256.

No Pod provisioning, model execution, automatic quantization or deletions.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/workspace/models"))
    parser.add_argument("--manifest", type=Path,
                        default=Path(__file__).resolve().parents[1] / "models.lock.json")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--accept-model-license", action="store_true")
    args = parser.parse_args()
    files = json.loads(args.manifest.read_text())["files"]
    root = args.root.resolve()
    need = 0
    for f in files:
        dest = (root / f["path"]).resolve()
        if not dest.is_relative_to(root):
            raise SystemExit("Unsafe manifest path")
        if dest.exists():
            if dest.stat().st_size != f["bytes"] or sha256(dest) != f["sha256"]:
                raise SystemExit(f"Existing file differs; not overwriting: {dest}")
            print(f"VERIFIED {f['path']}", flush=True)
        else:
            need += f["bytes"]
    if not args.download:
        print(json.dumps({"missing_bytes": need, "download_started": False}))
        return
    if not args.accept_model_license:
        raise SystemExit("Review the MiniMax H3 license/territory; --accept-model-license required")
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < need + 10_000_000_000:
        raise SystemExit("Insufficient free space (required missing files plus 10 GB margin)")
    from huggingface_hub import hf_hub_download
    for f in files:
        dest = root / f["path"]
        if dest.exists():
            continue
        # local_dir avoids a second full model cache. Download resumes via HF.
        local_root = root / "vdn" if f["repo"].startswith("OpenVDN/") else root
        got = Path(hf_hub_download(repo_id=f["repo"], filename=f["source"],
                                  revision=f["revision"], local_dir=str(local_root)))
        if got.resolve() != dest.resolve():
            raise SystemExit("Unexpected download destination")
        if got.stat().st_size != f["bytes"] or sha256(got) != f["sha256"]:
            raise SystemExit(f"Downloaded file verification failed; retained for inspection: {dest}")
        print(f"VERIFIED {f['path']}", flush=True)
    print("All 12 pinned model files verified")


if __name__ == "__main__":
    main()
