from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from repo_temp import workspace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('booster', ROOT / 'scripts/booster.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

def plan():
    return {'revision':'r1','goal':'visible value','next':'a','nodes':[{'id':'a','owner':'one','depends_on':[], 'status':'planned','acceptance':'value is visible','target':'real-terminal','revision':'r1'}]}

class GuidanceTests(unittest.TestCase):
    def test_administrator_assessment_is_bounded_and_not_automatic(self):
        # Text-contract regression, not execution with administrator privileges.
        text=(ROOT/'SKILL.md').read_text(encoding='utf-8')
        section=text.split('### Requirement 24: assess administrator privileges',1)[1].split('At the start of a local coding task',1)[0]
        for phrase in ('specific code', 'expected benefit and risks', 'least privilege necessary',
                       'does not authorize elevation', 'required approval',
                       'Never use administrator privileges to bypass', 'denied action',
                       'continue independent authorized work'):
            self.assertIn(phrase,section)

    def test_blocking_ambiguity_guidance_is_record_first_and_bounded(self):
        # Text-contract regression only, not proof of a future agent's behavior.
        for relative in ('SKILL.md','references/operating-algorithm.md'):
            with self.subTest(file=relative):
                text=(ROOT/relative).read_text(encoding='utf-8')
                section=text.split('## Requirement 40:',1)[1].split('\n## ',1)[0]
                self.assertLess(section.index('first inspect'),section.index('ask short, concrete questions'))
                self.assertIn('Do not invent the intended product behavior',section)
                self.assertIn('Do not block development for routine technical decisions',section)
                self.assertIn('continue independent work that is already clear',section)


class PlanTests(unittest.TestCase):
    def test_nonobject(self):
        with self.assertRaises(ValueError): b.validate([])
    def test_nested_malformed_evidence(self):
        for malformed in (None, [], 'invalid', 42):
            p=plan();p['next']=None;p['nodes'][0].update(status='verified',evidence=malformed)
            with self.subTest(evidence=malformed), self.assertRaisesRegex(ValueError,'evidence must be object'):
                b.validate(p)
    def test_malformed_nodes(self):
        for malformed in (None, [], 'invalid', 42):
            p=plan();p['nodes']=[malformed]
            with self.subTest(node=malformed), self.assertRaisesRegex(ValueError,'node must be object'):
                b.validate(p)
    def test_ready(self): self.assertTrue(b.validate(plan())['valid'])
    def test_verified(self):
        p=plan(); p['next']=None; p['nodes'][0].update(status='verified', evidence={'kind':'real','result':'pass','revision':'r1','target':'real-terminal','reference':'run.log','observed_at':'2020-01-01T00:00:00Z'})
        self.assertTrue(b.validate(p)['valid'])
        for field,value in [('kind','mock'),('revision','old'),('target','different')]:
            bad=copy.deepcopy(p); bad['nodes'][0]['evidence'][field]=value
            with self.assertRaises(ValueError): b.validate(bad)
    def test_cycle_and_unknown(self):
        for dependency in ['a','missing']:
            p=plan();p['nodes'][0]['depends_on']=[dependency]
            with self.assertRaises(ValueError):b.validate(p)
    def test_dependency_gate(self):
        p=plan(); second=copy.deepcopy(p['nodes'][0]);second.update(id='b',depends_on=['a'],status='active');p['nodes'].append(second);p['next']='b'
        with self.assertRaises(ValueError):b.validate(p)
    def test_blocked(self):
        p=plan();p['nodes'][0]['status']='blocked';p['next']=None
        self.assertTrue(b.validate(p)['valid'])
    def test_status(self):
        p=plan();p['nodes'][0]['status']='report_ready'
        with self.assertRaises(ValueError):b.validate(p)

    def test_validate_cli_rejects_nonstandard_json_number_literals(self):
        plan_path = ROOT / 'virtual-plan.json'
        original_read_text = Path.read_text
        plan_prefix = json.dumps(plan())[:-1] + ', "metadata": '

        def validate_metadata(metadata):
            reads = []

            def read_text(path, encoding=None, errors=None):
                if path != plan_path:
                    return original_read_text(path, encoding=encoding, errors=errors)
                reads.append(path)
                self.assertEqual(encoding, 'utf-8-sig')
                self.assertIn(errors, (None, 'strict'))
                return plan_prefix + metadata + '}'

            with (patch.object(sys, 'argv', ['booster.py', 'validate', str(plan_path)]),
                  patch.object(Path, 'read_text', read_text),
                  redirect_stdout(io.StringIO()) as stdout):
                status = b.main()
            self.assertEqual(reads, [plan_path])
            return status, json.loads(stdout.getvalue())

        for literal in ('NaN', 'Infinity', '-Infinity'):
            for metadata in (literal, '{"values": [' + literal + ']}'):
                with self.subTest(metadata=metadata):
                    self.assertEqual(validate_metadata(metadata), (1, {'error': 'non-finite JSON number'}))

        finite = '{"values": [17, -2.25, 1e-12, "NaN", "Infinity", "-Infinity"]}'
        self.assertEqual(validate_metadata(finite), (0, {'valid': True, 'ready': ['a'], 'evidence_authenticated': False}))
        duplicate = r'{"value": 1, "v\u0061lue": 2}'
        self.assertEqual(validate_metadata(duplicate), (1, {'error': 'duplicate JSON object key'}))

    def test_validate_cli_reports_json_nesting_error(self):
        plan_path = ROOT / 'virtual-plan.json'
        plan_json = json.dumps(plan())
        with (patch.object(sys, 'argv', ['booster.py', 'validate', str(plan_path)]),
              patch.object(b, 'Path') as path,
              patch.object(b.json, 'loads', side_effect=RecursionError('decoder detail')) as decode,
              redirect_stdout(io.StringIO()) as stdout,
              redirect_stderr(io.StringIO()) as stderr):
            path.return_value.read_text.return_value = plan_json
            status = b.main()

        decode.assert_called_once()
        self.assertEqual(decode.call_args.args[0], plan_json)
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(stdout.getvalue()), {'error': 'plan JSON nesting is too deep'})
        self.assertEqual(stderr.getvalue(), '')

class SelfCheckDuplicateRoutingTests(unittest.TestCase):
    """Exercise the actual source checker without a workspace fixture."""

    def _check_with_text(self, document, text):
        original_read_text = Path.read_text
        overlaid = []

        def read_text(path, *args, **kwargs):
            if path == document:
                overlaid.append(path)
                return text
            return original_read_text(path, *args, **kwargs)

        argv = ['booster.py', 'self-check', '--root', str(ROOT)]
        with (patch.object(sys, 'argv', argv),
              patch.object(Path, 'read_text', read_text),
              redirect_stdout(io.StringIO()) as stdout):
            status = b.main()

        self.assertEqual(overlaid, [document])
        return status, json.loads(stdout.getvalue())

    def _check_bundled_links(self, *targets):
        links = '\n'.join(f'[Workflow]({target})' for target in targets)
        return self._check_bundled_body(links)

    def _check_bundled_body(self, body):
        document = ROOT / 'SKILL.md'
        _, frontmatter, _ = document.read_text(encoding='utf-8').split('---\n', 2)
        return self._check_with_text(document, f'---\n{frontmatter}---\n\n{body}\n')

    def _check_registration_body(self, body):
        document = ROOT / '.agents/skills/booster-ai/SKILL.md'
        _, frontmatter, _ = document.read_text(encoding='utf-8').split('---\n', 2)
        return self._check_with_text(document, f'---\n{frontmatter}---\n\n{body}\n')

    def test_distinct_bundled_reference_targets_succeed(self):
        targets = ['references/operating-algorithm.md', 'references/project-workspace.md']
        status, report = self._check_bundled_links(*targets)
        self.assertEqual(status, 0)
        self.assertTrue(report['valid'])
        self.assertEqual(report['references_checked'], targets)

    def test_duplicate_bundled_reference_targets_fail(self):
        first = 'references/operating-algorithm.md'
        for repeated in (first, 'references/./operating-algorithm.md'):
            with self.subTest(repeated=repeated):
                status, report = self._check_bundled_links(first, repeated)
                self.assertEqual(status, 1)
                self.assertEqual(report, {'error': 'duplicate bundled resource: ' + repeated})

    def test_harmless_registration_link_succeeds(self):
        document = ROOT / '.agents/skills/booster-ai/SKILL.md'
        text = document.read_text(encoding='utf-8')
        status, report = self._check_with_text(document, text + '\n[README](../../../README.md)\n')
        self.assertEqual(status, 0)
        self.assertTrue(report['valid'])
        self.assertEqual(report['codex_repo_registration'], 'present_valid')

    def test_duplicate_canonical_registration_targets_fail(self):
        document = ROOT / '.agents/skills/booster-ai/SKILL.md'
        text = document.read_text(encoding='utf-8')
        for repeated in ('../../../SKILL.md', '../../.././SKILL.md'):
            with self.subTest(repeated=repeated):
                status, report = self._check_with_text(document, text + f'\n[Repeat]({repeated})\n')
                self.assertEqual(status, 1)
                self.assertEqual(report, {'error': 'Codex registration repeats its canonical skill link'})

    def test_inline_routes_require_complete_visible_labels(self):
        routes = (
            (
                self._check_bundled_body,
                'references/operating-algorithm.md',
                'no bundled references',
            ),
            (
                self._check_registration_body,
                '../../../SKILL.md',
                'Codex registration is missing its canonical skill link',
            ),
        )
        valid_labels = ('Guide', 'Read [the guide]', r'Read \[the guide\]', 'Read `]` here', '')
        for check, target, missing_error in routes:
            visible_link = f'[Visible]({target})'
            expected = check(visible_link)
            self.assertEqual(expected[0], 0)
            suffix = f']({target})'
            incomplete = (
                suffix,
                f'<!-- [Hidden -->{suffix}',
                f'`[Hidden`{suffix}',
                rf'\[Escaped{suffix}',
                rf'[Escaped close\{suffix}',
            )
            for malformed in incomplete:
                with self.subTest(target=target, malformed=malformed, visible_link=False):
                    self.assertEqual(check(malformed), (1, {'error': missing_error}))
                with self.subTest(target=target, malformed=malformed, visible_link=True):
                    self.assertEqual(check(f'{malformed} {visible_link}'), expected)
            for label in valid_labels:
                with self.subTest(target=target, label=label):
                    self.assertEqual(check(f'[{label}]({target})'), expected)

    def test_malformed_destinations_do_not_hide_or_add_routes(self):
        routes = (
            (self._check_bundled_body, 'references/operating-algorithm.md'),
            (self._check_registration_body, '../../../SKILL.md'),
        )
        for check, target in routes:
            link = f'[Route]({target})'
            expected = check(link)
            self.assertEqual(expected[0], 0)
            cases = (
                ('inner link after an incomplete destination', f'[Broken](x({link}'),
                ('unbalanced parenthesis', f'{link} [Broken]({target}(x)'),
                ('unquoted text', f'{link} [Broken]({target} unquoted)'),
            )
            for case, body in cases:
                with self.subTest(target=target, case=case):
                    self.assertEqual(check(body), expected)

    def test_commented_bundled_reference_cannot_supply_required_route(self):
        target = 'references/operating-algorithm.md'
        link = f'[Workflow]({target})'
        bodies = (
            f'<!-- {link} -->',
            f'<!--\n{link}\n-->',
            f'[Workflow]<!-- boundary -->({target})',
            '[Workflow](references/operating-<!-- boundary -->algorithm.md)',
        )
        for body in bodies:
            with self.subTest(body=body):
                status, report = self._check_bundled_body(body)
                self.assertEqual(status, 1)
                self.assertEqual(report, {'error': 'no bundled references'})

    def test_commented_bundled_links_do_not_change_valid_report(self):
        target = 'references/operating-algorithm.md'
        expected = self._check_bundled_links(target)
        self.assertEqual(expected[0], 0)
        self.assertEqual(expected[1]['references_checked'], [target])
        body = (
            f'[Workflow]({target})\n'
            '<!--\n'
            f'[Duplicate]({target})\n'
            '[Missing](references/nonexistent-commented-workflow.md)\n'
            '-->'
        )
        self.assertEqual(self._check_bundled_body(body), expected)

    def test_commented_registration_link_cannot_supply_canonical_route(self):
        target = '../../../SKILL.md'
        link = f'[Canonical]({target})'
        bodies = (
            f'<!-- {link} -->',
            f'<!--\n{link}\n-->',
            f'<!--\n{link}',
            f'[Canonical]<!-- boundary -->({target})',
        )
        for body in bodies:
            with self.subTest(body=body):
                status, report = self._check_registration_body(body)
                self.assertEqual(status, 1)
                self.assertEqual(
                    report, {'error': 'Codex registration is missing its canonical skill link'},
                )

    def test_commented_registration_duplicate_does_not_change_valid_report(self):
        document = ROOT / '.agents/skills/booster-ai/SKILL.md'
        text = document.read_text(encoding='utf-8')
        expected = self._check_with_text(document, text)
        self.assertEqual(expected[0], 0)
        self.assertEqual(expected[1]['codex_repo_registration'], 'present_valid')
        text += '\n<!-- [Duplicate](../../.././SKILL.md) -->\n'
        self.assertEqual(self._check_with_text(document, text), expected)

    def test_literal_examples_preserve_visible_routes(self):
        examples = (
            ('escaped opener', r'\<!-- '),
            ('inline code', '`<!--` '),
            ('backtick fence', '```markdown\n<!--\n```\n\n'),
            ('tilde fence', '~~~markdown\n<!--\n~~~\n\n'),
            ('tilde fence with backtick info', '~~~markdown `example`\n<!--\n~~~\n\n'),
        )
        routes = (
            ('bundled', self._check_bundled_body, '[Workflow](references/operating-algorithm.md)'),
            ('registration', self._check_registration_body, '[Canonical](../../../SKILL.md)'),
        )
        for route, check, link in routes:
            expected = check(link)
            self.assertEqual(expected[0], 0)
            for example, prefix in examples:
                with self.subTest(route=route, example=example):
                    self.assertEqual(check(prefix + link), expected)
            with self.subTest(route=route, example='escaped first backtick'):
                body = rf'\``{link}` {link}'
                self.assertEqual(check(body), expected)

    def test_line_start_triple_backtick_spans_do_not_mask_visible_routes(self):
        cases = (
            (
                'bundled',
                self._check_bundled_body,
                '```<!-- [Draft](references/operating-algorithm.md)```\n',
                '[Guide](references/records-and-continuity.md)',
            ),
            (
                'registration',
                self._check_registration_body,
                '```[Draft](../../../SKILL.md)``` ',
                '[Canonical](../../../SKILL.md)',
            ),
        )
        for route, check, prefix, link in cases:
            with self.subTest(route=route):
                expected = check(link)
                self.assertEqual(expected[0], 0)
                self.assertEqual(check(prefix + link), expected)

    def test_code_examples_cannot_supply_canonical_registration_route(self):
        link = '[Canonical](../../../SKILL.md)'
        examples = (
            ('inline code', f'`{link}`'),
            ('backtick fence', f'```markdown\n{link}\n```'),
            ('tilde fence', f'~~~markdown\n{link}\n~~~'),
        )
        for example, body in examples:
            with self.subTest(example=example):
                status, report = self._check_registration_body(body)
                self.assertEqual(status, 1)
                self.assertEqual(
                    report, {'error': 'Codex registration is missing its canonical skill link'},
                )

    def test_multiline_code_spans_cannot_supply_or_duplicate_routes(self):
        routes = (
            (self._check_bundled_body, 'references/operating-algorithm.md', 'no bundled references'),
            (
                self._check_registration_body,
                '../../../SKILL.md',
                'Codex registration is missing its canonical skill link',
            ),
        )
        for check, target, missing_error in routes:
            visible = f'[Guide]({target})'
            hidden = f'[Example]({target})'
            expected = check(visible)
            self.assertEqual(expected[0], 0)
            spans = (
                ('LF and one backtick', f'Example `literal\n{hidden}\ntext`.'),
                ('CRLF and two backticks', f'Example ``literal `\r\n{hidden}\r\ntext ``` here``.'),
                ('escaped first backtick', f'Example \\``literal\n{hidden}\ntext`.'),
                ('heading-like text', f'Example `literal\n#tag {hidden}\ntext`.'),
                ('ordered continuation', f'Example `literal\n2. {hidden}\ntext`.'),
            )
            for case, span in spans:
                with self.subTest(target=target, case=case, visible_link=False):
                    self.assertEqual(check(span), (1, {'error': missing_error}))
                with self.subTest(target=target, case=case, visible_link=True):
                    self.assertEqual(check(f'{span}\n{visible}'), expected)

    def test_multiline_code_spans_require_matching_runs_in_one_paragraph(self):
        routes = (
            (self._check_bundled_body, 'references/operating-algorithm.md'),
            (self._check_registration_body, '../../../SKILL.md'),
        )
        for check, target in routes:
            link = f'[Guide]({target})'
            expected = check(link)
            self.assertEqual(expected[0], 0)
            cases = (
                ('larger closer', f'Example `literal\n{link}\ntext``.'),
                ('smaller closer', f'Example ``literal\r\n{link}\r\ntext`.'),
                ('escaped opener', f'Example \\`literal\n{link}\ntext`.'),
                ('blank line', f'Example `literal\r\n \t\r\n{link}\r\ntext`.'),
                ('fence', f'Example `literal\n~~~text\n{link}\n~~~\n{link}\ntext`.'),
                ('block comment', f'Example `literal\n<!--\n{link}\n-->\n{link}\ntext`.'),
                ('ATX heading', f'Example `literal\n# {link}\ntext`.'),
                ('after an ATX heading', f'## Example `literal\n{link}\ntext`.'),
                ('inline code within a heading', f'## Example `{link}`\n{link}'),
                ('block quote', f'Example `literal\n> {link}\ntext`.'),
                ('bullet list', f'Example `literal\n- {link}\ntext`.'),
                ('ordered list', f'Example `literal\n1. {link}\ntext`.'),
                ('thematic rule', f'Example `literal\n***\n{link}\ntext`.'),
                ('setext underline', f'Example `literal\n===\n{link}\ntext`.'),
            )
            for case, body in cases:
                with self.subTest(target=target, case=case):
                    self.assertEqual(check(body), expected)

    def test_multiline_code_span_inside_a_link_label(self):
        routes = (
            (self._check_bundled_body, 'references/operating-algorithm.md'),
            (self._check_registration_body, '../../../SKILL.md'),
        )
        for check, target in routes:
            with self.subTest(target=target):
                expected = check(f'[Guide]({target})')
                self.assertEqual(expected[0], 0)
                body = f'[Read `literal\n] inside code` here]({target})'
                self.assertEqual(check(body), expected)

    def test_inline_route_labels_stay_within_atx_heading_boundaries(self):
        routes = (
            (self._check_bundled_body, 'references/operating-algorithm.md', 'no bundled references'),
            (
                self._check_registration_body,
                '../../../SKILL.md',
                'Codex registration is missing its canonical skill link',
            ),
        )
        for check, target, missing_error in routes:
            visible = f'[Guide]({target})'
            expected = check(visible)
            self.assertEqual(expected[0], 0)
            suffix = f'continues]({target})'
            crossed_labels = (
                ('paragraph to heading', ('[Paragraph', f'# {suffix}')),
                ('heading to paragraph', ('## [Heading', suffix)),
                ('adjacent headings', ('## [First heading', f'### {suffix}')),
                ('indented heading with tab', ('[Paragraph', f'   ######\t{suffix}')),
                ('empty heading', ('[Paragraph', '######', suffix)),
                ('inline comment crosses heading', ('[Paragraph <!-- note', f'# heading --> {suffix}')),
            )
            valid_labels = (
                ('heading', (f'# {visible}',)),
                ('heading with tab', (f'##\t{visible}',)),
                ('indented heading', (f'   ###### {visible} ###',)),
                ('paragraph soft break', ('[Paragraph', suffix)),
                ('heading-like paragraph text', ('[Paragraph', f'#tag {suffix}')),
                ('inline comment in paragraph', ('[Paragraph <!-- note', f'ends here --> {suffix}')),
                ('block comment across heading', ('<!-- note', f'# {visible}', '-->', visible)),
                ('unclosed inline comment before heading', (f'Intro <!-- {visible}', '# Heading')),
            )
            for newline in ('\n', '\r\n'):
                for case, lines in crossed_labels:
                    with self.subTest(target=target, newline=newline, case=case, visible_link=False):
                        self.assertEqual(check(newline.join(lines)), (1, {'error': missing_error}))
                    with self.subTest(target=target, newline=newline, case=case, visible_link=True):
                        self.assertEqual(check(newline.join((*lines, visible))), expected)
                for case, lines in valid_labels:
                    with self.subTest(target=target, newline=newline, case=case):
                        self.assertEqual(check(newline.join(lines)), expected)

    def test_inline_route_labels_stay_within_thematic_and_setext_boundaries(self):
        bundled_target = 'references/operating-algorithm.md'
        routes = (
            (
                self._check_bundled_body,
                bundled_target,
                'no bundled references',
                f'duplicate bundled resource: {bundled_target}',
            ),
            (
                self._check_registration_body,
                '../../../SKILL.md',
                'Codex registration is missing its canonical skill link',
                'Codex registration repeats its canonical skill link',
            ),
        )
        for check, target, missing_error, duplicate_error in routes:
            visible = f'[Guide]({target})'
            expected = check(visible)
            self.assertEqual(expected[0], 0)
            suffix = f'continues]({target})'
            invalid_routes = (
                ('asterisk rule', ('[Paragraph', '***', suffix)),
                ('indented underscore rule', ('[Paragraph', '   _ _ _\t', suffix)),
                ('spaced dash rule', ('[Paragraph', '- - -', suffix)),
                ('dash underline', ('[Heading', 'more heading text', '---', suffix)),
                ('short dash underline', ('[Heading', '--', suffix)),
                ('equals underline', ('[Heading', 'more heading text', '===', suffix)),
                ('inline comment crosses rule', ('[Paragraph <!-- note', '***', f'--> {suffix}')),
                ('inline comment crosses underline', ('[Heading <!-- note', '===', f'--> {suffix}')),
                ('block comment contains rule', ('<!-- note', '***', visible, '-->')),
                ('fence contains rule', ('```markdown', '- - -', visible, '```')),
            )
            dash_heading = ('Heading [Guide', suffix, '---')
            valid_routes = (
                ('multiline dash heading', dash_heading),
                ('multiline equals heading', ('Heading [Guide', suffix, '===')),
                ('paragraph soft break', ('[Paragraph', suffix)),
                ('inline markers', ('[Paragraph *** _ _ _ - - -', suffix)),
                ('mixed marker line', ('[Paragraph', '*_-*_-', suffix)),
                ('marker prefix with text', ('[Paragraph', '*** text', suffix)),
            )
            for newline in ('\n', '\r\n'):
                for case, lines in invalid_routes:
                    with self.subTest(target=target, newline=newline, case=case, visible_link=False):
                        self.assertEqual(check(newline.join(lines)), (1, {'error': missing_error}))
                    with self.subTest(target=target, newline=newline, case=case, visible_link=True):
                        self.assertEqual(check(newline.join((*lines, visible))), expected)
                for case, lines in valid_routes:
                    with self.subTest(target=target, newline=newline, case=case):
                        self.assertEqual(check(newline.join(lines)), expected)
                with self.subTest(target=target, newline=newline, case='genuine duplicate'):
                    self.assertEqual(
                        check(newline.join((*dash_heading, visible))), (1, {'error': duplicate_error}),
                    )

    def test_indented_top_level_code_cannot_supply_or_duplicate_routes(self):
        bundled_target = 'references/operating-algorithm.md'
        routes = (
            (
                ROOT / 'SKILL.md',
                bundled_target,
                'no bundled references',
                f'duplicate bundled resource: {bundled_target}',
            ),
            (
                ROOT / '.agents/skills/booster-ai/SKILL.md',
                '../../../SKILL.md',
                'Codex registration is missing its canonical skill link',
                'Codex registration repeats its canonical skill link',
            ),
        )
        for document, target, missing_error, duplicate_error in routes:
            frontmatter = document.read_text(encoding='utf-8').split('---\n', 2)[1]
            header = f'---\n{frontmatter}---\n'
            link = f'[Guide]({target})'
            expected = self._check_with_text(document, header + link)
            self.assertEqual(expected[0], 0)
            hidden_routes = (
                ('leading spaces', (f'    {link}',)),
                ('leading tab', (f'\t{link}',)),
                ('leading mixed indentation', (f'  \t{link}',)),
                ('after a blank line', ('Introduction.', ' \t', f'    {link}')),
                ('indented chunks', (f'    {link}', '', f'\t{link}', ' \t', f' \t{link}')),
                ('literal comment opener', ('    <!--', f'    {link}')),
                ('literal opening bracket', ('    [Example', f'caption]({target})')),
                ('after a definite list dedent', (
                    '- List item.',
                    '',
                    'Top-level paragraph.',
                    '',
                    f'    {link}',
                )),
                ('after a top-level heading', ('- List item.', '# Outside', '', f'    {link}')),
                ('after a top-level fence', (
                    '- List item.', '```', 'Example.', '```', '', f'    {link}',
                )),
                ('after a top-level block comment', (
                    '- List item.', '<!-- Example -->', '', f'    {link}',
                )),
                ('after a top-level thematic break', ('- List item.', '***', '', f'    {link}')),
                ('unclosed inline comment before a list dedent', (
                    '- List item <!-- open', ' \t', 'Top-level paragraph.', '', f'    {link}',
                )),
                ('unclosed inline comment before a top-level fence', (
                    '- List item <!-- open', '```markdown', link, '```', '', f'    {link}',
                )),
                ('unclosed inline comment before a top-level block comment', (
                    '- List item <!-- open', '<!--', link, '-->', '', f'    {link}',
                )),
                ('closed multiline inline comment', (
                    '- List item <!-- open', f'  {link}', '  -->',
                )),
                ('block comment across blank lines and block starters', (
                    '<!--', '', '# Heading', '```', link, '```', '', f'    {link}', '-->',
                )),
                ('block comment', ('<!--', '', f'    {link}', '-->')),
                ('fenced example', ('```markdown', '', '    <!--', f'    {link}', '```')),
            )
            visible_routes = (
                ('hanging spaces', ('Paragraph text.', f'    {link}')),
                ('hanging tab', ('Paragraph text.', f'\t{link}')),
                ('label in a hanging continuation', ('[Guide', f'    caption]({target})')),
                ('bullet continuation', ('- List item.', '', f'    {link}')),
                ('numbered continuation', ('1. List item.', '', f'    {link}')),
                ('after a list item heading', ('- List item.', '  # Inside', '', f'    {link}')),
                ('after a list item fence', (
                    '- List item.', '  ```', '  Example.', '  ```', '', f'    {link}',
                )),
                ('after a list item block comment', (
                    '- List item.', '  <!-- Example -->', '', f'    {link}',
                )),
                ('after a list item thematic break', ('- List item.', '  ***', '', f'    {link}')),
                ('after a possible equals underline', ('- List item.', '=', '', f'    {link}')),
                ('after a possible dash underline', ('- List item.', '--', '', f'    {link}')),
                ('three spaces after a blank line', ('Introduction.', '', f'   {link}')),
            )
            for newline in ('\n', '\r\n'):
                for case, lines in hidden_routes:
                    with self.subTest(target=target, newline=newline, case=case, visible_link=False):
                        self.assertEqual(
                            self._check_with_text(document, header + newline.join(lines)),
                            (1, {'error': missing_error}),
                        )
                    with self.subTest(target=target, newline=newline, case=case, visible_link=True):
                        self.assertEqual(
                            self._check_with_text(document, header + newline.join((*lines, link))),
                            expected,
                        )
                for case, lines in visible_routes:
                    with self.subTest(target=target, newline=newline, case=case):
                        self.assertEqual(
                            self._check_with_text(document, header + newline.join(lines)),
                            expected,
                        )
                with self.subTest(target=target, newline=newline, case='genuine duplicate'):
                    self.assertEqual(
                        self._check_with_text(document, header + newline.join((link, '', link))),
                        (1, {'error': duplicate_error}),
                    )


    def test_indented_code_after_completed_top_level_blocks_is_opaque(self):
        """A completed block permits adjacent code without ending a live paragraph."""
        bundled_target = 'references/operating-algorithm.md'
        routes = (
            (ROOT / 'SKILL.md', bundled_target, 'no bundled references',
             f'duplicate bundled resource: {bundled_target}'),
            (ROOT / '.agents/skills/booster-ai/SKILL.md', '../../../SKILL.md',
             'Codex registration is missing its canonical skill link',
             'Codex registration repeats its canonical skill link'),
        )
        completed_blocks = (
            ('ATX heading', ('# Heading',)),
            ('indented ATX heading', ('   ## Heading',)),
            ('thematic break', ('* * *',)),
            ('dash thematic break', ('---',)),
            ('asterisk break with one leading space', (' * * *',)),
            ('underscore break with two leading spaces', ('  _ _ _',)),
            ('dash break with three leading spaces', ('   - - -',)),
            ('backtick fence', ('``` example', 'Example', '````')),
            ('tilde fence', ('~~~', 'Example.', '~~~')),
            ('single-line block comment', ('<!-- Example -->',)),
            ('multiline block comment', ('<!--', 'Example', '--> \t')),
            ('heading ends list', ('- List item.', '# Outside')),
            ('comment ends list', ('- List item.', '<!-- Outside -->')),
        )
        for document, target, missing_error, duplicate_error in routes:
            frontmatter = document.read_text(encoding='utf-8').split('---\n', 2)[1]
            header = f'---\n{frontmatter}---\n'
            link = f'[Guide]({target})'
            expected = self._check_with_text(document, header + link)
            self.assertEqual(expected[0], 0)
            visible_prefixes = (
                ('paragraph continuation', ('Paragraph.',)),
                ('paragraph after a heading', ('# Heading', 'Paragraph.')),
                ('paragraph after a fence', ('~~~', 'Example.', '~~~', 'Paragraph.')),
                ('inline comment in paragraph', ('Paragraph <!-- inline -->',)),
                ('heading inside a list', ('- List item.', '  # Inside')),
                ('fence inside a list', ('- List item.', '  ~~~', '  Example.', '  ~~~')),
                ('comment inside a list', ('- List item.', '  <!-- Example -->')),
                ('thematic break inside a list', ('- List item.', '  ***')),
                ('possible equals underline', ('==',)),
                ('possible dash underline', ('--',)),
            )
            for newline in ('\n', '\r\n'):
                for case, lines in completed_blocks:
                    for indent in ('    ', '\t', '  \t'):
                        body = (*lines, indent + link)
                        with self.subTest(target=target, newline=newline, case=case, indent=indent,
                                          visible_link=False):
                            self.assertEqual(
                                self._check_with_text(document, header + newline.join(body)),
                                (1, {'error': missing_error}),
                            )
                        with self.subTest(target=target, newline=newline, case=case, indent=indent,
                                          visible_link=True):
                            self.assertEqual(
                                self._check_with_text(document, header + newline.join((*body, link))),
                                expected,
                            )
                for case, lines in visible_prefixes:
                    with self.subTest(target=target, newline=newline, case=case):
                        self.assertEqual(
                            self._check_with_text(document, header + newline.join((*lines, '    ' + link))),
                            expected,
                        )
                with self.subTest(target=target, newline=newline, case='genuine duplicate'):
                    lines = ('# Heading', '    example', link, '', link)
                    self.assertEqual(
                        self._check_with_text(document, header + newline.join(lines)),
                        (1, {'error': duplicate_error}),
                    )


    def test_indented_code_after_single_line_setext_headings_is_opaque(self):
        """Only a supported Setext heading starts adjacent indented code."""
        bundled_target = 'references/operating-algorithm.md'
        routes = (
            (ROOT / 'SKILL.md', bundled_target, 'no bundled references',
             f'duplicate bundled resource: {bundled_target}'),
            (ROOT / '.agents/skills/booster-ai/SKILL.md', '../../../SKILL.md',
             'Codex registration is missing its canonical skill link',
             'Codex registration repeats its canonical skill link'),
        )
        headings = (
            ('equals at start', (), 'Title', '==='),
            ('short dash at start', (), 'Title', '-'),
            ('indented title and underline', (), '   Title', '   -- \t'),
            ('after a blank line', ('Introduction.', ' \t'), '  Title', ' == \t'),
            ('after a definite list dedent', ('- Item.', ''), 'Title', '--'),
        )
        paragraph_prefixes = (
            ('lone equals', ('===',)),
            ('lone short dash', ('--',)),
            ('separator after a blank line', ('Introduction.', '', '===')),
            ('separator after an ATX heading', ('# Title', '--')),
            ('separator after a fence', ('```', 'Title', '```', '===')),
            ('separator after a comment', ('<!-- Note -->', '--')),
            ('separator after a multiline comment', ('<!--', '', 'Title -->', '===')),
            ('list heading', ('- Item.', '  Title', '  --')),
            ('list heading after a blank line', ('- Item.', '', '  Title', '  ===')),
            ('ordinary paragraph', ('Title',)),
            ('underline with trailing text', ('Title', '== text')),
            ('underline with mixed markers', ('Title', '=-=')),
        )
        for document, target, missing_error, duplicate_error in routes:
            frontmatter = document.read_text(encoding='utf-8').split('---\n', 2)[1]
            header = f'---\n{frontmatter}---\n'
            link = f'[Guide]({target})'
            visible = self._check_with_text(document, header + link)
            self.assertEqual(visible[0], 0)
            missing = (1, {'error': missing_error})
            duplicate = (1, {'error': duplicate_error})

            for newline in ('\n', '\r\n'):
                for case, prefix, title, underline in headings:
                    for indent in ('    ', '\t', '  \t'):
                        code = indent + link
                        cases = (
                            ('code only', (*prefix, title, underline, code), missing),
                            ('visible after code', (*prefix, title, underline, code, link), visible),
                            ('visible in title', (*prefix, f'{title} `{link}` {link}', underline, code), visible),
                            ('genuine duplicate', (*prefix, f'{title} {link}', underline, code, link), duplicate),
                        )
                        for location, lines, expected in cases:
                            with self.subTest(target=target, newline=newline, case=case, indent=indent,
                                              location=location):
                                self.assertEqual(
                                    self._check_with_text(document, header + newline.join(lines)),
                                    expected,
                                )

                for case, prefix in paragraph_prefixes:
                    with self.subTest(target=target, newline=newline, case=case):
                        lines = (*prefix, '    ' + link)
                        self.assertEqual(
                            self._check_with_text(document, header + newline.join(lines)),
                            visible,
                        )


    def test_indented_code_after_setext_headings_adjacent_to_completed_blocks_is_opaque(self):
        """A completed top-level block permits an adjacent single-line Setext heading."""
        bundled_target = 'references/operating-algorithm.md'
        routes = (
            (ROOT / 'SKILL.md', bundled_target, 'no bundled references',
             f'duplicate bundled resource: {bundled_target}'),
            (ROOT / '.agents/skills/booster-ai/SKILL.md', '../../../SKILL.md',
             'Codex registration is missing its canonical skill link',
             'Codex registration repeats its canonical skill link'),
        )
        completed_blocks = (
            ('ATX heading', ('# Previous',), 'Title', '=', '    '),
            ('thematic break', ('  * * *',), '   Title', '   -- \t', '\t'),
            ('backtick fence', ('``` text', 'Example', '```` \t'), ' Title', '==', '  \t'),
            ('tilde fence', ('~~~', 'Example', '~~~~'), 'Title', ' --', '    '),
            ('single-line comment', ('<!-- Previous --> \t',), '  Title', '===', '\t'),
            ('multiline comment', ('<!--', 'Example', '--> \t'), 'Title', '-', '  \t'),
            ('prior Setext heading', ('Previous', '==='), '   Title', '  --', '    '),
            ('stacked Setext headings', ('# Previous', 'First', '=', 'Second', '--'),
             'Third', '===', '\t'),
            ('definite list exit', ('- Item.', '# Previous'), 'Title', '--', '  \t'),
        )
        visible_prefixes = (
            ('lone equals after heading', ('# Previous', '===')),
            ('lone dash after fence', ('~~~', 'Example', '~~~', '--')),
            ('lone equals after comment', ('<!-- Previous -->', '==')),
            ('ordinary paragraph', ('# Previous', 'Paragraph.')),
            ('invalid underline', ('# Previous', 'Title', '- -')),
            ('comment closing line is not a title', ('<!--', '', 'Previous -->', '===')),
            ('possible list heading', ('- Item.', '  # Previous', '  Title', '  --')),
            ('possible list after fence', ('- Item.', '  ~~~', '  Example', '  ~~~',
                                          '  Title', '  ===')),
        )
        for document, target, missing_error, duplicate_error in routes:
            frontmatter = document.read_text(encoding='utf-8').split('---\n', 2)[1]
            header = f'---\n{frontmatter}---\n'
            link = f'[Guide]({target})'
            visible = self._check_with_text(document, header + link)
            self.assertEqual(visible[0], 0)
            missing = (1, {'error': missing_error})
            duplicate = (1, {'error': duplicate_error})

            for newline in ('\n', '\r\n'):
                for case, prefix, title, underline, indent in completed_blocks:
                    code = indent + link
                    cases = (
                        ('code only', (*prefix, title, underline, code), missing),
                        ('visible afterward', (*prefix, title, underline, code, link), visible),
                        ('visible in title', (*prefix, f'{title} `{link}` {link}', underline, code),
                         visible),
                        ('genuine duplicate', (*prefix, f'{title} {link}', underline, code, link),
                         duplicate),
                    )
                    for location, lines, expected in cases:
                        with self.subTest(target=target, newline=newline, case=case, location=location):
                            self.assertEqual(
                                self._check_with_text(document, header + newline.join(lines)),
                                expected,
                            )

                for case, prefix in visible_prefixes:
                    with self.subTest(target=target, newline=newline, case=case):
                        lines = (*prefix, '    ' + link)
                        self.assertEqual(
                            self._check_with_text(document, header + newline.join(lines)),
                            visible,
                        )

                for opener in ('<!--', '``` example', '~~~'):
                    with self.subTest(target=target, newline=newline, unclosed=opener):
                        lines = (opener, 'Title ' + link, '===', '    ' + link)
                        self.assertEqual(
                            self._check_with_text(document, header + newline.join(lines)),
                            missing,
                        )


class SelfCheckRootCliTests(unittest.TestCase):
    """Check root selection at the CLI boundary without a package fixture."""

    def test_omitted_and_single_roots_reach_self_check(self):
        root = ' selected package '
        cases = (([], str(ROOT)), (['--root', root], root))
        for options, expected_root in cases:
            with (
                self.subTest(options=options),
                patch.object(sys, 'argv', ['booster.py', 'self-check', *options]),
                patch.object(b, 'self_check', return_value={'valid': True}) as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(b.main(), 0)
                check.assert_called_once_with(expected_root)
                self.assertEqual(json.loads(stdout.getvalue()), {'valid': True})
                self.assertEqual(stderr.getvalue(), '')

    def test_invalid_roots_stop_before_self_check(self):
        first, second = 'first-private-root', 'second-private-root'
        cases = (
            ['--root='],
            ['--root= '],
            ['--root=\t'],
            ['--root', first, f'--root={first}'],
            ['--root', first, '--root', second],
        )
        for options in cases:
            with (
                self.subTest(options=options),
                patch.object(sys, 'argv', ['booster.py', 'self-check', *options]),
                patch.object(b, 'self_check', return_value={'valid': True}) as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                with self.assertRaises(SystemExit) as error:
                    b.main()
                self.assertEqual(error.exception.code, 2)
                check.assert_not_called()
                self.assertEqual(stdout.getvalue(), '')
                output = stderr.getvalue()
                diagnostic = output.partition('error:')[2].strip()
                self.assertEqual(diagnostic, '--root may be supplied only once with a nonempty value')
                self.assertNotIn(first, output)
                self.assertNotIn(second, output)

class SchemaStatusRootCliTests(unittest.TestCase):
    """Exercise schema-status argument selection without a workspace fixture."""

    def test_single_root_reaches_schema_status_unchanged(self):
        root = ' selected project '
        report = {'acceptance_verified': False}
        with (
            patch.object(sys, 'argv', ['booster.py', 'schema-status', '--root', root]),
            patch.object(b, 'schema_status', return_value=report) as check,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(b.main(), 0)

        check.assert_called_once_with(root)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertEqual(stderr.getvalue(), '')

    def test_invalid_roots_stop_before_schema_status(self):
        first, second = 'first-private-root', 'second-private-root'
        cases = (
            [],
            ['--root='],
            ['--root= '],
            ['--root', first, f'--root={first}'],
            ['--root', first, '--root', second],
            ['--root', second, '--root', first],
        )
        for options in cases:
            with (
                self.subTest(options=options),
                patch.object(sys, 'argv', ['booster.py', 'schema-status', *options]),
                patch.object(b, 'schema_status') as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                with self.assertRaises(SystemExit) as error:
                    b.main()
                self.assertEqual(error.exception.code, 2)
                check.assert_not_called()
                self.assertEqual(stdout.getvalue(), '')
                diagnostic = ('--root may be supplied only once with a nonempty value'
                              if options else 'the following arguments are required: --root')
                self.assertEqual(stderr.getvalue(),
                                 'usage: booster.py schema-status [-h] --root ROOT\n'
                                 f'booster.py schema-status: error: {diagnostic}\n')


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.root=workspace(self)
        (self.root/'.booster-tmp').mkdir();self.file=self.root/'.booster-tmp/out.txt';self.file.write_text('owned scratch')
        self.item={'path':'.booster-tmp/out.txt','owner':'test','inactive':True,'retain':False,'expires_at':'2020-01-01T00:00:00Z','sha256':hashlib.sha256(self.file.read_bytes()).hexdigest()}
        self.save()
    def save(self):(self.root/'manifest.json').write_text(json.dumps({'owner':'test','files':[self.item]}))
    def run_gc(self,apply=False):return b.collect(str(self.root),'manifest.json','test',apply)
    def test_dry_run(self):
        self.assertEqual(len(self.run_gc()['eligible']),1);self.assertTrue(self.file.exists())
    def test_actual_move_and_recovery(self):
        result=self.run_gc(True);self.assertFalse(self.file.exists());self.assertEqual(len(result['moved']),1)
        self.assertEqual((self.root/result['moved'][0]['destination']).read_text(),'owned scratch')
        self.assertTrue((self.root/result['recovery']).is_file())
        mapping=json.loads((self.root/result['recovery']).read_text())
        original=b.checked(self.root,mapping[0]['source']); saved=b.checked(self.root,mapping[0]['destination'])
        self.assertFalse(original.exists());self.assertEqual(b.digest(saved),mapping[0]['sha256'])
        saved.rename(original)
        self.assertEqual(original.read_text(),'owned scratch')
    def test_retained_active_unexpired_changed(self):
        original=self.item.copy()
        for field,value in [('retain',True),('inactive',False),('expires_at','2999-01-01T00:00:00Z'),('sha256','changed'),('owner','other'),('path','../outside')]:
            self.item=original.copy();self.item[field]=value;self.save()
            self.assertEqual(self.run_gc(True)['moved'],[]);self.assertTrue(self.file.exists())
    def test_lock(self):
        (self.root/'.booster-gc.lock').write_text('other owner')
        with self.assertRaises(FileExistsError):self.run_gc(True)
        self.assertTrue(self.file.exists());self.assertEqual((self.root/'.booster-gc.lock').read_text(),'other owner')
    def test_collector_preserves_changed_lock_content(self):
        lock=self.root/'.booster-gc.lock';original_rename=Path.rename
        def changed(path,destination):
            lock.write_bytes(b'changed by fixture writer')
            return original_rename(path,destination)
        with patch.object(Path,'rename',changed):
            with self.assertRaisesRegex(ValueError,'collector lock content changed'):self.run_gc(True)
        self.assertEqual(lock.read_bytes(),b'changed by fixture writer')
        mapping_path=next((self.root/'.booster-quarantine').glob('*/recovery.json'))
        mapping=json.loads(mapping_path.read_text(encoding='utf-8'))
        saved=self.root/mapping[0]['destination'];self.assertEqual(saved.read_text(),'owned scratch')
        saved.rename(self.file)
        with self.assertRaises(FileExistsError):self.run_gc(True)
        self.assertEqual(lock.read_bytes(),b'changed by fixture writer')
    def test_collector_preserves_replacement_lock(self):
        lock=self.root/'.booster-gc.lock';original=self.root/'original-owned-lock';original_rename=Path.rename
        def replaced(path,destination):
            os.replace(lock,original);lock.write_bytes(b'replacement fixture owner')
            return original_rename(path,destination)
        with patch.object(Path,'rename',replaced):
            with self.assertRaisesRegex(ValueError,'collector lock identity changed'):self.run_gc(True)
        self.assertEqual(lock.read_bytes(),b'replacement fixture owner');self.assertTrue(original.is_file())
    def test_collector_stops_following_moves_after_lock_change(self):
        second=self.root/'.booster-tmp/second.txt';second.write_text('second owned scratch')
        item=dict(self.item,path='.booster-tmp/second.txt',sha256=hashlib.sha256(second.read_bytes()).hexdigest())
        (self.root/'manifest.json').write_text(json.dumps({'owner':'test','files':[self.item,item]}))
        lock=self.root/'.booster-gc.lock';original_rename=Path.rename;calls=[]
        def changed(path,destination):
            result=original_rename(path,destination);calls.append(path)
            lock.write_bytes(b'changed by fixture writer')
            return result
        with patch.object(Path,'rename',changed):
            with self.assertRaisesRegex(ValueError,'collector lock content changed'):self.run_gc(True)
        self.assertEqual(len(calls),1);self.assertFalse(self.file.exists());self.assertTrue(second.is_file())
        self.assertEqual(lock.read_bytes(),b'changed by fixture writer')
        self.assertEqual(len(list((self.root/'.booster-quarantine').glob('*/recovery.json'))),1)
    def test_collector_preserves_hardlinked_lock(self):
        lock=self.root/'.booster-gc.lock';alias=self.root/'lock-alias';original_rename=Path.rename
        def linked(path,destination):
            alias.hardlink_to(lock)
            return original_rename(path,destination)
        with patch.object(Path,'rename',linked):
            with self.assertRaisesRegex(ValueError,'collector lock link/reparse forbidden'):self.run_gc(True)
        self.assertTrue(lock.is_file());self.assertEqual(lock.read_bytes(),alias.read_bytes())
    def test_collector_lock_initialization_preserves_permission_denial(self):
        with patch.object(b.os,'write',side_effect=PermissionError('fixture access denied')):
            with self.assertRaisesRegex(PermissionError,'fixture access denied'):self.run_gc(True)
        self.assertTrue((self.root/'.booster-gc.lock').is_file());self.assertTrue(self.file.is_file())
        self.assertFalse((self.root/'.booster-quarantine').exists())
    def test_gc_cli_reports_reconciliation_for_existing_lock(self):
        lock=self.root/'.booster-gc.lock';lock.write_text('retained owner')
        cli=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),'gc-apply','--root',str(self.root),'--manifest','manifest.json','--owner','test'],capture_output=True,text=True)
        self.assertEqual(cli.returncode,1,cli.stdout+cli.stderr)
        result=json.loads(cli.stdout);self.assertTrue(result['reconciliation_required'])
        self.assertEqual(result['error_type'],'FileExistsError');self.assertEqual(result['effect'],'UNKNOWN');self.assertEqual(lock.read_text(),'retained owner')
        self.assertTrue(self.file.is_file())
    def test_gc_cli_reports_unknown_after_partial_move(self):
        code='''import importlib.util, sys\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location("booster_fixture",sys.argv[1]); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\nroot=Path(sys.argv[2]); original=Path.rename\ndef changed(path,destination):\n    result=original(path,destination); (root/".booster-gc.lock").write_bytes(b"changed by fixture writer"); return result\nPath.rename=changed\nsys.argv=[sys.argv[1],"gc-apply","--root",str(root),"--manifest","manifest.json","--owner","test"]\nraise SystemExit(module.main())\n'''
        cli=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT/'scripts/booster.py'),str(self.root)],capture_output=True,text=True)
        self.assertEqual(cli.returncode,1,cli.stdout+cli.stderr)
        result=json.loads(cli.stdout);self.assertTrue(result['reconciliation_required']);self.assertEqual(result['effect'],'UNKNOWN')
        self.assertEqual(result['error_type'],'ValueError');self.assertEqual((self.root/'.booster-gc.lock').read_bytes(),b'changed by fixture writer')
        self.assertFalse(self.file.exists());self.assertEqual(len(list((self.root/'.booster-quarantine').glob('*/recovery.json'))),1)
    def test_gc_cli_preserves_first_permission_denial_when_lock_also_changes(self):
        code='''import importlib.util, sys\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location("booster_fixture",sys.argv[1]); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\nroot=Path(sys.argv[2])\ndef denied(path,destination):\n    (root/".booster-gc.lock").write_bytes(b"changed by fixture writer"); raise PermissionError("fixture access denied")\nPath.rename=denied\nsys.argv=[sys.argv[1],"gc-apply","--root",str(root),"--manifest","manifest.json","--owner","test"]\nraise SystemExit(module.main())\n'''
        cli=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT/'scripts/booster.py'),str(self.root)],capture_output=True,text=True)
        self.assertEqual(cli.returncode,1,cli.stdout+cli.stderr)
        result=json.loads(cli.stdout);self.assertTrue(result['reconciliation_required'])
        self.assertEqual(result['first_error_type'],'PermissionError');self.assertIn('fixture access denied',result['first_error'])
        self.assertTrue(self.file.exists());self.assertEqual((self.root/'.booster-gc.lock').read_bytes(),b'changed by fixture writer')
    @unittest.skipUnless(sys.platform=='win32','Windows junction test')
    def test_checked_refuses_broken_junction(self):
        target=self.root/'junction-target';target.mkdir();junction=self.root/'broken-junction'
        made=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(target)],capture_output=True,text=True)
        self.assertEqual(made.returncode,0,made.stdout+made.stderr);self.addCleanup(junction.rmdir)
        target.rmdir()
        with self.assertRaisesRegex(ValueError,'links/reparse'):b.checked(self.root,'broken-junction/item')
    def test_hardlink(self):
        (self.root/'.booster-tmp/alias.txt').hardlink_to(self.file)
        self.assertEqual(self.run_gc(True)['moved'],[])
    def test_cli_nested_error_json(self):
        p=plan();p['next']=None;p['nodes'][0].update(status='verified',evidence=None)
        target=self.root/'invalid-plan.json';target.write_text(json.dumps(p))
        result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/booster.py'),'validate',str(target)],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stdout),{'error':'evidence must be object'})
        self.assertEqual(result.stderr,'')
    def test_malformed_manifest(self):
        for value in (None, [], {'owner':'test','files':[None]}):
            (self.root/'manifest.json').write_text(json.dumps(value))
            with self.subTest(manifest=value),self.assertRaises(ValueError): self.run_gc(True)
            self.assertTrue(self.file.exists())
    def test_self_check_without_site_packages(self):
        result=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),'self-check'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        outcome=json.loads(result.stdout)
        self.assertEqual(outcome['validator'],'bundled-stdlib')
        self.assertEqual(outcome['codex_repo_registration'],'present_valid')
        self.assertEqual(outcome['canonical_source_sha256'],hashlib.sha256((ROOT/'SKILL.md').read_bytes()).hexdigest())
        self.assertEqual(outcome['canonical_source_sha256'],outcome['registration_target_sha256'])
        self.assertEqual(outcome['python_version'],'.'.join(str(n) for n in sys.version_info[:3]))
        self.assertFalse(outcome['agent_discovery_verified'])
    def test_codex_registration_rejects_mismatches(self):
        canonical=(ROOT/'SKILL.md').read_text(encoding='utf-8').split('---\n',2)[1]
        expected={line.partition(':')[0]:line.partition(':')[2].strip() for line in canonical.splitlines()}
        entry=(ROOT/'.agents/skills/booster-ai/SKILL.md').read_text(encoding='utf-8')
        self.assertTrue(b.check_codex_entry(entry,expected))
        for changed in (entry.replace('name: booster-ai','name: different'),
                        entry.replace('../../../SKILL.md','../../../missing.md'),
                        entry.replace('description: ', 'description: changed ', 1),
                        entry.replace('name: booster-ai\n', 'name: booster-ai\nname: booster-ai\n',1)):
            with self.subTest(change=changed[:90]),self.assertRaises(ValueError):
                b.check_codex_entry(changed,expected)
    def test_self_check_reports_missing_and_broken_registration(self):
        target=self.root/'portable-copy';target.mkdir()
        for name in ('SKILL.md','README.md'):
            shutil.copy2(ROOT/name,target/name)
        for name in ('scripts','references','tests','solutions'):
            shutil.copytree(ROOT/name,target/name)
        missing=b.self_check(target)
        self.assertEqual(missing['codex_repo_registration'],'not_present')
        self.assertIsNone(missing['registration_target_sha256'])
        self.assertFalse(missing['agent_discovery_verified'])
        entry=target/'.agents/skills/booster-ai/SKILL.md';entry.parent.mkdir(parents=True)
        entry.write_text('---\nname: broken\ndescription: broken\n---\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'metadata differs'):
            b.self_check(target)
    def test_self_check_missing_resource(self):
        target=self.root/'skill-fixture';target.mkdir()
        (target/'SKILL.md').write_text('---\nname: booster-ai\ndescription: test\n---\n[missing](references/missing.md)\n')
        with self.assertRaisesRegex(ValueError,'missing bundled resource'):b.self_check(target)
    @unittest.skipUnless(sys.platform=='win32','Windows junction test')
    def test_self_check_rejects_junction_root(self):
        target=self.root/'actual';target.mkdir()
        marker=target/'preserve.txt';marker.write_text('preserve')
        junction=self.root/'linked'
        made=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(target)],capture_output=True,text=True)
        self.assertEqual(made.returncode,0,made.stdout+made.stderr);self.addCleanup(junction.rmdir)
        cli=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),'self-check','--root',str(junction)],capture_output=True,text=True)
        self.assertEqual(cli.returncode,1,cli.stdout+cli.stderr)
        self.assertIn('root link forbidden',json.loads(cli.stdout)['error'])
        with self.assertRaisesRegex(ValueError,'root link forbidden'):b.schema_status(junction)
        self.assertEqual(marker.read_text(),'preserve')
    def test_partials_init_new_standard_and_idempotence(self):
        first=b.partials_init(self.root)
        index=self.root/'_boosterAI/_partials/README.md'
        self.assertTrue(first['created'])
        self.assertFalse(first['preserved_existing'])
        self.assertEqual(first['directory'],'_boosterAI/_partials')
        self.assertEqual(first['index'],'_boosterAI/_partials/README.md')
        self.assertIsNone(first['legacy_location'])
        self.assertIsNone(first['legacy_root_location'])
        self.assertTrue(index.is_file())
        self.assertFalse((self.root/'_partials').exists())
        original=index.read_bytes();before=index.stat()
        second=b.partials_init(self.root)
        after=index.stat()
        self.assertFalse(second['created'])
        self.assertTrue(second['preserved_existing'])
        self.assertEqual(second['legacy_location'],'_boosterAI/_partials')
        self.assertIsNone(second['legacy_root_location'])
        self.assertEqual(index.read_bytes(),original)
        self.assertEqual((after.st_ino,after.st_size,after.st_mtime_ns),
                         (before.st_ino,before.st_size,before.st_mtime_ns))

    def test_partials_init_preserves_existing(self):
        standard=self.root/'_boosterAI/_partials';standard.mkdir(parents=True)
        legacy=self.root/'_partials';legacy.mkdir()
        files={standard/'README.md':b'existing standard notes\n',
               standard/'piece.py':b'existing standard code\n',
               legacy/'README.md':b'existing legacy notes\n',
               legacy/'piece.py':b'existing legacy code\n'}
        for path,content in files.items():path.write_bytes(content)
        before={path:(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns) for path in files}
        first=b.partials_init(self.root)
        second=b.partials_init(self.root)
        self.assertFalse(first['created']);self.assertFalse(second['created'])
        self.assertEqual(first['legacy_location'],'_boosterAI/_partials')
        self.assertEqual(first['legacy_root_location'],'_partials')
        self.assertEqual(first['index'],'_boosterAI/_partials/README.md')
        for path,original in before.items():
            self.assertEqual((path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns),original)

    def test_partials_init_preserves_legacy_root_file(self):
        legacy=self.root/'_partials';legacy.write_bytes(b'owner file is not a directory')
        before=(legacy.read_bytes(),legacy.stat().st_ino,legacy.stat().st_mtime_ns)
        first=b.partials_init(self.root)
        self.assertTrue(first['created'])
        self.assertIsNone(first['legacy_location'])
        self.assertEqual(first['legacy_root_location'],'_partials')
        self.assertTrue((self.root/'_boosterAI/_partials/README.md').is_file())
        self.assertEqual((legacy.read_bytes(),legacy.stat().st_ino,legacy.stat().st_mtime_ns),before)
        self.assertFalse(b.partials_init(self.root)['created'])
        self.assertEqual((legacy.read_bytes(),legacy.stat().st_ino,legacy.stat().st_mtime_ns),before)

    def test_partials_init_preserves_legacy_directory_when_standard_is_missing(self):
        legacy=self.root/'_partials';legacy.mkdir()
        existing=legacy/'README.md';existing.write_bytes(b'legacy project notes')
        before=(existing.read_bytes(),existing.stat().st_ino,existing.stat().st_mtime_ns)
        report=b.partials_init(self.root)
        self.assertTrue(report['created'])
        self.assertIsNone(report['legacy_location'])
        self.assertEqual(report['legacy_root_location'],'_partials')
        self.assertTrue((self.root/'_boosterAI/_partials/README.md').is_file())
        self.assertEqual((existing.read_bytes(),existing.stat().st_ino,existing.stat().st_mtime_ns),before)
    def test_partials_consumer_uses_own_code_without_sample_dependencies(self):
        result=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),'partials-init','--root',str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        partials=self.root/'_boosterAI/_partials'
        legacy=self.root/'_partials';legacy.mkdir()
        samples=[]
        for directory in (partials,legacy):
            sample=directory/'normalize_ab12cd34.py'
            sample.write_text('def normalize(value):\n    """Return stripped lowercase text without side effects."""\n    return value.strip().lower()\n')
            samples.append(sample)
        before={path:(path.read_bytes(),path.stat().st_mtime_ns) for path in samples}
        # The consumer reads for guidance, then writes a separate product implementation.
        for sample in samples:
            self.assertIn('strip().lower()',sample.read_text())
        app=self.root/'app.py'
        app.write_text('import sys\ndef greet(value):\n    return "Hello " + value.strip().lower()\nprint(greet(sys.argv[1]))\n')
        for phase in ('available','unavailable'):
            if phase=='unavailable':
                # Only this test's owned fixture directories are temporarily renamed.
                partials.rename(partials.with_name('samples-unavailable'))
                legacy.rename(legacy.with_name('legacy-unavailable'))
            actual=subprocess.run([sys.executable,'-I','-S','-B',str(app),' ALICE '],capture_output=True,text=True)
            self.assertEqual(actual.returncode,0,actual.stderr)
            self.assertEqual(actual.stdout.strip(),'Hello alice')
        for sample,(content,mtime) in before.items():
            directory='samples-unavailable' if sample.parent==partials else 'legacy-unavailable'
            preserved=sample.parent.with_name(directory)/sample.name
            self.assertEqual((preserved.read_bytes(),preserved.stat().st_mtime_ns),(content,mtime))
        guidance=(partials.with_name('samples-unavailable')/'README.md').read_text()
        self.assertIn('read-only learning references',guidance)
        self.assertIn('verified clickable file links when the host supports them',guidance)
        self.assertIn("Give each file's purpose and observed test status.",guidance)
        self.assertIn('give the exact absolute path in the authorized local audience',guidance)
        self.assertIn('Check that targets exist; never invent links',guidance)
        self.assertIn('Never import, link, include, dynamically load or build against',guidance)

    def test_partials_refuses_missing_root_and_file_collision(self):
        with self.assertRaises(ValueError):b.partials_init(self.root/'absent')
        owned=self.root/'_boosterAI';owned.mkdir()
        collision=owned/'_partials';collision.write_text('do not overwrite')
        with self.assertRaises(OSError):b.partials_init(self.root)
        self.assertEqual(collision.read_text(),'do not overwrite')
    def test_workspace_real_cli_and_preservation(self):
        source=self.root/'existing-schema.md';source.write_text('# Existing schema\nunchanged source')
        original_source = source.read_bytes()
        record=self.root/'existing-record.md';record.write_text('existing record owner')
        args=[sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),'workspace-init','--root',str(self.root),'--records-existing','existing-record.md','--schema','existing-schema.md']
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'_boosterAI/algorithm.md').read_bytes(),source.read_bytes())
        self.assertEqual(record.read_text(),'existing record owner')
        self.assertFalse((self.root/'PROJECT_RECORDS.md').exists())
        self.assertTrue((self.root/'_boosterAI/_partials/README.md').is_file())
        self.assertFalse((self.root/'_partials').exists())
        self.assertIn('`_partials/README.md`',(self.root/'_boosterAI/README.md').read_text())
        status_args = [
            sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/booster.py'),
            'schema-status', '--root', str(self.root),
        ]

        def assert_schema_status(source_changed, working_copy_changed=False):
            expected = {
                'source': source.name,
                'source_changed': source_changed,
                'working_copy_changed': working_copy_changed,
                'acceptance_verified': False,
            }
            self.assertEqual(b.schema_status(self.root), expected)
            status = subprocess.run(status_args, capture_output=True, text=True)
            self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
            self.assertEqual(status.stderr, '')
            self.assertEqual(json.loads(status.stdout), expected)

        assert_schema_status(False)
        source.write_text('updated original')
        assert_schema_status(True)
        self.assertEqual((self.root/'_boosterAI/algorithm.md').read_text(),'# Existing schema\nunchanged source')
        with self.assertRaisesRegex(ValueError,'reconcile'):b.workspace_init(self.root, schema='existing-schema.md')

        source.write_bytes(original_source)
        (self.root / '_boosterAI/algorithm.md').write_bytes(b'# Edited working copy\n')
        assert_schema_status(source_changed=False, working_copy_changed=True)
    def test_workspace_new_records_no_overwrite(self):
        result=b.workspace_init(self.root,create_records=True)
        relative=Path('_boosterAI')/'PROJECT_RECORDS.md'
        self.assertEqual(result['project_record'],str(relative))
        self.assertFalse((self.root/'PROJECT_RECORDS.md').exists())
        record=self.root/relative;record.write_text('user added history')
        algorithm=self.root/'_boosterAI/algorithm.md';algorithm.write_text('owner algorithm')
        b.workspace_init(self.root,create_records=True)
        self.assertEqual(record.read_text(),'user added history')
        self.assertEqual(algorithm.read_text(),'owner algorithm')
        with self.assertRaises(ValueError):b.workspace_init(self.root,records_existing=str(relative),create_records=True)

    def test_workspace_new_cli_scaffolds_only_nested_partials(self):
        args=[sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),
              'workspace-init','--root',str(self.root),'--create-records']
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report['directory'],'_boosterAI/_partials')
        self.assertEqual(report['index'],'_boosterAI/_partials/README.md')
        self.assertEqual(report['project_record'],str(Path('_boosterAI')/'PROJECT_RECORDS.md'))
        self.assertTrue(report['created'])
        self.assertTrue((self.root/'_boosterAI/PROJECT_RECORDS.md').is_file())
        self.assertFalse((self.root/'PROJECT_RECORDS.md').exists())
        self.assertTrue((self.root/'_boosterAI/algorithm.md').is_file())
        self.assertTrue((self.root/'_boosterAI/_partials/README.md').is_file())
        self.assertFalse((self.root/'_partials').exists())

    def test_workspace_cli_preserves_existing_overview_and_both_partials(self):
        standard=self.root/'_boosterAI/_partials';standard.mkdir(parents=True)
        legacy=self.root/'_partials';legacy.mkdir()
        files={self.root/'existing-record.md':b'existing project record',
               self.root/'_boosterAI/algorithm.md':b'existing algorithm',
               self.root/'_boosterAI/README.md':b'existing workspace overview',
               standard/'README.md':b'existing standard index',
               standard/'piece.py':b'existing standard source',
               legacy/'README.md':b'existing legacy index',
               legacy/'piece.py':b'existing legacy source'}
        for path,content in files.items():path.write_bytes(content)
        before={path:(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns) for path in files}
        args=[sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),
              'workspace-init','--root',str(self.root),'--records-existing','existing-record.md']
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout)
        self.assertFalse(report['created'])
        self.assertEqual(report['legacy_location'],'_boosterAI/_partials')
        self.assertEqual(report['legacy_root_location'],'_partials')
        self.assertEqual(report['index'],'_boosterAI/_partials/README.md')
        for path,original in before.items():
            self.assertEqual((path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns),original)
    def test_cli_real_process(self):
        p=self.root/'plan.json';p.write_text(json.dumps(plan()))
        result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/booster.py'),'validate',str(p)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout);self.assertTrue(json.loads(result.stdout)['valid'])


class WorkspaceDefaultCliTests(unittest.TestCase):
    def setUp(self):
        self.root = workspace(self)
        self.owned = self.root / '_boosterAI'
        self.partials = self.owned / '_partials'
        self.partials.mkdir(parents=True)
        (self.owned / 'algorithm.md').write_bytes(b'# Existing algorithm\n')
        (self.owned / 'README.md').write_bytes(b'# Existing workspace\n')
        (self.partials / 'README.md').write_bytes(b'# Existing partial index\n')
        self.root_record = self.root / 'PROJECT_RECORDS.md'
        self.nested_record = self.owned / 'PROJECT_RECORDS.md'

    def _ordinary_path(self, path):
        info = path.lstat()
        self.assertFalse(path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400)
        return info

    def _snapshot(self):
        paths = [self.root]
        for directory in (self.root, self.owned, self.partials):
            self._ordinary_path(directory)
            self.assertTrue(directory.is_dir())
            paths.extend(directory.iterdir())

        state = {}
        for path in paths:
            info = self._ordinary_path(path)
            state[path.relative_to(self.root)] = (
                info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns,
                path.read_bytes() if path.is_file() else None,
            )
        return state

    def _create_records(self):
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/booster.py'),
             'workspace-init', '--root', str(self.root), '--create-records'],
            capture_output=True, text=True,
        )

    def test_workspace_create_records_reuses_existing_root_record(self):
        self.root_record.write_bytes(b'# Existing root records\r\n')
        before = self._snapshot()
        result = self._create_records()
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        report = json.loads(result.stdout)
        self.assertEqual(report['project_record'], 'PROJECT_RECORDS.md')
        self.assertEqual(report['records_status'], 'selected')
        self.assertFalse(report['created'])
        self.assertTrue(report['preserved_existing'])
        self.assertEqual(report['algorithm'], 'preserved_existing')
        self.assertFalse(self.nested_record.exists())

    def test_workspace_create_records_reuses_existing_nested_record(self):
        self.nested_record.write_bytes(b'# Existing nested records\n')
        before = self._snapshot()
        self.assertNotIn(self.root_record.relative_to(self.root), before)
        result = self._create_records()
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        report = json.loads(result.stdout)
        self.assertEqual(report['project_record'], str(self.nested_record.relative_to(self.root)))
        self.assertEqual(report['records_status'], 'selected')
        self.assertFalse(report['created'])
        self.assertTrue(report['preserved_existing'])
        self.assertEqual(report['algorithm'], 'preserved_existing')

    def test_workspace_create_records_rejects_both_defaults_without_changes(self):
        self.root_record.write_bytes(b'# Existing root records\r\n')
        self.nested_record.write_bytes(b'# Existing nested records\n')
        before = self._snapshot()
        result = self._create_records()
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout), {
            'error': 'multiple project records exist; select one with --records-existing',
        })


if __name__=='__main__':unittest.main(verbosity=2)
