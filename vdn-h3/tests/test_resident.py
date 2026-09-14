import ast
import json
import math
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ResidentContractTests(unittest.TestCase):
    def test_no_inference_setting_changes(self):
        one = json.loads((ROOT / 'workflows/minimax-h3-i2va-vdn-bf16-8step-v1.json').read_text())
        two = json.loads((ROOT / 'workflows/minimax-h3-i2va-vdn-bf16-8step-v2.json').read_text())
        self.assertEqual(one['parameters'], two['parameters'])
        self.assertEqual(one['modelProfile'], two['modelProfile'])
        two['apiWorkflow']['11']['class_type'] = 'SamplerCustomAdvanced'
        self.assertEqual(one['apiWorkflow'], two['apiWorkflow'])

    def test_inputs_identical(self):
        # Requests/media are deliberately absent from the public build context.
        if not (ROOT / 'requests').exists():
            self.skipTest('private requests excluded from public image')
        source = (ROOT / 'requests/vdn-h3-idle-8step.md').read_bytes()
        for name in ('warmup', 'warm-1', 'warm-2', 'warm-3'):
            candidate = (ROOT / f'requests/resident-{name}.md').read_bytes()
            self.assertEqual(source.replace(b'workflowVersion: 1', b'workflowVersion: 2'), candidate)

    def test_shell_syntax_and_scope(self):
        script = ROOT / 'resident/start.sh'
        subprocess.run(['bash', '-n', str(script)], check=True)
        content = script.read_text()
        self.assertIn('--cache-classic --disable-pinned-memory', content)
        self.assertNotIn('--highvram', content)
        self.assertNotIn('--cache-none', content)
        self.assertNotIn('unload_all_models', content)

    def test_sampler_passthrough_and_uncacheable(self):
        tree = ast.parse((ROOT / 'resident/__init__.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'VDNH3ResidentSampler')
        fingerprint = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'fingerprint_inputs')
        self.assertEqual(ast.unparse(fingerprint.body[-1]), "return float('nan')")
        execute = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        self.assertIn('super().execute(noise, guider, sampler, sigmas, latent_image)', ast.unparse(execute))
        self.assertTrue(math.isnan(float('nan')))

    def test_loader_signature_detects_precision_and_vdn_change(self):
        tree = ast.parse((ROOT / 'resident/__init__.py').read_text())
        selected = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == 'loader_signature')
                    or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'LOADER_CLASSES' for t in n.targets))]
        namespace = {'json': json}
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'signature', 'exec'), namespace)
        signature = namespace['loader_signature']
        graph = json.loads((ROOT / 'workflows/minimax-h3-i2va-vdn-bf16-8step-v2.json').read_text())['apiWorkflow']
        baseline = signature(graph)
        graph['7']['inputs']['noise_seed'] += 1
        self.assertEqual(baseline, signature(graph))
        graph['4']['inputs']['weight_dtype'] = 'fp8_e4m3fn'
        self.assertNotEqual(baseline, signature(graph))

    def test_model_switch_is_enabled_only_for_both_or_explicit_opt_in(self):
        tree = ast.parse((ROOT / 'resident/__init__.py').read_text())
        selected = [n for n in tree.body if
                    (isinstance(n, ast.FunctionDef) and n.name == 'model_switch_allowed')
                    or (isinstance(n, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == 'SWITCH_TRUE_VALUES'
                        for t in n.targets))]
        namespace = {'os': __import__('os')}
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'switch-policy', 'exec'), namespace)
        allowed = namespace['model_switch_allowed']
        self.assertTrue(allowed({'VDN_MODEL_PROFILE': 'both'}))
        self.assertFalse(allowed({'VDN_MODEL_PROFILE': 'i2va'}))
        self.assertFalse(allowed({'VDN_MODEL_PROFILE': 'ref2va'}))
        self.assertTrue(allowed({'VDN_MODEL_PROFILE': 'i2va', 'VDN_ALLOW_MODEL_SWITCH': '1'}))
        self.assertFalse(allowed({'VDN_MODEL_PROFILE': 'both', 'VDN_ALLOW_MODEL_SWITCH': '0'}))

    def test_model_switch_unload_requires_an_empty_queue(self):
        tree = ast.parse((ROOT / 'resident/__init__.py').read_text())
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'queue_is_empty']
        namespace = {}
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'queue-policy', 'exec'), namespace)
        queue_is_empty = namespace['queue_is_empty']

        class Queue:
            def __init__(self, running, queued):
                self.value = (running, queued)

            def get_current_queue(self):
                return self.value

        self.assertTrue(queue_is_empty(Queue([], [])))
        self.assertFalse(queue_is_empty(Queue([object()], [])))
        self.assertFalse(queue_is_empty(Queue([], [object()])))


if __name__ == '__main__':
    unittest.main()
