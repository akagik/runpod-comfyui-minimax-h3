import base64
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest


if "runpod" not in sys.modules:
    sys.modules["runpod"] = types.SimpleNamespace(
        serverless=types.SimpleNamespace(start=lambda _config: None)
    )

SPEC = importlib.util.spec_from_file_location(
    "worker_handler", Path(__file__).parents[1] / "worker" / "handler.py"
)
handler = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(handler)


class HandlerInputTests(unittest.TestCase):
    def test_safe_child_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(handler.WorkerInputError):
                handler._safe_child(root, "../escape.png")
            with self.assertRaises(handler.WorkerInputError):
                handler._safe_child(root, "/absolute.png")

    def test_base64_decode(self):
        value = base64.b64encode(b"test-image").decode()
        self.assertEqual(handler._decode_image(value), b"test-image")
        with self.assertRaises(handler.WorkerInputError):
            handler._decode_image("not base64!")

    def test_volume_input_is_symlinked_and_source_survives_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            volume = root / "volume"
            inputs = root / "inputs"
            source = volume / "references" / "reference.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"png")
            inputs.mkdir()

            old_volume, old_input = handler.VOLUME_ROOT, handler.INPUT_ROOT
            handler.VOLUME_ROOT, handler.INPUT_ROOT = volume, inputs
            try:
                staged = handler._stage_inputs(
                    {
                        "volume_inputs": [
                            {"path": "references/reference.png", "name": "reference.png"}
                        ]
                    }
                )
                self.assertEqual(len(staged), 1)
                self.assertTrue(staged[0].is_symlink())
                handler._cleanup_staged(staged)
                self.assertFalse(staged[0].exists())
                self.assertTrue(source.is_file())
            finally:
                handler.VOLUME_ROOT, handler.INPUT_ROOT = old_volume, old_input


if __name__ == "__main__":
    unittest.main()

