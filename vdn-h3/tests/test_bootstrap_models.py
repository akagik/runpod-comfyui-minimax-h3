import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts/bootstrap_models.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def record(path="diffusion_models/test.bin", data=b"pinned weight"):
    return {"repo": "test/models", "revision": "a" * 40, "source": path,
            "path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.volume = Path(self.temporary.name)
        self.f = record()
        self.calls = []

    def download(self, f, stage):
        self.calls.append(f["path"])
        target = stage / f["source"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"pinned weight")
        return target

    def run_boot(self, files=None, **kwargs):
        options = dict(capacity=1_000_000, reserve=100, accept_license=True,
                       require_mount=False, downloader=self.download)
        options.update(kwargs)
        with contextlib.redirect_stdout(io.StringIO()):
            return b.provision(self.volume, files or [self.f], **options)

    def test_missing_only_then_fast_offline_restart(self):
        self.run_boot()
        self.assertEqual(self.calls, [self.f["path"]])
        with mock.patch.object(b, "sha256", side_effect=AssertionError("rehash")):
            self.run_boot(downloader=mock.Mock(side_effect=AssertionError("network")))
        status = json.loads((self.volume / "model-bootstrap/status.json").read_text())
        self.assertEqual(status["phase"], "MODELS_READY")
        self.assertEqual(status["downloaded_files"], 0)

    def test_existing_first_hash_then_forced_audit(self):
        target = self.volume / "models" / self.f["path"]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"pinned weight")
        with mock.patch.object(b, "sha256", wraps=b.sha256) as hashing:
            self.run_boot(accept_license=False)
            self.assertEqual(hashing.call_count, 1)
            self.run_boot(verify_full=True)
            self.assertEqual(hashing.call_count, 2)
        self.assertEqual(self.calls, [])

    def test_modified_existing_preserved(self):
        self.run_boot()
        target = self.volume / "models" / self.f["path"]
        target.write_bytes(b"changedweight")
        with self.assertRaisesRegex(b.BootstrapError, "mismatch"):
            self.run_boot()
        self.assertEqual(target.read_bytes(), b"changedweight")
        self.assertEqual(len(self.calls), 1)

    def test_license_blocks_only_missing_download(self):
        with self.assertRaisesRegex(b.BootstrapError, "license"):
            self.run_boot(accept_license=False)
        self.assertFalse((self.volume / "models").exists())
        self.assertEqual(self.calls, [])

    def test_disabled_download_does_not_claim_ready(self):
        with self.assertRaisesRegex(b.BootstrapError, "disabled"):
            self.run_boot(download=False)
        status = json.loads((self.volume / "model-bootstrap/status.json").read_text())
        self.assertEqual(status["phase"], "FAILED")

    def test_contract_quota_wins_over_huge_df(self):
        with mock.patch.object(b.shutil, "disk_usage", return_value=mock.Mock(free=10**15)):
            with self.assertRaisesRegex(b.BootstrapError, "Insufficient Volume"):
                self.run_boot(capacity=100)
        self.assertEqual(self.calls, [])

    def test_filesystem_free_wins_over_large_contract(self):
        with mock.patch.object(b.shutil, "disk_usage", return_value=mock.Mock(free=0)):
            with self.assertRaisesRegex(b.BootstrapError, "Insufficient Volume"):
                self.run_boot()

    def test_mount_check_precedes_writes(self):
        with mock.patch.object(b.os.path, "ismount", return_value=False):
            with self.assertRaisesRegex(b.BootstrapError, "not mounted"):
                self.run_boot(require_mount=True)
        self.assertEqual(list(self.volume.iterdir()), [])

    def test_capacity_required(self):
        with self.assertRaisesRegex(b.BootstrapError, "CAPACITY"):
            self.run_boot(capacity=0)
        self.assertEqual(list(self.volume.iterdir()), [])

    def test_unsafe_manifest_and_conflicts(self):
        for value in ("../outside", "/outside", ".cache/outside", "a/../../outside"):
            with self.subTest(value=value), self.assertRaises(b.BootstrapError):
                self.run_boot(files=[record(value)])
        with self.assertRaisesRegex(b.BootstrapError, "Conflicting"):
            self.run_boot(files=[self.f, record(data=b"different")])
        with self.assertRaisesRegex(b.BootstrapError, "unpinned"):
            self.run_boot(files=[{**self.f, "revision": "main"}])

    def test_symlinked_model_directory_rejected(self):
        (self.volume / "models").symlink_to(self.volume / "elsewhere")
        with self.assertRaisesRegex(b.BootstrapError, "Symlink"):
            self.run_boot()
        self.assertFalse((self.volume / "elsewhere").exists())

    def test_symlinked_state_rejected(self):
        (self.volume / "model-bootstrap").symlink_to(self.volume / "elsewhere")
        with self.assertRaisesRegex(b.BootstrapError, "Symlink"):
            self.run_boot()

    def test_shared_volume_lock(self):
        state = self.volume / "model-bootstrap"
        state.mkdir()
        with b.volume_lock(state / "bootstrap.lock", 0):
            with self.assertRaisesRegex(b.BootstrapError, "lock"):
                self.run_boot(lock_timeout=0)
        self.assertEqual(self.calls, [])
        self.run_boot(lock_timeout=0)

    def test_bad_download_not_promoted_or_deleted(self):
        def bad(f, stage):
            target = self.download(f, stage)
            target.write_bytes(b"bad")
            return target
        with self.assertRaisesRegex(b.BootstrapError, "mismatch"):
            self.run_boot(downloader=bad)
        stage = b.stage_for(self.volume / "model-bootstrap", self.f)
        self.assertEqual((stage / self.f["source"]).read_bytes(), b"bad")
        self.assertFalse((self.volume / "models" / self.f["path"]).exists())

    def test_interrupted_download_kept_then_resumed(self):
        def interrupted(f, stage):
            cache = stage / ".cache/huggingface/download/diffusion_models"
            cache.mkdir(parents=True)
            (cache / "test.bin.testhash.incomplete").write_bytes(b"pinned")
            raise RuntimeError("secret-token=DO-NOT-LOG")
        with self.assertRaisesRegex(b.BootstrapError, "Download failed"):
            self.run_boot(downloader=interrupted)
        self.assertEqual(b.staged_bytes(self.volume / "model-bootstrap", self.f), 6)
        self.assertNotIn("DO-NOT-LOG", (self.volume / "model-bootstrap/status.json").read_text())
        self.run_boot()
        self.assertEqual(len(self.calls), 1)

    def test_completed_stage_does_not_redownload(self):
        stage = b.stage_for(self.volume / "model-bootstrap", self.f)
        self.download(self.f, stage)
        self.calls.clear()
        self.run_boot(downloader=mock.Mock(side_effect=AssertionError("network")))
        self.assertFalse((stage / self.f["source"]).exists())
        self.assertTrue((self.volume / "models" / self.f["path"]).is_file())

    def test_racing_writer_not_overwritten(self):
        def race(f, stage):
            target = self.download(f, stage)
            dest = self.volume / "models" / f["path"]
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b"user data")
            return target
        with self.assertRaisesRegex(b.BootstrapError, "not overwriting"):
            self.run_boot(downloader=race)
        self.assertEqual((self.volume / "models" / self.f["path"]).read_bytes(), b"user data")

    def test_hardlinks_counted_once_symlinks_not_followed(self):
        (self.volume / "a").write_bytes(b"123")
        b.os.link(self.volume / "a", self.volume / "b")
        (self.volume / "link").symlink_to(self.volume)
        self.assertEqual(b.volume_usage(self.volume), 3)

    def test_profile_indexes(self):
        for profile, count, size in [("i2va", 12, 129064802960),
                                      ("ref2va", 12, 129064802960),
                                      ("both", 13, 195345290328)]:
            files = b.load_files(ROOT, profile)
            self.assertEqual(len(files), count)
            self.assertEqual(sum(f["bytes"] for f in files), size)
            paths = [f["path"] for f in files]
            if profile == "ref2va":
                self.assertNotIn("diffusion_models/minimax_h3_fl2va_bf16.safetensors", paths)

    def test_cli_list_does_not_require_capacity_or_mount(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/bootstrap_models.py"),
                                 "--profile", "both", "--list"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["files"]), 13)

    def test_startup_and_image_keep_resident_contract(self):
        script = (ROOT / "resident/start-auto.sh").read_text()
        self.assertLess(script.index("service ssh start"), script.index("bootstrap_models.py"))
        self.assertLess(script.index('wait "$bootstrap_pid"'), script.index("exec /opt/vdn-h3/resident/start.sh"))
        self.assertIn('if [[ "$result" == 0 ]]', script)
        self.assertIn("ComfyUI NOT started", script)
        dockerfile = (ROOT / "Dockerfile.autoboot").read_text()
        self.assertIn("vdn-h3-0.1.1@sha256:270eb4", dockerfile)
        self.assertNotIn("pip install", dockerfile)
        self.assertNotIn("resident/__init__.py", dockerfile)
        subprocess.run(["bash", "-n", str(ROOT / "resident/start-auto.sh")], check=True)


if __name__ == "__main__":
    unittest.main()
