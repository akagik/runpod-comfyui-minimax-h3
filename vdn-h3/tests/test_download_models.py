import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/download_models.py"


class PreflightTests(unittest.TestCase):
    def run_script(self, manifest, root, *args):
        return subprocess.run([sys.executable, str(SCRIPT), "--manifest", str(manifest),
                               "--root", str(root), *args], capture_output=True, text=True)

    def test_empty_preflight_does_not_create_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = base / "lock.json"
            manifest.write_text(json.dumps({"files": []}))
            root = base / "models"
            result = self.run_script(manifest, root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(root.exists())

    def test_license_gate_precedes_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = base / "lock.json"
            manifest.write_text(json.dumps({"files": []}))
            root = base / "models"
            result = self.run_script(manifest, root, "--download")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("license", result.stderr)
            self.assertFalse(root.exists())

    def test_existing_data_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            payload = base / "keep.bin"
            payload.write_bytes(b"user data")
            manifest = base / "lock.json"
            manifest.write_text(json.dumps({"files": [{"path": "keep.bin", "bytes": 1,
                                                       "sha256": "0" * 64}]}))
            result = self.run_script(manifest, base)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(payload.read_bytes(), b"user data")

    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = base / "lock.json"
            manifest.write_text(json.dumps({"files": [{"path": "../outside", "bytes": 1,
                                                       "sha256": "0" * 64}]}))
            result = self.run_script(manifest, base / "models")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unsafe", result.stderr)


if __name__ == "__main__":
    unittest.main()
