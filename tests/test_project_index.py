"""Project-local index behavior through real CLI and guarded failure paths."""
import importlib.util
import hashlib
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from repo_temp import workspace

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('project_index_tools',ROOT/'scripts/project_tools.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


class ProjectIndexTests(unittest.TestCase):
    def setUp(self):
        self.project=workspace(self)
        self.root=p.workspace(self.project)
        scratch=self.project/'scratch';scratch.mkdir()
        environment=patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(scratch)})
        environment.start();self.addCleanup(environment.stop)
        self.scratch=scratch

    def cli(self,*args):
        return subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/project_tools.py'),
                               *args,'--root',str(self.project)],capture_output=True,text=True)

    def add_file(self,relative,content):
        path=self.project/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(content,encoding='utf-8')
        return path

    def test_real_cli_refresh_search_and_metadata_privacy(self):
        readme=self.add_file('_partials/README.md','existing project index; private prose\n')
        piece=self.add_file('_partials/example_a1b2c3d4.py',
                            'raise RuntimeError("must not execute private project code")\n')
        legacy=self.add_file('_boosterAI/_partials/legacy.py','private legacy implementation\n')
        legacy_readme=self.add_file('_boosterAI/_partials/README.md','existing experience references\n')
        experience=self.add_file('_boosterAI/experiences/local.md','private experience text\n')
        helper=self.add_file('_boosterAI/helpers/sample/1.0.0/metadata.json','{"private":"metadata"}\n')
        extra=self.add_file('_boosterAI/records/observation.md','private observation\n')
        protected={path:path.read_bytes() for path in (readme,piece,legacy,legacy_readme,experience,helper,extra)}
        first=self.cli('refresh-project-index','--experience','_boosterAI/records/observation.md')
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        self.assertEqual(json.loads(first.stdout)['status'],'CREATED')
        index=self.root/'project-index.json';initial=index.read_bytes();data=json.loads(initial)
        self.assertEqual(data['format'],'boosterai-project-index-v1')
        self.assertEqual([entry['path'] for entry in data['entries']],sorted(path.relative_to(self.project).as_posix() for path in protected))
        self.assertTrue(all(set(entry)=={'path','kind','size','sha256'} for entry in data['entries']))
        for path,content in protected.items():
            self.assertEqual(path.read_bytes(),content)
            self.assertNotIn(content.strip(),initial)
        self.assertFalse((self.project/'solutions').exists())
        second=self.cli('refresh-project-index')
        self.assertEqual(second.returncode,0,second.stdout+second.stderr)
        self.assertEqual(json.loads(second.stdout)['status'],'UNCHANGED');self.assertEqual(index.read_bytes(),initial)
        piece.write_text('updated partial without execution\n',encoding='utf-8')
        self.add_file('_boosterAI/experiences/next.md','new private experience\n')
        third=self.cli('refresh-project-index')
        self.assertEqual(third.returncode,0,third.stdout+third.stderr)
        self.assertEqual(json.loads(third.stdout)['status'],'UPDATED')
        refreshed=json.loads(index.read_bytes());paths={entry['path'] for entry in refreshed['entries']}
        self.assertIn('_boosterAI/records/observation.md',paths)
        self.assertIn('_boosterAI/experiences/next.md',paths)
        search=self.cli('search-project-index','--query','LEGACY','--kind','partial')
        self.assertEqual(search.returncode,0,search.stdout+search.stderr)
        results=json.loads(search.stdout)
        self.assertEqual([entry['path'] for entry in results['entries']],['_boosterAI/_partials/legacy.py'])
        self.assertFalse(results['source_freshness_checked'])
        self.assertEqual(list(self.scratch.iterdir()),[])

    def test_unknown_or_custom_index_is_preserved(self):
        root_piece=self.add_file('_partials/existing.py','unchanged legacy code')
        nested_piece=self.add_file('_boosterAI/_partials/existing.py','unchanged standard code')
        protected={item:item.read_bytes() for item in (root_piece,nested_piece)}
        index=self.add_file('_boosterAI/project-index.json','{"custom_schema":"existing project data"}')
        original=index.read_bytes();result=self.cli('refresh-project-index')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('unrecognized project index',result.stdout)
        self.assertEqual(index.read_bytes(),original)
        for path,content in protected.items():self.assertEqual(path.read_bytes(),content)

    def test_legacy_root_file_blocks_index_refresh_without_overwriting(self):
        """A legacy root file is preserved while nested scaffold is created.

        A recognized pre-existing index must stay byte-for-byte intact when the
        legacy discovery root is a file instead of a directory. Exercise both
        real CLI entry points in the owned fixture; do not follow or create links.
        """
        index=self.add_file('_boosterAI/project-index.json',
                            json.dumps({'format':p.PROJECT_INDEX_FORMAT,'entries':[]})+'\n')
        legacy=self.add_file('_partials','existing user-owned root file\n')
        original_index=index.read_bytes();original_legacy=legacy.read_bytes()
        scaffold=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),
                                 'partials-init','--root',str(self.project)],capture_output=True,text=True)
        self.assertEqual(scaffold.returncode,0,scaffold.stdout+scaffold.stderr)
        self.assertEqual(json.loads(scaffold.stdout)['directory'],'_boosterAI/_partials')
        standard=self.project/'_boosterAI/_partials/README.md'
        self.assertTrue(standard.is_file());original_standard=standard.read_bytes()
        result=self.cli('refresh-project-index')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('project index source root must be a directory',result.stdout)
        self.assertEqual(legacy.read_bytes(),original_legacy)
        self.assertEqual(index.read_bytes(),original_index)
        self.assertEqual(standard.read_bytes(),original_standard)
        self.assertEqual(list(self.scratch.iterdir()),[])

    def test_existing_v1_and_both_partial_locations_are_preserved(self):
        """Index standard and legacy files independently without modifying sources.

        Build an existing v1 snapshot containing root partials and an experience,
        then add distinct same-named files in the standard nested directory. The
        real CLI must retain prior entries and content, index both paths and hashes,
        produce a stable second refresh, and search both without executing code.
        """
        legacy_readme=self.add_file('_partials/README.md','legacy partial notes\n')
        legacy_code=self.add_file('_partials/shared_a1b2c3d4.py',
                                  'raise RuntimeError("legacy content is inert")\n')
        experience=self.add_file('_boosterAI/experiences/local.md','existing experience\n')
        initial=self.cli('refresh-project-index')
        self.assertEqual(initial.returncode,0,initial.stdout+initial.stderr)
        index=self.root/'project-index.json'
        original=json.loads(index.read_bytes())
        self.assertEqual(original['format'],'boosterai-project-index-v1')
        original_entries={entry['path']:entry for entry in original['entries']}
        standard_readme=self.add_file('_boosterAI/_partials/README.md','standard partial notes\n')
        standard_code=self.add_file('_boosterAI/_partials/shared_a1b2c3d4.py',
                                    'raise RuntimeError("standard content is inert")\n')
        protected={path:path.read_bytes() for path in
                   (legacy_readme,legacy_code,experience,standard_readme,standard_code)}
        refreshed=self.cli('refresh-project-index')
        self.assertEqual(refreshed.returncode,0,refreshed.stdout+refreshed.stderr)
        self.assertEqual(json.loads(refreshed.stdout)['status'],'UPDATED')
        after=index.read_bytes();data=json.loads(after)
        self.assertEqual(data['format'],'boosterai-project-index-v1')
        entries={entry['path']:entry for entry in data['entries']}
        for path,entry in original_entries.items():self.assertEqual(entries[path],entry)
        for path,content in protected.items():
            relative=path.relative_to(self.project).as_posix()
            self.assertEqual(path.read_bytes(),content)
            self.assertEqual(entries[relative]['sha256'],hashlib.sha256(content).hexdigest())
            self.assertNotIn(content.strip(),after)
        search=self.cli('search-project-index','--query','shared_a1b2c3d4','--kind','partial')
        self.assertEqual(search.returncode,0,search.stdout+search.stderr)
        result=json.loads(search.stdout)
        self.assertEqual([entry['path'] for entry in result['entries']],
                         ['_boosterAI/_partials/shared_a1b2c3d4.py','_partials/shared_a1b2c3d4.py'])
        self.assertFalse(result['source_freshness_checked'])
        repeated=self.cli('refresh-project-index')
        self.assertEqual(repeated.returncode,0,repeated.stdout+repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)['status'],'UNCHANGED')
        self.assertEqual(index.read_bytes(),after)
        for path,content in protected.items():self.assertEqual(path.read_bytes(),content)
        self.assertEqual(list(self.scratch.iterdir()),[])

    def test_missing_previously_indexed_record_stops_refresh(self):
        source=self.add_file('_boosterAI/_partials/piece.py','preserve its index entry')
        p.refresh_project_index(self.project);index=self.root/'project-index.json';before=index.read_bytes()
        self.assertTrue(source.resolve().is_relative_to(self.project.resolve()))
        self.assertFalse(source.is_symlink());source.unlink()
        with self.assertRaisesRegex(ValueError,'refusing to drop'):
            p.refresh_project_index(self.project)
        self.assertEqual(index.read_bytes(),before)

    def test_source_changed_between_scans_does_not_create_index(self):
        source=self.add_file('_partials/piece.py','first version')
        real=p.project_index_entries;calls=[]
        def change_after_first_scan(*args):
            result=real(*args);calls.append(True)
            if len(calls)==1:source.write_text('a different version',encoding='utf-8')
            return result
        with patch.object(p,'project_index_entries',side_effect=change_after_first_scan):
            with self.assertRaisesRegex(ValueError,'sources changed'):
                p.refresh_project_index(self.project)
        self.assertFalse((self.root/'project-index.json').exists())

    def test_uncoordinated_index_change_detected_and_preserved(self):
        source=self.add_file('_partials/piece.py','first version')
        p.refresh_project_index(self.project);index=self.root/'project-index.json'
        source.write_text('a different version',encoding='utf-8')
        other=b'{"other_owner":"preserve this record"}\n';real=p.verify_archive_stage_file
        def interfere(*args):
            result=real(*args)
            if args[-1] is True:index.write_bytes(other)
            return result
        with patch.object(p,'verify_archive_stage_file',side_effect=interfere):
            with self.assertRaises(ValueError):p.refresh_project_index(self.project)
        self.assertEqual(index.read_bytes(),other)
        self.assertEqual(list(self.scratch.iterdir()),[])

    def test_index_change_during_unchanged_refresh_is_preserved(self):
        self.add_file('_partials/piece.py','unchanged source')
        p.refresh_project_index(self.project);index=self.root/'project-index.json'
        other=b'{"other_owner":"preserve this record"}\n';real=p.project_index_entries;calls=[]
        def change_index_after_second_scan(*args):
            result=real(*args);calls.append(True)
            if len(calls)==2:index.write_bytes(other)
            return result
        with patch.object(p,'project_index_entries',side_effect=change_index_after_second_scan):
            with self.assertRaises(ValueError):p.refresh_project_index(self.project)
        self.assertEqual(index.read_bytes(),other)

    def test_active_writer_lock_blocks_another_process(self):
        self.add_file('_partials/piece.py','ordinary content')
        with p.locked(self.root):
            result=self.cli('refresh-project-index')
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((self.root/'project-index.json').exists())
        self.assertEqual(list(self.scratch.iterdir()),[])

    def test_hardlinked_source_and_index_are_rejected(self):
        source=self.add_file('_partials/piece.py','protected source')
        alias=self.project/'source-alias'
        self.addCleanup(lambda: alias.unlink(missing_ok=True))
        try:alias.hardlink_to(source)
        except OSError as error:self.skipTest('hard links unavailable: '+str(error))
        with self.assertRaisesRegex(ValueError,'hard link'):
            p.refresh_project_index(self.project)
        self.assertEqual(source.read_text(encoding='utf-8'),'protected source')
        self.assertFalse((self.root/'project-index.json').exists())
        alias.unlink()
        p.refresh_project_index(self.project);index=self.root/'project-index.json';before=index.read_bytes()
        alias.hardlink_to(index)
        with self.assertRaisesRegex(ValueError,'hard link'):
            p.refresh_project_index(self.project)
        self.assertEqual(index.read_bytes(),before)

    @unittest.skipUnless(sys.platform=='win32','Windows junction test')
    def test_junction_in_partials_is_rejected_without_traversal(self):
        target=self.project/'retained';target.mkdir();marker=target/'keep.txt';marker.write_text('protected')
        partials=self.project/'_partials';partials.mkdir();junction=partials/'linked'
        def remove_owned_junction():
            if junction.exists():
                self.assertTrue(target.resolve().is_relative_to(self.project.resolve()))
                junction.rmdir()
        self.addCleanup(remove_owned_junction)
        created=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(target)],capture_output=True,text=True)
        self.assertEqual(created.returncode,0,created.stdout+created.stderr)
        with self.assertRaisesRegex(ValueError,'link/reparse'):
            p.refresh_project_index(self.project)
        self.assertEqual(marker.read_text(),'protected')
        self.assertFalse((self.root/'project-index.json').exists())

    def test_experiences_cannot_escape_or_impersonate_partials(self):
        self.add_file('_partials/piece.py','content')
        for path in ('../outside','_partials/piece.py','_boosterAI/../outside',
                     '_boosterAI/project-index.json','C:/outside'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):p.refresh_project_index(self.project,[path])
        self.assertFalse((self.root/'project-index.json').exists())

    @unittest.skipUnless(sys.platform=='win32','Windows case-insensitive path test')
    def test_case_variants_cannot_index_the_index_or_partials_as_experiences(self):
        self.add_file('_boosterAI/_partials/piece.py','local partial')
        p.refresh_project_index(self.project)
        index=self.root/'project-index.json';before=index.read_bytes()
        for path in ('_boosterAI/PROJECT-INDEX.JSON','_boosterAI/_PARTIALS/piece.py'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                p.refresh_project_index(self.project,[path])
        self.assertEqual(index.read_bytes(),before)

    def test_existing_file_identity_cannot_alias_the_index(self):
        self.add_file('_boosterAI/records/other.md','local experience')
        p.refresh_project_index(self.project)
        index=self.root/'project-index.json';before=index.read_bytes()
        with patch.object(p.os.path,'samefile',return_value=True):
            with self.assertRaisesRegex(ValueError,'cannot index itself'):
                p.refresh_project_index(self.project,['_boosterAI/records/other.md'])
        self.assertEqual(index.read_bytes(),before)


class ProjectIndexSearchRootTests(unittest.TestCase):
    def test_rejects_empty_blank_and_repeated_roots_before_search(self):
        cases = (
            ('--root', ''),
            ('--root=',),
            ('--root', ' \t '),
            ('--root', 'first', '--root', 'second'),
            ('--root', 'first', '--root=first'),
        )
        for options in cases:
            argv = ['project_tools.py', 'search-project-index', *options]
            stdout, stderr = StringIO(), StringIO()
            with self.subTest(options=options):
                with (
                    patch.object(sys, 'argv', argv),
                    patch.object(p, 'search_project_index', return_value={}) as search,
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as status,
                ):
                    sys.exit(p.main())
                search.assert_not_called()
                self.assertEqual(status.exception.code, 2)
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(
                    stderr.getvalue(), 'project index search requires exactly one nonblank --root\n'
                )

    def test_dispatches_one_root_with_query_and_kind(self):
        argv = ['project_tools.py', 'search-project-index', '--root', 'checkout path',
                '--query', 'example name', '--kind', 'partial']
        result = {'entries': [], 'source_freshness_checked': False}
        stdout, stderr = StringIO(), StringIO()
        with (
            patch.object(sys, 'argv', argv),
            patch.object(p, 'search_project_index', return_value=result) as search,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            self.assertEqual(p.main(), 0)
        search.assert_called_once_with('checkout path', 'example name', 'partial')
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertEqual(stderr.getvalue(), '')

    def test_rejects_conflicting_filters_before_search(self):
        cases = (
            ('--query', 'first', '--query', 'second'),
            ('--query', 'second', '--query', 'first'),
            ('--kind', 'partial', '--kind', 'experience'),
            ('--kind', 'experience', '--kind', 'partial'),
            ('--query=', '--query', 'first'),
            ('--query', 'first', '--query='),
        )
        for options in cases:
            argv = ['project_tools.py', 'search-project-index', '--root', 'checkout path', *options]
            stdout, stderr = StringIO(), StringIO()
            with self.subTest(options=options):
                with (
                    patch.object(sys, 'argv', argv),
                    patch.object(p, 'search_project_index', return_value={}) as search,
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as status,
                ):
                    sys.exit(p.main())
                search.assert_not_called()
                self.assertEqual(status.exception.code, 2)
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(stderr.getvalue(), 'conflicting project index filter values\n')

    def test_dispatches_omitted_and_identical_filters_once(self):
        query = '  Example Name  '
        cases = (
            ((), '', None),
            (('--query', '', '--query='), '', None),
            (('--query', query, '--query', query, '--kind', 'partial', '--kind=partial'),
             query, 'partial'),
        )
        result = {'entries': [], 'source_freshness_checked': False}
        for options, expected_query, expected_kind in cases:
            argv = ['project_tools.py', 'search-project-index', '--root', 'checkout path', *options]
            stdout, stderr = StringIO(), StringIO()
            with self.subTest(options=options):
                with (
                    patch.object(sys, 'argv', argv),
                    patch.object(p, 'search_project_index', return_value=result) as search,
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(p.main(), 0)
                search.assert_called_once_with('checkout path', expected_query, expected_kind)
                self.assertEqual(json.loads(stdout.getvalue()), result)
                self.assertEqual(stderr.getvalue(), '')


if __name__=='__main__':unittest.main(verbosity=2)
