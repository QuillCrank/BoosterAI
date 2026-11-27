import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from repo_temp import workspace

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('project_tools',ROOT/'scripts/project_tools.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class ProjectToolsTests(unittest.TestCase):
    def setUp(self):
        self.project=workspace(self)
        self.root=p.workspace(self.project)
        self.now=dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc)
    def approval_for(self,plan):
        return {'action':'delete-owned-expired-logs','plan_fingerprint':p.plan_fingerprint(plan),'confirmed':True,'reference':'isolated fixture approval simulation; verified equivalent copies retained','approved_at':self.now.isoformat()}
    def backup_equivalent(self,relative):
        source=self.root/relative;backup=self.root/('verified-copy-'+source.name)
        backup.write_bytes(source.read_bytes());self.assertEqual(p.sha(source),p.sha(backup))
        return backup
    def test_log_deletion_requires_exact_fresh_approval(self):
        p.log_event(self.project,'old owned work',now=self.now-dt.timedelta(days=7,seconds=1))
        p.log_event(self.project,'retained work',now=self.now-dt.timedelta(days=6))
        result=p.log_event(self.project,'today work',now=self.now)
        self.assertEqual(len(result['plan']['files']),1);self.assertEqual(result['deleted'],[])
        old=self.root/'logs/2026-11-07.jsonl';self.assertTrue(old.exists())
        self.assertFalse((self.root/'log-quarantine').exists())
        self.assertEqual(p.purge_logs(self.project,result['plan'],now=self.now)['status'],'NOT_DELETED_APPROVAL_REQUIRED')
        self.assertTrue(old.exists())
        backup=self.backup_equivalent('logs/2026-11-07.jsonl')
        removed=p.purge_logs(self.project,result['plan'],self.approval_for(result['plan']),now=self.now)
        self.assertEqual(removed['status'],'DELETED');self.assertFalse(old.exists());self.assertTrue(backup.exists())
        self.assertTrue((self.root/'logs/2026-11-08.jsonl').exists())
        self.assertTrue((self.root/'logs/2026-11-14.jsonl').exists())
    def test_deletion_changed_batch_and_stale_approval_refused(self):
        p.log_event(self.project,'old work',now=self.now-dt.timedelta(days=8))
        result=p.plan_logs(self.project,now=self.now);plan=result['plan']
        self.backup_equivalent('logs/2026-11-06.jsonl')
        approval=self.approval_for(plan);approval['approved_at']=(self.now-dt.timedelta(minutes=6)).isoformat()
        with self.assertRaises(ValueError):p.purge_logs(self.project,plan,approval,now=self.now)
        approval=self.approval_for(plan)
        old=self.root/'logs/2026-11-06.jsonl';old.write_text(old.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'changed'):p.purge_logs(self.project,plan,approval,now=self.now)
        self.assertTrue(old.exists())
        approval['plan_fingerprint']='wrong'
        with self.assertRaisesRegex(ValueError,'exact batch'):p.purge_logs(self.project,plan,approval,now=self.now)
    def test_approval_expires_after_preflight_before_first_unlink(self):
        p.log_event(self.project,'old fixture',now=self.now-dt.timedelta(days=8))
        plan=p.plan_logs(self.project,now=self.now)['plan'];self.backup_equivalent('logs/2026-11-06.jsonl')
        approval=self.approval_for(plan);path=self.root/'logs/2026-11-06.jsonl';before=path.read_bytes()
        with patch.object(p,'utc_now',side_effect=[self.now,self.now+dt.timedelta(seconds=301)]):
            result=p.purge_logs(self.project,plan,approval)
        self.assertEqual(result['status'],'NOT_DELETED');self.assertEqual(result['deleted'],[])
        self.assertIn('fresh',result['errors'][0]['reason']);self.assertEqual(path.read_bytes(),before)
    def test_approval_expiring_between_unlinks_stops_remaining_batch(self):
        p.log_event(self.project,'older fixture',now=self.now-dt.timedelta(days=9))
        p.log_event(self.project,'old fixture',now=self.now-dt.timedelta(days=8))
        plan=p.plan_logs(self.project,now=self.now)['plan']
        self.backup_equivalent('logs/2026-11-05.jsonl');self.backup_equivalent('logs/2026-11-06.jsonl')
        approval=self.approval_for(plan);second=self.root/'logs/2026-11-06.jsonl';before=second.read_bytes()
        clock=[self.now,self.now+dt.timedelta(seconds=30),self.now+dt.timedelta(seconds=301)]
        with patch.object(p,'utc_now',side_effect=clock):result=p.purge_logs(self.project,plan,approval)
        self.assertEqual(result['status'],'PARTIAL_FAILURE');self.assertEqual(result['deleted'],['logs/2026-11-05.jsonl'])
        self.assertIn('fresh',result['errors'][0]['reason']);self.assertEqual(second.read_bytes(),before)
    def test_unknown_old_log_preserved(self):
        logs=self.root/'logs';logs.mkdir();unknown=logs/'2026-01-01.jsonl';unknown.write_text('user data')
        result=p.log_event(self.project,'work',now=self.now)
        self.assertEqual(len(result['skipped']),1);self.assertEqual(unknown.read_text(),'user data')
    def test_verified_requires_evidence_and_secrets_rejected(self):
        with self.assertRaises(ValueError):p.log_event(self.project,'completed','verified',now=self.now)
        with self.assertRaises(ValueError):p.log_event(self.project,'password=do-not-log',now=self.now)
        (self.root/'proof.txt').write_text('observed output')
        result=p.log_event(self.project,'function output matched','verified','proof.txt',self.now)
        self.assertFalse(result['evidence_authenticated']);self.assertTrue(result['evidence_presence_checked'])
    def test_new_log_never_persists_caller_details_or_evidence(self):
        evidence=self.root/'evidence-for-archived-source.txt'
        evidence.write_text('private fixture contents')
        details='product feature input output and archive/asset.py'
        result=p.log_event(self.project,details,'verified',evidence.name,self.now)
        path=self.root/result['log'];raw=path.read_text(encoding='utf-8')
        row=json.loads(raw)
        self.assertEqual(row,{'format':'boosterai-log-v2','at':self.now.isoformat(),'status':'verified','summary':'verification completed'})
        for value in (details,evidence.name,evidence.read_text(),p.sha(evidence)):
            self.assertNotIn(value,raw)
        self.assertFalse(result['summary_recorded']);self.assertFalse(result['evidence_reference_recorded'])
        self.assertFalse(result['evidence_hash_recorded']);self.assertFalse(result['evidence_authenticated'])
        p.log_event(self.project,'different feature details','work',now=self.now)
        p.log_event(self.project,'another private detail','blocked',now=self.now)
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([row['summary'] for row in rows],['verification completed','work checkpoint','work blocked'])
    def test_legacy_v1_bytes_and_mixed_retention_are_preserved(self):
        old_time=self.now-dt.timedelta(days=8)
        logs=self.root/'logs';logs.mkdir()
        path=logs/(old_time.date().isoformat()+'.jsonl')
        legacy=json.dumps({'format':'boosterai-log-v1','at':old_time.isoformat(),'summary':'legacy text'},indent=0).encode('utf-8')
        # Existing JSONL records need not have a final newline. Appending v2 must
        # retain the legacy prefix byte-for-byte and insert only the delimiter.
        legacy=legacy.replace(b'\n',b'')
        path.write_bytes(legacy)
        p.log_event(self.project,'new product detail','work',now=old_time)
        raw=path.read_bytes();self.assertTrue(raw.startswith(legacy+b'\n'))
        self.assertEqual([json.loads(line)['format'] for line in raw.splitlines()],['boosterai-log-v1','boosterai-log-v2'])
        result=p.plan_logs(self.project,now=self.now)
        self.assertEqual(len(result['plan']['files']),1)
        self.assertEqual(p.purge_logs(self.project,result['plan'],now=self.now)['status'],'NOT_DELETED_APPROVAL_REQUIRED')
        self.assertEqual(path.read_bytes(),raw)
        alias=self.root/'hardlinked-legacy-log';alias.hardlink_to(path)
        linked=p.plan_logs(self.project,now=self.now)
        self.assertEqual(linked['plan']['files'],[])
        self.assertEqual(len(linked['skipped']),1)
        self.assertEqual(path.read_bytes(),raw)
    def test_mixed_log_with_recent_entry_is_not_eligible(self):
        old_time=self.now-dt.timedelta(days=8)
        logs=self.root/'logs';logs.mkdir()
        path=logs/(old_time.date().isoformat()+'.jsonl')
        entries=[{'format':'boosterai-log-v1','at':old_time.isoformat()},
                 {'format':'boosterai-log-v2','at':(self.now-dt.timedelta(days=7)).isoformat()}]
        content=''.join(json.dumps(row)+'\n' for row in entries);path.write_text(content,encoding='utf-8')
        result=p.plan_logs(self.project,now=self.now)
        self.assertEqual(result['plan']['files'],[]);self.assertEqual(len(result['skipped']),1)
        self.assertEqual(path.read_text(encoding='utf-8'),content)
    def test_legacy_cr_ending_gets_lf_separator(self):
        logs=self.root/'logs';logs.mkdir()
        path=logs/(self.now.date().isoformat()+'.jsonl')
        legacy=json.dumps({'format':'boosterai-log-v1','at':self.now.isoformat()}).encode('utf-8')+b'\r'
        path.write_bytes(legacy)
        p.log_event(self.project,'work','work',now=self.now)
        result=path.read_bytes();self.assertTrue(result.startswith(legacy+b'\n'))
        self.assertEqual([json.loads(line)['format'] for line in result.split(b'\n') if line],['boosterai-log-v1','boosterai-log-v2'])
    def test_existing_lock_and_hardlink_refused(self):
        (self.root/'.project-tools.lock').write_text('active owner')
        with self.assertRaises(FileExistsError):p.log_event(self.project,'work',now=self.now)
        self.assertEqual((self.root/'.project-tools.lock').read_text(),'active owner')
        (self.root/'.project-tools.lock').unlink()
        p.log_event(self.project,'old work',now=self.now-dt.timedelta(days=8))
        old=self.root/'logs/2026-11-06.jsonl';(self.root/'alias').hardlink_to(old)
        result=p.log_event(self.project,'work',now=self.now)
        self.assertTrue(old.exists());self.assertEqual(len(result['skipped']),1)
    def test_incomplete_and_hardlinked_legacy_guards_are_preserved(self):
        guard=self.root/'.project-tools.lock';guard.write_bytes(b'{"format":')
        with self.assertRaises(FileExistsError):p.log_event(self.project,'work',now=self.now)
        self.assertEqual(guard.read_bytes(),b'{"format":')
        other=self.project/'second-project';other.mkdir();other_root=p.workspace(other)
        marker=other_root/'owner-data';marker.write_bytes(b'preserve')
        other_guard=other_root/'.project-tools.lock';other_guard.hardlink_to(marker)
        with self.assertRaises(FileExistsError):p.log_event(other,'work',now=self.now)
        self.assertEqual(marker.read_bytes(),b'preserve');self.assertEqual(other_guard.read_bytes(),b'preserve')
    def test_scratch_selection_guard_reuse_and_conflict(self):
        first=self.project/'scratch-one';first.mkdir();second=self.project/'scratch-two';second.mkdir()
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(first)}):
            p.log_event(self.project,'work',now=self.now)
        guard=self.root/'.project-tools.lock';original=guard.read_bytes()
        self.assertEqual(json.loads(original),{'format':'boosterai-project-tools-guard-v1','scratch':'scratch-one'})
        self.assertEqual(list(first.iterdir()),[])
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(second)}):
            with self.assertRaisesRegex(ValueError,'scratch differs'):p.log_event(self.project,'work',now=self.now)
        self.assertEqual(list(second.iterdir()),[]);self.assertEqual(guard.read_bytes(),original)
        environment=dict(os.environ);environment.pop('BOOSTERAI_SCRATCH',None);environment['TEMP']=str(second)
        with patch.dict(os.environ,environment,clear=True):p.log_event(self.project,'work',now=self.now)
        self.assertEqual(guard.read_bytes(),original);self.assertEqual(list(first.iterdir()),[])
    def test_external_explicit_scratch_rejected_and_external_temp_uses_local_default(self):
        external=self.project.parent
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(external)}):
            with self.assertRaisesRegex(ValueError,'scratch must stay within project'):p.log_event(self.project,'work',now=self.now)
        self.assertFalse((self.root/'.project-tools.lock').exists())
        environment=dict(os.environ);environment.pop('BOOSTERAI_SCRATCH',None);environment['TEMP']=str(external)
        with patch.dict(os.environ,environment,clear=True):p.log_event(self.project,'work',now=self.now)
        guard=json.loads((self.root/'.project-tools.lock').read_text())
        self.assertEqual(guard['scratch'],'_boosterAI/.booster-tmp')
        self.assertEqual(list((self.root/'.booster-tmp').iterdir()),[])
    def test_sequential_disposable_process_temps_keep_stable_scratch(self):
        for explicit in (False,True):
            with self.subTest(explicit=explicit):
                project=self.project/('explicit-case' if explicit else 'default-case');project.mkdir()
                root=p.workspace(project)
                first=project/'operation-one';first.mkdir();second=project/'operation-two';second.mkdir()
                stable=project/'stable-scratch';stable.mkdir()
                env=dict(os.environ);env.pop('BOOSTERAI_SCRATCH',None)
                if explicit:env['BOOSTERAI_SCRATCH']=str(stable)
                env.update({name:str(first) for name in ('TEMP','TMP','TMPDIR')})
                with patch.dict(os.environ,env,clear=True):p.log_event(project,'work',now=self.now)
                guard=root/'.project-tools.lock';original=guard.read_bytes()
                expected='stable-scratch' if explicit else '_boosterAI/.booster-tmp'
                self.assertEqual(json.loads(original)['scratch'],expected)
                self.assertEqual(list(first.iterdir()),[]);self.assertTrue(first.resolve().is_relative_to(project.resolve()))
                self.assertFalse(getattr(first.lstat(),'st_file_attributes',0)&0x400);first.rmdir()
                env.update({name:str(second) for name in ('TEMP','TMP','TMPDIR')})
                with patch.dict(os.environ,env,clear=True):p.log_event(project,'work',now=self.now)
                self.assertEqual(guard.read_bytes(),original);self.assertEqual(list(second.iterdir()),[])
                self.assertTrue(second.resolve().is_relative_to(project.resolve()))
                self.assertFalse(getattr(second.lstat(),'st_file_attributes',0)&0x400);second.rmdir()
                actual=stable if explicit else root/'.booster-tmp'
                self.assertEqual(list(actual.iterdir()),[])
    def test_recognized_old_temp_guard_is_not_silently_rewritten(self):
        old=self.project/'former-temp';old.mkdir();guard=self.root/'.project-tools.lock'
        original=(json.dumps({'format':'boosterai-project-tools-guard-v1','scratch':'former-temp'},sort_keys=True,separators=(',',':'))+'\n').encode()
        guard.write_bytes(original)
        env=dict(os.environ);env.pop('BOOSTERAI_SCRATCH',None);env['TEMP']=str(self.project)
        with patch.dict(os.environ,env,clear=True):p.log_event(self.project,'work',now=self.now)
        self.assertEqual(guard.read_bytes(),original);self.assertEqual(list(old.iterdir()),[])
        self.assertTrue(old.resolve().is_relative_to(self.project.resolve()))
        self.assertFalse(getattr(old.lstat(),'st_file_attributes',0)&0x400);old.rmdir()
        with patch.dict(os.environ,env,clear=True):
            with self.assertRaisesRegex(ValueError,'scratch directory missing'):p.log_event(self.project,'work',now=self.now)
        self.assertEqual(guard.read_bytes(),original)
    def test_archive_staging_reuses_guarded_scratch_across_process_temps(self):
        stable=self.project/'stable-scratch';stable.mkdir()
        first=self.project/'operation-one';first.mkdir();second=self.project/'operation-two';second.mkdir()
        destination=self.project/'solutions'/'index.json';destination.parent.mkdir()
        env=dict(os.environ);env['BOOSTERAI_SCRATCH']=str(stable)
        observed=[];replace=os.replace
        def record_stage(source,target):
            source=Path(source);observed.append(source.parent)
            self.assertEqual(source.parent.parent.resolve(),stable.resolve())
            self.assertTrue(source.parent.name.startswith('.project-archive-op-'))
            replace(source,target)
        env.update({name:str(first) for name in ('TEMP','TMP','TMPDIR')})
        with patch.dict(os.environ,env,clear=True),patch.object(p.os,'replace',side_effect=record_stage):
            p.atomic_archive_json(self.project,destination,{'record':1})
        self.assertEqual(list(first.iterdir()),[]);self.assertTrue(first.resolve().is_relative_to(self.project.resolve()))
        self.assertFalse(getattr(first.lstat(),'st_file_attributes',0)&0x400);first.rmdir()
        env.update({name:str(second) for name in ('TEMP','TMP','TMPDIR')})
        with patch.dict(os.environ,env,clear=True),patch.object(p.os,'replace',side_effect=record_stage):
            p.atomic_archive_json(self.project,destination,{'record':2})
        self.assertEqual(list(second.iterdir()),[]);self.assertTrue(second.resolve().is_relative_to(self.project.resolve()))
        self.assertFalse(getattr(second.lstat(),'st_file_attributes',0)&0x400);second.rmdir()
        self.assertEqual(json.loads(destination.read_text(encoding='utf-8'))['record'],2)
        self.assertEqual(len(observed),2);self.assertNotEqual(observed[0],observed[1]);self.assertEqual(list(stable.iterdir()),[])
    def test_archive_staging_preserves_unexpected_same_inode_content(self):
        stable=self.project/'stable-scratch';stable.mkdir()
        destination=self.project/'solutions'/'index.json';destination.parent.mkdir();destination.write_bytes(b'original')
        def tamper(source,target):
            Path(source).write_bytes(b'other owner data')
            raise OSError('simulated replacement failure')
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(stable)}),patch.object(p.os,'replace',side_effect=tamper):
            with self.assertRaisesRegex(ValueError,'scratch file content changed'):
                p.atomic_archive_json(self.project,destination,{'record':1})
        operations=list(stable.iterdir());self.assertEqual(len(operations),1)
        self.assertEqual((operations[0]/'data.json').read_bytes(),b'other owner data')
        self.assertEqual(destination.read_bytes(),b'original')
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(stable)}):
            with self.assertRaisesRegex(ValueError,'unreconciled archive scratch'):
                p.atomic_archive_json(self.project,destination,{'record':2})
    def test_archive_staging_reports_retained_directory_when_identity_unavailable(self):
        stable=self.project/'stable-scratch';stable.mkdir()
        destination=self.project/'solutions'/'index.json';destination.parent.mkdir();destination.write_bytes(b'original')
        original_lstat=Path.lstat
        def fail_new_operation(path):
            if path.name.startswith('.project-archive-op-') and path.exists():
                raise PermissionError('fixture metadata read denied')
            return original_lstat(path)
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(stable)}),patch.object(Path,'lstat',autospec=True,side_effect=fail_new_operation):
            with self.assertRaises(PermissionError) as raised:p.atomic_archive_json(self.project,destination,{'record':1})
        retained=list(stable.iterdir());self.assertEqual(len(retained),1)
        self.assertIn(str(retained[0]),str(raised.exception));self.assertIn('fixture metadata read denied',str(raised.exception))
        self.assertTrue(retained[0].is_dir());self.assertEqual(destination.read_bytes(),b'original')
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(stable)}):
            with self.assertRaisesRegex(ValueError,'unreconciled archive scratch'):
                p.atomic_archive_json(self.project,destination,{'record':2})
    def test_replaced_owned_lock_is_preserved(self):
        scratch=self.project/'scratch';scratch.mkdir()
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(scratch)}):
            with self.assertRaisesRegex(ValueError,'lock identity changed'):
                with p.locked(self.root):
                    lock=next(scratch.glob('.project-tools-*.lock'))
                    lock.rename(scratch/'original-owned-lock')
                    lock.write_bytes(b'other owner')
            self.assertEqual(lock.read_bytes(),b'other owner')
            self.assertTrue((scratch/'original-owned-lock').is_file())
            with self.assertRaises(FileExistsError):p.log_event(self.project,'work',now=self.now)
            self.assertEqual(lock.read_bytes(),b'other owner')
    def test_owned_lock_replaced_by_hardlink_is_preserved(self):
        scratch=self.project/'scratch';scratch.mkdir();marker=scratch/'owner-data';marker.write_bytes(b'preserve')
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(scratch)}):
            with self.assertRaisesRegex(ValueError,'lock link/reparse forbidden'):
                with p.locked(self.root):
                    lock=next(scratch.glob('.project-tools-*.lock'))
                    lock.rename(scratch/'original-owned-lock')
                    lock.hardlink_to(marker)
            self.assertEqual(lock.read_bytes(),b'preserve');self.assertEqual(marker.read_bytes(),b'preserve')
    def test_owned_lock_with_changed_same_inode_content_is_preserved(self):
        scratch=self.project/'scratch';scratch.mkdir()
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(scratch)}):
            with self.assertRaisesRegex(ValueError,'lock content changed'):
                with p.locked(self.root):
                    lock=next(scratch.glob('.project-tools-*.lock'))
                    before=lock.stat();lock.write_bytes(b'other writer contents');after=lock.stat()
                    self.assertEqual((before.st_dev,before.st_ino),(after.st_dev,after.st_ino))
            self.assertEqual(lock.read_bytes(),b'other writer contents')
            with self.assertRaises(FileExistsError):p.log_event(self.project,'work',now=self.now)
            self.assertEqual(lock.read_bytes(),b'other writer contents')
    def test_guard_access_denial_is_not_reclassified(self):
        guard=self.root/'.project-tools.lock'
        value={'format':'boosterai-project-tools-guard-v1','scratch':'.'}
        guard.write_bytes((json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode())
        with patch.object(Path,'open',side_effect=PermissionError('fixture access denied')):
            with self.assertRaisesRegex(PermissionError,'fixture access denied'):p.read_project_tools_guard(self.root)
        self.assertTrue(guard.exists())
    def test_two_process_lock_and_legacy_exclusion(self):
        scratch=self.project/'scratch';scratch.mkdir()
        env=dict(os.environ);env['BOOSTERAI_SCRATCH']=str(scratch)
        source=('import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n'
                'import project_tools as p\nwith p.locked(Path(sys.argv[2])):\n'
                ' print("ready",flush=True)\n sys.stdin.readline()\n')
        child=subprocess.Popen([sys.executable,'-I','-S','-B','-u','-c',source,str(ROOT/'scripts'),str(self.root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        try:
            ready=child.stdout.readline().strip();self.assertEqual(ready,'ready')
            with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(scratch)}):
                with self.assertRaises(FileExistsError):p.log_event(self.project,'work',now=self.now)
            guard=self.root/'.project-tools.lock';before=guard.read_bytes()
            old_code='import os,sys; fd=os.open(sys.argv[1],os.O_CREAT|os.O_EXCL|os.O_WRONLY); os.close(fd)'
            old=subprocess.run([sys.executable,'-I','-S','-B','-c',old_code,str(guard)],capture_output=True,text=True,env=env)
            self.assertNotEqual(old.returncode,0);self.assertIn('FileExistsError',old.stderr)
            self.assertEqual(guard.read_bytes(),before)
        finally:
            output,error=child.communicate('release\n',timeout=15)
        self.assertEqual(child.returncode,0,output+error)
        self.assertEqual(list(scratch.iterdir()),[])
        with patch.dict(os.environ,{'BOOSTERAI_SCRATCH':str(scratch)}):p.log_event(self.project,'work',now=self.now)
        self.assertEqual(list(scratch.iterdir()),[])
    @unittest.skipUnless(sys.platform=='win32','Windows junction test')
    def test_safe_refuses_broken_junction_without_following_it(self):
        target=self.project/'target';target.mkdir();junction=self.project/'broken-junction'
        made=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(target)],capture_output=True,text=True)
        self.assertEqual(made.returncode,0,made.stdout+made.stderr)
        def cleanup_junction():
            if not target.exists():target.mkdir()
            info=junction.lstat();self.assertTrue(info.st_file_attributes&0x400)
            junction.rmdir()
        self.addCleanup(cleanup_junction)
        target.rmdir()
        with self.assertRaisesRegex(ValueError,'link/reparse'):p.safe(self.project.resolve(),'broken-junction/anything')
        self.assertTrue(junction.lstat().st_file_attributes&0x400)
    def test_versioned_helper_reuse_actual_python(self):
        source=self.root/'normalize.py';source.write_text('def normalize(text):\n    """Return the input with outer spaces removed and letters lowercased."""\n    return text.strip().lower()\n')
        probe='import runpy; f=runpy.run_path('+repr(str(source))+')["normalize"]; assert f(" ALICE ")=="alice"; print("alice")'
        initial=subprocess.run([sys.executable,'-I','-S','-B','-c',probe],capture_output=True,text=True)
        self.assertEqual(initial.returncode,0,initial.stderr)
        proof=self.root/'proof.txt';proof.write_text(initial.stdout)
        result=p.register_helper(self.project,'normalize','1.0.0','normalize.py','proof.txt','Python CLI')
        path=self.root/result['path']
        self.assertRegex(path.name,r'^normalize_[0-9a-f]{8}\.py$')
        self.assertEqual(result['metadata']['source_scope'],'workspace')
        self.assertEqual(result['metadata']['documentation_check'],'python-ast')
        probe='import runpy; f=runpy.run_path('+repr(str(path))+')["normalize"]; assert f(" ALICE ")=="alice"; print(f(" BOB "))'
        actual=subprocess.run([sys.executable,'-I','-S','-B','-c',probe],capture_output=True,text=True)
        self.assertEqual(actual.returncode,0,actual.stderr);self.assertEqual(actual.stdout.strip(),'bob')
        other=self.project/'second-project';other.mkdir();other_root=p.workspace(other)
        (other_root/'normalize.py').write_bytes(source.read_bytes());(other_root/'proof.txt').write_bytes(proof.read_bytes())
        other_result=p.register_helper(other,'normalize','1.0.0','normalize.py','proof.txt','Python CLI')
        self.assertEqual(Path(other_result['path']).name,path.name)
        again=p.register_helper(self.project,'normalize','1.0.0','normalize.py','proof.txt','Python CLI')
        self.assertFalse(again['created'])
        source.write_text('def normalize(text):\n    return text\n')
        with self.assertRaisesRegex(ValueError,'version conflict'):p.register_helper(self.project,'normalize','1.0.0','normalize.py','proof.txt','Python CLI')
        self.assertEqual(p.helper_check(self.project,'normalize','1.0.0')['path'],result['path'])
        proof.write_text('changed evidence')
        with self.assertRaisesRegex(ValueError,'evidence'):p.helper_check(self.project,'normalize','1.0.0')
    def test_project_scope_helper_real_cli_and_reuse(self):
        partials=self.project/'_partials';partials.mkdir()
        source=partials/'double_1234abcd.py'
        source.write_text('def double(value):\n    """Return twice the supplied number without mutating the input."""\n    return value*2\n')
        candidate_probe='import runpy; print(runpy.run_path('+repr(str(source))+')["double"](4))'
        candidate=subprocess.run([sys.executable,'-I','-S','-B','-c',candidate_probe],capture_output=True,text=True)
        self.assertEqual(candidate.returncode,0,candidate.stderr);self.assertEqual(candidate.stdout.strip(),'8')
        proof=self.root/'proof.txt';proof.write_text(candidate.stdout)
        command=[sys.executable,'-I','-S','-B',str(ROOT/'scripts/project_tools.py'),'register-helper','--root',str(self.project),'--name','double-number','--version','1.0.0','--source','_partials/double_1234abcd.py','--source-scope','project','--evidence','proof.txt','--target','Python CLI']
        first=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        record=json.loads(first.stdout);self.assertTrue(record['created'])
        self.assertEqual(record['metadata']['source_scope'],'project')
        stored=self.root/record['path'];self.assertRegex(stored.name,r'^double_number_[0-9a-f]{8}\.py$')
        probe='import runpy; assert runpy.run_path('+repr(str(stored))+')["double"](4)==8'
        executed=subprocess.run([sys.executable,'-I','-S','-B','-c',probe],capture_output=True,text=True)
        self.assertEqual(executed.returncode,0,executed.stderr)
        second=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(second.returncode,0,second.stdout+second.stderr);self.assertFalse(json.loads(second.stdout)['created'])
        checked=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/project_tools.py'),'check-helper','--root',str(self.project),'--name','double-number','--version','1.0.0'],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
        self.assertEqual(json.loads(checked.stdout)['path'],record['path'])
        with self.assertRaisesRegex(ValueError,'source and real-test evidence'):
            p.register_helper(self.project,'other','1.0.0','_partials/double_1234abcd.py','proof.txt','Python CLI')
        with self.assertRaisesRegex(ValueError,'unsafe relative path'):
            p.register_helper(self.project,'other','1.0.0','../outside.py','proof.txt','Python CLI',source_scope='project')
    def test_standard_partial_registered_and_executed_by_consumer(self):
        """Exercise the nested standard, real helper CLI and a consuming process.

        First verify the project function against a real input file, then use
        that observation as registration evidence. A separate project-owned
        entry point invokes the registered copy; source, evidence, code and
        metadata remain unchanged when the helper is checked and reused.
        """
        project_record=self.project/'PROJECT_RECORDS.md';project_record.write_bytes(b'Existing project record\n')
        record_state=(project_record.read_bytes(),project_record.stat().st_ino,project_record.stat().st_mtime_ns)
        scaffold=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/booster.py'),
                                 'partials-init','--root',str(self.project)],capture_output=True,text=True)
        self.assertEqual(scaffold.returncode,0,scaffold.stdout+scaffold.stderr)
        self.assertEqual(json.loads(scaffold.stdout)['directory'],'_boosterAI/_partials')
        self.assertEqual((project_record.read_bytes(),project_record.stat().st_ino,project_record.stat().st_mtime_ns),record_state)
        partials=self.root/'_partials'
        source=partials/'count_lines_c7a4e1b2.py'
        source.write_text('from pathlib import Path\n'
                          'def count_content_lines(path):\n'
                          '    """Count nonempty UTF-8 lines in a caller-supplied file.\n\n'
                          '    Read the file without modifying it and return an integer count.\n'
                          '    Filesystem and decoding errors propagate to the caller.\n'
                          '    """\n'
                          '    return sum(bool(line.strip()) for line in Path(path).read_text(encoding="utf-8").splitlines())\n',
                          encoding='utf-8')
        input_file=self.project/'notes.txt';input_file.write_text('first\n\nsecond\n',encoding='utf-8')
        probe='import runpy,sys; f=runpy.run_path(sys.argv[1])["count_content_lines"]; print(f(sys.argv[2]))'
        observed=subprocess.run([sys.executable,'-I','-S','-B','-c',probe,str(source),str(input_file)],
                                capture_output=True,text=True)
        self.assertEqual(observed.returncode,0,observed.stderr);self.assertEqual(observed.stdout.strip(),'2')
        evidence=self.root/'evidence'/'count-lines.txt';evidence.parent.mkdir();evidence.write_text(observed.stdout,encoding='utf-8')
        original={path:(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns)
                  for path in (source,input_file,evidence)}
        original[project_record]=record_state
        register=[sys.executable,'-I','-S','-B',str(ROOT/'scripts/project_tools.py'),'register-helper',
                  '--root',str(self.project),'--name','count-lines','--version','1.0.0',
                  '--source','_boosterAI/_partials/count_lines_c7a4e1b2.py','--source-scope','project',
                  '--evidence','evidence/count-lines.txt','--target','Python CLI']
        first=subprocess.run(register,capture_output=True,text=True)
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        registered=json.loads(first.stdout);self.assertTrue(registered['created'])
        self.assertEqual(registered['metadata']['source_scope'],'project')
        self.assertEqual(registered['metadata']['documentation_check'],'python-ast')
        self.assertFalse(registered['metadata']['evidence_authenticated'])
        stored=self.root/registered['path']
        self.assertRegex(stored.name,r'^count_lines_[0-9a-f]{8}\.py$')
        self.assertEqual(stored.read_bytes(),source.read_bytes())
        metadata=stored.parent/'metadata.json'
        for path,state in original.items():
            self.assertEqual((path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns),state)
        protected={**original,**{path:(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns)
                                 for path in (stored,metadata)}}
        checked=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/project_tools.py'),
                                'check-helper','--root',str(self.project),'--name','count-lines',
                                '--version','1.0.0'],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
        check_report=json.loads(checked.stdout)
        self.assertEqual(check_report['path'],registered['path'])
        self.assertTrue(check_report['actual_runtime_retest_required_when_target_changes'])
        app=self.project/'app.py'
        app.write_text('import runpy,sys\nfrom pathlib import Path\n'
                       'helper=Path(__file__).parent / "_boosterAI" / '+repr(registered['path'])+'\n'
                       'count=runpy.run_path(str(helper))["count_content_lines"]\n'
                       'print("nonempty_lines="+str(count(sys.argv[1])))\n',encoding='utf-8')
        consumer=subprocess.run([sys.executable,'-I','-S','-B',str(app),str(input_file)],
                                capture_output=True,text=True)
        self.assertEqual(consumer.returncode,0,consumer.stderr)
        self.assertEqual(consumer.stdout.strip(),'nonempty_lines=2')
        reused=subprocess.run(register,capture_output=True,text=True)
        self.assertEqual(reused.returncode,0,reused.stdout+reused.stderr)
        self.assertFalse(json.loads(reused.stdout)['created'])
        self.assertEqual(json.loads(reused.stdout)['path'],registered['path'])
        for path,original in protected.items():
            self.assertEqual((path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns),original)
        self.assertFalse((self.project/'_partials').exists())
    def test_existing_legacy_helper_reused_without_rewriting_or_new_doc_gate(self):
        source=self.root/'legacy-source.py';source.write_text('def old(x):\n    return x+1\n')
        proof=self.root/'proof.txt';proof.write_text('legacy proof')
        directory=self.root/'helpers/legacy/1.0.0';directory.mkdir(parents=True)
        code=directory/'code.py';code.write_bytes(source.read_bytes())
        legacy={'format':'boosterai-helper-v1','name':'legacy','version':'1.0.0','source_origin':'legacy-source.py','code':'helpers/legacy/1.0.0/code.py','code_sha256':p.sha(code),'evidence':'proof.txt','evidence_sha256':p.sha(proof),'tested_target':'Python CLI'}
        metadata=directory/'metadata.json';metadata.write_text(json.dumps(legacy),encoding='utf-8')
        old_metadata=metadata.read_bytes();old_code=code.read_bytes()
        self.assertEqual(p.helper_check(self.project,'legacy','1.0.0')['path'],legacy['code'])
        reused=p.register_helper(self.project,'legacy','1.0.0','legacy-source.py','proof.txt','Python CLI')
        self.assertFalse(reused['created']);self.assertEqual(reused['path'],legacy['code'])
        self.assertEqual(metadata.read_bytes(),old_metadata);self.assertEqual(code.read_bytes(),old_code)
        probe='import runpy; assert runpy.run_path('+repr(str(code))+')["old"](2)==3'
        used=subprocess.run([sys.executable,'-I','-S','-B','-c',probe],capture_output=True,text=True)
        self.assertEqual(used.returncode,0,used.stderr)
        with self.assertRaisesRegex(ValueError,'function documentation missing'):
            p.register_helper(self.project,'legacy','1.0.1','legacy-source.py','proof.txt','Python CLI')
        self.assertFalse((self.root/'helpers/legacy/1.0.1').exists())
    def test_nonpython_new_helper_needs_explicit_documentation_review(self):
        source=self.root/'utility.js';source.write_text('// Return the input unchanged without modifying the caller value.\nfunction identity(x) { return x; }\n')
        (self.root/'proof.txt').write_text('synthetic fixture proof; no JavaScript runtime claim')
        with self.assertRaisesRegex(ValueError,'documentation review required'):
            p.register_helper(self.project,'utility','1.0.0','utility.js','proof.txt','JS target')
        self.assertFalse((self.root/'helpers/utility/1.0.0').exists())
        result=p.register_helper(self.project,'utility','1.0.0','utility.js','proof.txt','JS target',function_docs_reviewed=True)
        self.assertEqual(result['metadata']['documentation_check'],'manual-confirmed')
    def test_helper_suffix_collision_is_rejected_before_new_record(self):
        source=self.root/'candidate.py';source.write_text('def f(x):\n    """Return the supplied input unchanged for this example."""\n    return x\n')
        (self.root/'proof.txt').write_text('fixture proof')
        first=p.register_helper(self.project,'alpha','1.0.0','candidate.py','proof.txt','Python CLI')
        suffix=Path(first['path']).stem.rsplit('_',1)[-1]
        with patch.object(p.profile,'code_filename',return_value='beta-'+suffix+'.py'):
            with self.assertRaisesRegex(ValueError,'suffix collision'):
                p.register_helper(self.project,'beta','1.0.0','candidate.py','proof.txt','Python CLI')
        self.assertFalse((self.root/'helpers/beta/1.0.0').exists())
        self.assertTrue((self.root/first['path']).is_file())
    def test_cli_log_real_process(self):
        evidence=self.root/'private-evidence-name.txt';evidence.write_text('private evidence')
        details='sensitive product and archived example details'
        result=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/project_tools.py'),'log','--root',str(self.project),'--summary',details,'--status','verified','--evidence',evidence.name],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        observed=json.loads(result.stdout);path=self.root/observed['log'];self.assertTrue(path.is_file())
        content=path.read_text(encoding='utf-8')
        self.assertEqual(json.loads(content)['summary'],'verification completed')
        self.assertNotIn(details,content);self.assertNotIn(evidence.name,content);self.assertNotIn(p.sha(evidence),content)
        self.assertFalse(observed['summary_recorded']);self.assertFalse(observed['evidence_reference_recorded'])
        self.assertTrue(observed['evidence_presence_checked']);self.assertFalse(observed['evidence_authenticated'])
    def test_generalized_archive_real_code_and_dedupe(self):
        candidates=self.project/'.solution-candidates';candidates.mkdir()
        code=candidates/'example.py';code.write_text('def double(value):\n    """Return twice the input for a deterministic numeric fixture."""\n    return value * 2\n')
        probe='import runpy; f=runpy.run_path('+repr(str(code))+')["double"]; assert f(3)==6; print("6")'
        observed=subprocess.run([sys.executable,'-I','-S','-B','-c',probe],capture_output=True,text=True)
        self.assertEqual(observed.returncode,0,observed.stderr)
        evidence=candidates/'proof.txt';evidence.write_text(observed.stdout)
        review={'name':'double-number','title':'Double an integer without side effects','license':'MIT','code_sha256':p.sha(code),'evidence':'.solution-candidates/proof.txt','evidence_sha256':p.sha(evidence),'outcome':'success','test_summary':'Python assertion returned 6','categories':['numeric'],'tags':['integer','stdlib']}
        for key in ('rights_confirmed','privacy_reviewed','generalized','security_reviewed','real_test_verified'):review[key]=True
        manifest=candidates/'review.json';manifest.write_text(json.dumps(review))
        ready=p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')
        self.assertEqual(ready['status'],'READY_FOR_AUTHORIZED_PUBLICATION');self.assertFalse(ready['published'])
        first=p.archive_search(self.project,'double')['results'][0]
        self.assertEqual((self.project/first['code']).read_text(),code.read_text())
        self.assertEqual(first['evidence_basis'],'artifact-sha256')
        self.assertRegex(Path(first['code']).name,r'^double-number-[0-9a-f]{8}\.py$')
        current=self.project/'solutions/numeric/double-number/metadata.json'
        original_metadata=current.read_bytes()
        original_index=(self.project/'solutions/index.json').read_bytes()
        historical=json.loads(original_metadata);historical['legacy_private_detail']='fixture-only'
        current.write_text(json.dumps(historical),encoding='utf-8');p.rebuild_index(self.project)
        protected_index=(self.project/'solutions/index.json').read_bytes()
        blocked=p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')
        self.assertEqual(blocked['status'],'LOCAL_ONLY')
        self.assertIn('unreviewed_metadata_requires_privacy_review',blocked['reasons'])
        self.assertEqual((self.project/'solutions/index.json').read_bytes(),protected_index)
        self.assertEqual(json.loads(current.read_text(encoding='utf-8'))['legacy_private_detail'],'fixture-only')
        current.write_bytes(original_metadata);(self.project/'solutions/index.json').write_bytes(original_index)
        historical.pop('legacy_private_detail');historical['unrecognized_note']='fixture-only'
        current.write_text(json.dumps(historical),encoding='utf-8');p.rebuild_index(self.project)
        second=p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')
        self.assertEqual(second['status'],'LOCAL_ONLY')
        self.assertIn('unreviewed_metadata_requires_privacy_review',second['reasons'])
        current.write_bytes(original_metadata);(self.project/'solutions/index.json').write_bytes(original_index)
        p.rebuild_index(self.project)
        before=(self.project/'solutions/index.json').read_bytes()
        archived=self.project/first['code'];original=archived.read_bytes();archived.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'code missing or changed'):
            p.rebuild_index(self.project)
        self.assertEqual((self.project/'solutions/index.json').read_bytes(),before)
        archived.write_bytes(original)
        with patch.object(p.profile,'code_filename',return_value=Path(first['code']).name):
            with self.assertRaisesRegex(ValueError,'suffix collision'):
                p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')
        self.assertEqual((self.project/'solutions/index.json').read_bytes(),before)
        review['private_terms']=['double'];manifest.write_text(json.dumps(review))
        self.assertIn('possible_private_data',p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')['reasons'])
        del review['private_terms']
        review['description']='password=fixture-not-a-secret';manifest.write_text(json.dumps(review))
        self.assertIn('possible_private_data',p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')['reasons'])
        del review['description']
        review['categories']=['transforms'];review['tags']=['reusable'];manifest.write_text(json.dumps(review))
        self.assertEqual(p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')['status'],'UPDATED_EXISTING')
        cli=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/project_tools.py'),'search-archive','--root',str(self.project),'--category','transforms','--tag','reusable'],capture_output=True,text=True)
        self.assertEqual(cli.returncode,0,cli.stdout+cli.stderr)
        self.assertEqual(len(json.loads(cli.stdout)['results']),1)
        self.assertTrue((self.project/first['code']).is_file())
        search=p.archive_search(self.project,'double',category='numeric',tag='stdlib')
        self.assertEqual(len(search['results']),1);self.assertEqual(search['results'][0]['revision'],2)
        self.assertTrue((self.project/'solutions/index.json').is_file())
        index=(self.project/'solutions/index.json').read_bytes();p.rebuild_index(self.project)
        self.assertEqual((self.project/'solutions/index.json').read_bytes(),index)
        self.assertFalse((self.root/'logs').exists())
        review['outcome']='failed';manifest.write_text(json.dumps(review))
        self.assertEqual(p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')['status'],'LOCAL_ONLY')
        review['outcome']='success';review['rights_confirmed']=False;manifest.write_text(json.dumps(review))
        self.assertEqual(p.archive_example(self.project,'.solution-candidates/example.py','.solution-candidates/review.json')['status'],'LOCAL_ONLY')
    def test_archive_private_or_unreviewed_stays_local(self):
        code=self.project/'candidate.py';code.write_text('password="fixture-not-a-secret"\n')
        review=self.project/'review.json';review.write_text('{}')
        result=p.archive_example(self.project,'candidate.py','review.json')
        self.assertEqual(result['status'],'LOCAL_ONLY');self.assertIn('possible_private_data',result['reasons'])
        self.assertFalse((self.project/'solutions').exists())
    def test_archive_rejects_undocumented_functions(self):
        code=self.project/'candidate.py';code.write_text('def f(x):\n    return x\n')
        proof=self.project/'proof.txt';proof.write_text('verified fixture')
        review={'name':'small-function','title':'A small function','license':'MIT','code_sha256':p.sha(code),'evidence':'proof.txt','evidence_sha256':p.sha(proof),'outcome':'success','test_summary':'Real fixture output checked','categories':['general'],'tags':[]}
        for key in ('rights_confirmed','privacy_reviewed','generalized','security_reviewed','real_test_verified'):review[key]=True
        (self.project/'review.json').write_text(json.dumps(review))
        result=p.archive_example(self.project,'candidate.py','review.json')
        self.assertEqual(result['status'],'LOCAL_ONLY');self.assertIn('function_documentation_missing',result['reasons'])
    @unittest.skipUnless(sys.platform=='win32','Windows junction test')
    def test_archive_rebuild_rejects_junctions(self):
        archive=self.project/'solutions';archive.mkdir()
        target=self.project/'retained';target.mkdir();marker=target/'keep.txt';marker.write_text('preserve')
        junction=archive/'linked'
        made=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(target)],capture_output=True,text=True)
        self.assertEqual(made.returncode,0,made.stdout+made.stderr);self.addCleanup(junction.rmdir)
        with self.assertRaisesRegex(ValueError,'link/reparse'):
            p.current_archive_records(self.project)
        self.assertEqual(marker.read_text(),'preserve')
    def test_invalid_log_date_does_not_lose_today(self):
        logs=self.root/'logs';logs.mkdir()
        bad=logs/'2026-99-99.jsonl';bad.write_text('unrelated content')
        result=p.log_event(self.project,'today observation preserved',now=self.now)
        self.assertEqual(bad.read_text(),'unrelated content')
        self.assertEqual(result['skipped'][0]['file'],'2026-99-99.jsonl')
        self.assertEqual(json.loads((self.root/result['log']).read_text())['summary'],'work checkpoint')
    def test_exact_seven_day_boundary_preserved(self):
        p.log_event(self.project,'exact boundary',now=self.now-dt.timedelta(days=7))
        plan=p.plan_logs(self.project,now=self.now)
        self.assertEqual(plan['plan']['files'],[])
        self.assertTrue((self.root/'logs/2026-11-07.jsonl').exists())
    def test_cli_no_approval_never_deletes(self):
        old_time=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=8)
        result=p.log_event(self.project,'old CLI fixture',now=old_time)
        batch=p.plan_logs(self.project)
        (self.root/'deletion-plan.json').write_text(json.dumps(batch['plan']))
        cli=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/project_tools.py'),'purge-logs','--root',str(self.project),'--plan','deletion-plan.json'],capture_output=True,text=True)
        self.assertEqual(cli.returncode,0,cli.stdout+cli.stderr)
        self.assertEqual(json.loads(cli.stdout)['status'],'NOT_DELETED_APPROVAL_REQUIRED')
        self.assertTrue((self.root/result['log']).exists())
        with self.assertRaises(ValueError):p.purge_logs(self.project,batch['plan'],{'confirmed':True})
    def test_utc_boundary(self):
        now=dt.datetime(2026,11,15,1,tzinfo=dt.timezone(dt.timedelta(hours=3)))
        self.assertEqual(p.log_event(self.project,'work',now=now)['log'],'logs/2026-11-14.jsonl')

if __name__=='__main__':unittest.main(verbosity=2)
