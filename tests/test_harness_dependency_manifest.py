"""Keep bundled verifier imports inside the explicit acceptance fingerprint."""
import ast
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
SPEC = importlib.util.spec_from_file_location('dependency_harness', SCRIPTS / 'harness_5b7e9c31.py')
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


class DependencyManifest(unittest.TestCase):
    def test_bundled_static_and_literal_loader_dependencies_are_in_manifest(self):
        """Recognize local imports and current fixed loaders; not a universal analyzer."""
        manifest = set(h._TOOL_FILES)
        for relative in manifest:
            tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split('.')[0] + '.py' for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split('.')[0] + '.py')
                elif isinstance(node, ast.Call):
                    arg = None
                    if isinstance(node.func, ast.Attribute) and node.func.attr == 'with_name' and node.args:
                        arg = node.args[0]
                    elif isinstance(node.func, ast.Name) and node.func.id == '_load' and len(node.args) > 1:
                        arg = node.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.endswith('.py'):
                        names.add(arg.value)
            for name in names:
                if (SCRIPTS / name).is_file():
                    self.assertIn('scripts/' + name, manifest, (relative, name))

    def test_each_bundled_tool_change_invalidates_verified_observation(self):
        """Mutate returned hashes only, never the files or acceptance runner."""
        baseline = h._tool_snapshot()
        card = {'depends_on': []}
        for changed in h._TOOL_FILES:
            with self.subTest(tool=changed):
                item = {'status': 'VERIFIED', 'card_sha256': h._digest(card), 'test': 'test.py',
                        'verified_files': {}, 'verified_tools': baseline}
                def changed_hash(root, name):
                    return 'f' * 64 if name == changed else baseline[name]
                with patch.object(h, '_snapshot', return_value={}), \
                        patch.object(h._CORE, '_read_hash', side_effect=changed_hash):
                    self.assertEqual(h._refresh(ROOT, {'tasks': {'piece': item}}, {'piece': card}), ['piece'])
                self.assertEqual(item['status'], 'STALE')


if __name__ == '__main__':
    unittest.main()
