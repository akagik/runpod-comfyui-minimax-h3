"""Pinned, missing-only startup provisioning. No GPU operations or data deletion.

The original download_models.py remains a historical/manual diagnostic tool.
This startup path additionally checks the mount, free space, receipts,
shared-volume lock and staging before allowing ComfyUI to accept requests.
"""
import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
import uuid


class BootstrapError(RuntimeError):
    pass


def safe_path(root, relative):
    parts = PurePosixPath(relative)
    if (not relative or not parts.parts or parts.is_absolute() or ".." in parts.parts
            or "\\" in relative or parts.parts[0].startswith(".")):
        raise BootstrapError("Unsafe manifest path")
    result = root
    for part in parts.parts:
        result = result / part
        if result.is_symlink():
            raise BootstrapError("Symlink in managed path; not modifying it")
    if not result.resolve().is_relative_to(root.resolve()):
        raise BootstrapError("Unsafe manifest path")
    return result


def capacity_bytes(value):
    """Optional contracted Volume size in GB; 0 means the size is unknown."""
    value = (value or "").strip()
    if not value:
        return 0
    if not value.isdigit():
        raise BootstrapError("MODEL_VOLUME_CAPACITY_GB must be a whole number of GB")
    return int(value) * 1_000_000_000


def load_files(directory, profile):
    if profile not in {"i2va", "ref2va", "both"}:
        raise BootstrapError("Unknown model profile")
    files = json.loads((directory / "models.lock.json").read_text())["files"]
    if profile == "ref2va":
        files = [f for f in files if f["path"] !=
                 "diffusion_models/minimax_h3_fl2va_bf16.safetensors"]
    if profile in {"ref2va", "both"}:
        files += json.loads((directory / "models.ref2va.lock.json").read_text())["files"]
    return validate_files(files)


def validate_files(files):
    unique = {}
    for f in files:
        for key in ("path", "source"):
            safe_path(Path("/manifest-validation"), f[key])
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", f["repo"])
                or not re.fullmatch(r"[0-9a-f]{40}", f["revision"])
                or not re.fullmatch(r"[0-9a-f]{64}", f["sha256"])
                or type(f["bytes"]) is not int or f["bytes"] <= 0):
            raise BootstrapError("Invalid or unpinned manifest record")
        if f["path"] in unique and unique[f["path"]] != f:
            raise BootstrapError("Conflicting manifest destinations")
        unique[f["path"]] = f
    return list(unique.values())


def fingerprint(path):
    s = path.stat()
    if not stat.S_ISREG(s.st_mode):
        raise BootstrapError("Managed model is not a regular file")
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino, s.st_dev]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, data):
    if path.is_symlink():
        raise BootstrapError("Symlink in bootstrap metadata")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextlib.contextmanager
def volume_lock(path, timeout):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BootstrapError("Another bootstrap holds the Volume lock") from None
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
    # Never unlink a shared lock inode.


def volume_usage(root):
    """Logical bytes, including staging, without following links/double counting.

    This is a conservative local estimate, not a remote filesystem quota API.
    Snapshots or concurrent unrelated writers may still consume extra capacity.
    """
    seen, total = set(), 0
    def fail(error):
        raise error
    for directory, _, names in os.walk(root, followlinks=False, onerror=fail):
        for name in names:
            try:
                s = (Path(directory) / name).lstat()
            except FileNotFoundError:
                continue
            key = (s.st_dev, s.st_ino)
            if stat.S_ISREG(s.st_mode) and key not in seen:
                seen.add(key)
                total += s.st_size
    return total


def stage_for(state, f):
    return safe_path(state, "staging/" + f["sha256"])


def staged_bytes(state, f):
    stage = stage_for(state, f)
    complete = safe_path(stage, f["source"])
    if complete.is_file():
        return min(complete.stat().st_size, f["bytes"])
    # Only the expected filename's HF partial in this SHA-specific stage gets
    # credit. Extra orphaned cache files count as used space, not resume credit.
    source = PurePosixPath(f["source"])
    cache = stage / ".cache/huggingface/download" / source.parent
    for ancestor in [cache, *cache.parents]:
        if ancestor == state:
            break
        if ancestor.is_symlink():
            raise BootstrapError("Symlink in download cache")
    sizes = [p.stat().st_size for p in cache.glob(source.name + ".*.incomplete")
             if p.is_file() and not p.is_symlink()]
    return min(max(sizes, default=0), f["bytes"])


def check_capacity(volume, state, files, capacity, reserve, usage=None):
    """Refuse a download that provably cannot fit.

    On RunPod's shared MFS `df` reports the whole filesystem, so it over-states
    what one Network Volume accepts: it can rule a download out, never in. A
    caller that knows the contracted size (the Manager always does; the Web
    Console does not) passes `capacity` for the stricter check. Without it the
    download still runs and a real ENOSPC is handled as a clean failure, which
    is what lets one template serve Volumes of any size.

    `usage` carries a single Volume-wide scan across the whole run; re-walking a
    shared Volume of hundreds of thousands of entries per file is prohibitive.
    """
    extra = sum(max(0, f["bytes"] - staged_bytes(state, f)) for f in files)
    free = shutil.disk_usage(volume).free
    detail = f"filesystem_free={free}"
    if capacity > 0:
        used = volume_usage(volume) if usage is None else usage
        free = min(free, capacity - used)
        detail += f", used={used}, configured_capacity={capacity}"
    if free < extra + reserve:
        raise BootstrapError(
            f"Insufficient Volume capacity: additional={extra}, reserve={reserve}, "
            f"{detail}. No files removed and no storage resized.")


def out_of_space(error):
    """True when `error`, or any error it wraps, is a filesystem ENOSPC."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            return True
        error = error.__cause__ or error.__context__
    return False


def discard_stage(stage):
    """Drop only our own staging tree and report the bytes reclaimed.

    Used after ENOSPC: keeping a partial file that cannot fit just holds the
    space. Installed models and unrelated Volume data are never touched.
    """
    if stage.is_symlink() or not stage.is_dir():
        return 0
    freed = volume_usage(stage)
    shutil.rmtree(stage)
    return freed


def hf_download(f, stage):
    from huggingface_hub import hf_hub_download
    # Keep all large caches on the mounted Volume. Disable the optional Xet
    # chunk cache; local_dir already resumes without a second full weight copy.
    return Path(hf_hub_download(repo_id=f["repo"], filename=f["source"],
                               revision=f["revision"], local_dir=str(stage)))


def provision(volume, files, *, capacity=0, reserve=10_000_000_000,
              download=True, accept_license=False, verify_full=False,
              require_mount=True, lock_timeout=3600, downloader=hf_download):
    files = validate_files(files)
    volume = Path(os.path.abspath(volume))
    if volume.is_symlink() or not volume.is_dir():
        raise BootstrapError("Persistent Volume path missing or symlinked")
    if require_mount and not os.path.ismount(volume):
        raise BootstrapError("Persistent Volume is not mounted; refusing container-disk download")
    if capacity < 0 or reserve < 0:
        raise BootstrapError("Invalid capacity or reserve")
    root = safe_path(volume, "models")
    state = safe_path(volume, "model-bootstrap")
    state.mkdir(exist_ok=True)
    safe_path(state, "hf/xet")
    with volume_lock(state / "bootstrap.lock", lock_timeout):
        status = state / "status.json"
        def report(phase, **info):
            data = {"phase": phase, "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "pid": os.getpid(), **info}
            atomic_json(status, data)
            print(json.dumps(data), flush=True)
        try:
            report("CHECKING", required_files=len(files),
                   capacity_source="configured" if capacity > 0 else "filesystem")
            receipt_path = state / "verified.json"
            if receipt_path.is_symlink():
                raise BootstrapError("Symlink in receipt path")
            try:
                receipts = json.loads(receipt_path.read_text())
                if not isinstance(receipts, dict):
                    receipts = {}
            except (FileNotFoundError, ValueError):
                receipts = {}

            def verify(path, f):
                before = fingerprint(path)
                if before[0] != f["bytes"]:
                    raise BootstrapError(f"Size mismatch; existing data retained: {f['path']}")
                expected = {"sha256": f["sha256"], "stat": before}
                if not verify_full and receipts.get(f["path"]) == expected:
                    return
                report("VERIFYING", file=f["path"])
                if sha256(path) != f["sha256"] or fingerprint(path) != before:
                    raise BootstrapError(f"SHA256 mismatch/file changed; data retained: {f['path']}")
                receipts[f["path"]] = expected

            missing = []
            for f in files:
                dest = safe_path(root, f["path"])
                if dest.exists():
                    verify(dest, f)
                else:
                    missing.append(f)
            atomic_json(receipt_path, receipts)
            if missing and not download:
                raise BootstrapError("Models missing; automatic download is disabled")
            if missing and not accept_license:
                raise BootstrapError("Model license acceptance required: VDN_ACCEPT_MODEL_LICENSE=1")
            usage = volume_usage(volume) if missing and capacity > 0 else None
            if missing:
                check_capacity(volume, state, missing, capacity, reserve, usage)
            for index, f in enumerate(missing):
                check_capacity(volume, state, missing[index:], capacity, reserve, usage)
                staged = staged_bytes(state, f)
                stage = stage_for(state, f)
                stage.mkdir(parents=True, exist_ok=True)
                expected = safe_path(stage, f["source"])
                if not expected.exists():
                    report("DOWNLOADING", file=f["path"], bytes=f["bytes"])
                    try:
                        got = Path(downloader(f, stage))
                    except Exception as error:
                        # SDK errors can contain signed URLs or auth material.
                        if out_of_space(error):
                            freed = discard_stage(stage)
                            raise BootstrapError(
                                f"Out of space while downloading {f['path']}; discarded "
                                f"{freed} staged bytes. Existing models retained.") from None
                        raise BootstrapError("Download failed (" + type(error).__name__ +
                                             "); staged data retained for retry") from None
                    if got != expected:
                        raise BootstrapError("Unexpected download path; not installing")
                verify(expected, f)
                dest = safe_path(root, f["path"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Atomic, same-filesystem, NO overwrite even if another writer
                # creates a target after validation. Unsupported hardlinks fail.
                try:
                    os.link(expected, dest, follow_symlinks=False)
                except FileExistsError:
                    raise BootstrapError("Destination appeared during download; not overwriting") from None
                expected.unlink()  # only our verified staging hardlink; model remains
                receipts[f["path"]] = {"sha256": f["sha256"], "stat": fingerprint(dest)}
                atomic_json(receipt_path, receipts)
                if usage is not None:
                    usage += f["bytes"] - staged
                report("INSTALLED", file=f["path"])
            report("MODELS_READY", required_files=len(files), downloaded_files=len(missing),
                   total_model_bytes=sum(f["bytes"] for f in files))
        except Exception as error:
            message = str(error) if isinstance(error, BootstrapError) else type(error).__name__
            report("FAILED", error=message)
            raise BootstrapError(message) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volume-root", type=Path,
                        default=Path(os.environ.get("RUNPOD_VOLUME_ROOT", "/workspace")))
    parser.add_argument("--profile", choices=["i2va", "ref2va", "both"],
                        default=os.environ.get("VDN_MODEL_PROFILE", "i2va"))
    parser.add_argument("--manifest-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--verify-full", action="store_true")
    parser.add_argument("--list", action="store_true", help="Print selected index without writes/network")
    args = parser.parse_args()
    try:
        files = load_files(args.manifest_dir, args.profile)
        if args.list:
            print(json.dumps({"profile": args.profile, "totalBytes": sum(f["bytes"] for f in files),
                              "files": files}, indent=2))
            return 0
        # Must be set before importing huggingface_hub/hf_xet.
        os.environ["HF_HOME"] = str(args.volume_root / "model-bootstrap/hf")
        os.environ["HF_XET_CACHE"] = str(args.volume_root / "model-bootstrap/hf/xet")
        os.environ["HF_XET_CHUNK_CACHE_SIZE_BYTES"] = "0"
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        provision(args.volume_root, files,
                  capacity=capacity_bytes(os.environ.get("MODEL_VOLUME_CAPACITY_GB")),
                  download=os.environ.get("MODEL_AUTO_DOWNLOAD", "1") == "1",
                  accept_license=os.environ.get("VDN_ACCEPT_MODEL_LICENSE", "0") == "1",
                  verify_full=args.verify_full)
        return 0
    except (BootstrapError, ValueError) as error:
        print("Model bootstrap failed: " + (str(error) if isinstance(error, BootstrapError)
                                           else "Invalid configuration"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
