"""In-memory graph freshness regressions; no verifier processes or cleanup."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('graph_freshness', ROOT / 'scripts/harness_5b7e9c31.py')
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def graph(edges):
    """Build explicit synthetic accepted observations for a dependency graph."""
    cards = {name: {'name': name, 'depends_on': deps} for name, deps in edges.items()}
    tasks = {name: {'status': 'VERIFIED', 'card_sha256': h._digest(card),
                   'test': 'test.py', 'verified_files': {}, 'verified_tools': {}}
             for name, card in cards.items()}
    return cards, {'tasks': tasks}


class GraphFreshness(unittest.TestCase):
    """Prove bounded observations and fail-closed invalidation independently of hosts."""

    def test_shared_layered_graph_observed_once(self):
        edges = {'base': []}
        prior = ['base']
        for level in range(12):
            current = [f'{level}a', f'{level}b']
            edges.update({name: prior[:] for name in current})
            prior = current
        for ordered in (edges, dict(reversed(list(edges.items())))):
            cards, data = graph(ordered)
            with patch.object(h, '_snapshot', return_value={}) as files, \
                    patch.object(h, '_tool_snapshot', return_value={}) as tools:
                self.assertEqual(h._refresh(ROOT, data, cards), [])
                self.assertEqual(files.call_count, len(cards))
                self.assertEqual(tools.call_count, 1)

    def test_later_operation_reads_changed_files_and_preserves_unrelated(self):
        cards, data = graph({'base': [], 'child': ['base'], 'other': []})
        with patch.object(h, '_snapshot', return_value={}), patch.object(h, '_tool_snapshot', return_value={}):
            self.assertEqual(h._refresh(ROOT, data, cards), [])
        def changed(root, card, test):
            return {'changed': True} if card['name'] == 'base' else {}
        with patch.object(h, '_snapshot', side_effect=changed), patch.object(h, '_tool_snapshot', return_value={}):
            self.assertEqual(set(h._refresh(ROOT, data, cards)), {'base', 'child'})
        self.assertEqual(data['tasks']['other']['status'], 'VERIFIED')

    def test_later_operation_reads_changed_rules(self):
        cards, data = graph({'base': [], 'child': ['base']})
        with patch.object(h, '_snapshot', return_value={}), patch.object(h, '_tool_snapshot', return_value={}):
            self.assertEqual(h._refresh(ROOT, data, cards), [])
        with patch.object(h, '_snapshot', return_value={}), patch.object(h, '_tool_snapshot', return_value={'rule': 'new'}):
            self.assertEqual(set(h._refresh(ROOT, data, cards)), set(cards))

    def test_cycle_missing_and_unverified_dependency_fail_closed(self):
        for edges in ({'a': ['b'], 'b': ['a'], 'other': []}, {'a': ['missing'], 'other': []}):
            cards, data = graph(edges)
            with patch.object(h, '_snapshot', return_value={}), patch.object(h, '_tool_snapshot', return_value={}):
                self.assertEqual(set(h._refresh(ROOT, data, cards)), set(cards) - {'other'})
        cards, data = graph({'base': [], 'child': ['base']})
        data['tasks']['base']['status'] = 'FAILED'
        with patch.object(h, '_snapshot', return_value={}), patch.object(h, '_tool_snapshot', return_value={}):
            self.assertEqual(h._refresh(ROOT, data, cards), ['child'])

    def test_observation_exception_restores_active_path(self):
        cards, data = graph({'base': []})
        visited = {'ancestor'}
        with patch.object(h, '_snapshot', side_effect=OSError('synthetic read failure')):
            with self.assertRaises(OSError):
                h._fresh(ROOT, data['tasks'], cards, 'base', visited)
        self.assertEqual(visited, {'ancestor'})

    def test_standalone_checks_do_not_share_cache(self):
        cards, data = graph({'base': []})
        visited = set()
        with patch.object(h, '_snapshot', return_value={}) as files, patch.object(h, '_tool_snapshot', return_value={}):
            self.assertTrue(h._fresh(ROOT, data['tasks'], cards, 'base', visited))
            self.assertTrue(h._fresh(ROOT, data['tasks'], cards, 'base', visited))
            self.assertEqual(files.call_count, 2)
        self.assertEqual(visited, set())


if __name__ == '__main__':
    unittest.main()
