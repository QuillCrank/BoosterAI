"""Verify the metadata gate on deep dependency graphs without disk fixtures."""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from booster import validate


def reversed_chain(length=1500):
    """Return valid metadata that lists each node before its dependency."""
    nodes = []
    for index in range(length - 1, -1, -1):
        nodes.append({
            'id': f'node-{index}',
            'owner': 'planner',
            'acceptance': 'observe the selected result',
            'target': 'ordinary target',
            'revision': 'graph-test',
            'status': 'planned',
            'depends_on': [f'node-{index - 1}'] if index else [],
        })
    return {'revision': 'graph-test', 'goal': 'validate a deep plan',
            'nodes': nodes, 'next': 'node-0'}


class PlanGraphDepthTests(unittest.TestCase):
    def test_valid_reverse_order_chain_has_one_ready_root(self):
        self.assertEqual(validate(reversed_chain()), {
            'valid': True, 'ready': ['node-0'], 'evidence_authenticated': False,
        })

    def test_deep_cycle_has_the_existing_validation_error(self):
        plan = reversed_chain()
        plan['nodes'][-1]['depends_on'] = ['node-1499']
        with self.assertRaisesRegex(ValueError, '^dependency cycle$'):
            validate(plan)

    def test_first_dependency_error_follows_depth_first_order(self):
        cases = (
            (['node-2'], ['unknown-later-node'], [], 'dependency cycle'),
            (['node-0'], ['unknown-earlier-node'], ['unknown-deeper-node'],
             'unknown dependency unknown-deeper-node'),
        )
        for last, middle, first, message in cases:
            with self.subTest(error=message):
                plan = reversed_chain(3)
                for node, dependencies in zip(plan['nodes'], (last, middle, first)):
                    node['depends_on'] = dependencies
                with self.assertRaisesRegex(ValueError, f'^{message}$'):
                    validate(plan)


if __name__ == '__main__':
    unittest.main()
