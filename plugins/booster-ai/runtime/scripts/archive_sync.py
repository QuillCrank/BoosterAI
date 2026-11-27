"""Pinned public archive synchronization. Downloaded source is inert, untrusted data."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive_profile as profile

REPOSITORY='https://github.com/QuillCrank/BoosterAI'
LOCAL_REPOSITORIES=(REPOSITORY,)
COMMIT_URL='https://api.github.com/repos/QuillCrank/BoosterAI/commits/main'
RAW='https://raw.githubusercontent.com/QuillCrank/BoosterAI/'
MAX_INDEX=profile.MAX_INDEX_BYTES
MAX_METADATA=MAX_INDEX
MAX_CODE=profile.MAX_CODE_BYTES
MAX_TOTAL=profile.MAX_TOTAL_BYTES


def require(ok,message):
    if not ok:raise ValueError(message)


def digest(data):return hashlib.sha256(data).hexdigest()


def safe(root,relative):
    require(isinstance(relative,str) and relative and ':' not in relative,'invalid local path')
    raw=Path(relative);require(not raw.is_absolute() and '..' not in raw.parts,'outside local scope')
    path=root
    for part in raw.parts:
        path/=part
        if path.exists() or path.is_symlink():
            info=path.lstat();require(not stat.S_ISLNK(info.st_mode) and not (getattr(info,'st_file_attributes',0)&0x400),'link/reparse forbidden')
            if path.is_file():require(info.st_nlink==1,'hardlink forbidden')
    require(path.resolve().is_relative_to(root),'outside local scope')
    return path


def storage(repository, *, create=True):
    """Resolve safe local storage, creating it for writers by default."""
    root=Path(repository).absolute();require(root.is_dir(),'local repository missing')
    for ancestor in (root,*root.parents):
        info=ancestor.lstat();require(not stat.S_ISLNK(info.st_mode) and not (getattr(info,'st_file_attributes',0)&0x400),'root link/reparse forbidden')
    root=root.resolve();work=safe(root,'_boosterAI/archive-sync')
    if create:
        work.mkdir(parents=True,exist_ok=True)
    return work


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect rejected')


def remote_get(url,limit):
    require(url==COMMIT_URL or re.fullmatch(re.escape(RAW)+r'[0-9a-f]{40}/solutions/[A-Za-z0-9_./-]+',url),'unpinned/unapproved URL')
    request=urllib.request.Request(url,headers={'User-Agent':'BoosterAI-read-only-archive-sync','Accept':'application/json'})
    # No tokens, cookies, user data, local paths, credentials or account headers sent.
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
        require(response.status==200,'remote status not 200')
        content=response.read(limit+1);require(len(content)<=limit,'remote file exceeds size limit')
        return content


def unique_manifest_object(pairs):
    """Reject duplicate index names at every object depth without echoing input.

    JSON-decoded names, including escaped aliases, are compared before entries
    are used. The fixed error is safe for the CLI's normal failure reporting.
    """
    result = {}
    for name, value in pairs:
        require(name not in result, 'duplicate archive manifest field')
        result[name] = value
    return result


def validate_manifest(raw):
    require(len(raw) <= MAX_INDEX, 'index too large')
    data = json.loads(raw, object_pairs_hook=unique_manifest_object,
                      parse_constant=profile.reject_non_finite_number)
    require(isinstance(data,dict) and isinstance(data.get('entries'),list),'invalid archive manifest')
    if data.get('format') in ('boosterai-index-v2','boosterai-index-v3'):return profile.validate_index(data)
    require(data.get('format')=='boosterai-index-v1','invalid archive manifest')
    require(len(data['entries'])<=200,'too many archive entries')
    names=set();paths=set()
    for entry in data['entries']:
        require(isinstance(entry,dict),'invalid entry')
        name=entry.get('name');path=entry.get('code');sha=entry.get('code_sha256')
        require(isinstance(name,str) and re.fullmatch(r'[a-z][a-z0-9-]{0,63}',name),'invalid entry name')
        require(name not in names,'duplicate name');names.add(name)
        require(isinstance(path,str) and re.fullmatch(r'solutions/'+re.escape(name)+r'/revisions/[0-9]{4}/example\.(py|cs|js|ts|ps1|sh|go|rs|java|cpp|c)',path),'invalid remote code path')
        require(path not in paths,'duplicate code path');paths.add(path)
        require(isinstance(sha,str) and re.fullmatch(r'[0-9a-f]{64}',sha),'invalid code hash')
        require(entry.get('outcome')=='success' and isinstance(entry.get('evidence_sha256'),str) and re.fullmatch(r'[0-9a-f]{64}',entry['evidence_sha256']),'missing success metadata')
        for key in ('title','test_summary'):
            require(isinstance(entry.get(key),str) and 0<len(entry[key])<=1000,'invalid descriptive metadata')
        for key in ('categories','tags'):
            require(isinstance(entry.get(key),list) and len(entry[key])<=30 and all(isinstance(v,str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',v) for v in entry[key]),'invalid taxonomy')
    return data


def _read_snapshot_bytes(path, limit, remaining, size_error):
    """Read at most one byte beyond the smaller applicable quota."""
    maximum = min(limit, remaining)
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    require(len(data) <= maximum,
            'snapshot total size exceeded' if remaining < limit else size_error)
    return data


def _read_metadata_json(path, size_error):
    """Bound metadata before strict UTF-8 decoding and JSON parsing."""
    raw = _read_snapshot_bytes(path, MAX_METADATA, MAX_METADATA, size_error)
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_manifest_object)


def verify_snapshot(work,commit):
    require(re.fullmatch(r'[0-9a-f]{40}',commit) is not None,'invalid snapshot commit')
    directory=safe(work,'snapshots/'+commit)
    meta = _read_metadata_json(safe(directory, 'snapshot.json'), 'snapshot metadata too large')
    require(isinstance(meta,dict),'snapshot metadata must be an object')
    require(meta.get('commit')==commit and meta.get('repository') in LOCAL_REPOSITORIES,'snapshot origin mismatch')
    raw = _read_snapshot_bytes(safe(directory, 'index.json'), MAX_INDEX, MAX_TOTAL, 'index too large')
    require(digest(raw)==meta.get('index_sha256'),'local manifest modified')
    data=validate_manifest(raw)
    total = len(raw)
    for entry in data['entries']:
        content = _read_snapshot_bytes(
            safe(directory, 'code/' + entry['code_sha256'] + '.txt'),
            MAX_CODE, MAX_TOTAL - total, 'local downloaded source too large',
        )
        require(digest(content)==entry['code_sha256'],'local downloaded source modified')
        total += len(content)
    return data


def read_guard(work):
    """Read only a complete, exact synchronization guard; preserve unknown legacy locks."""
    path=work/'sync.lock'
    try:info=path.lstat()
    except FileNotFoundError:return None
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400 or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise FileExistsError('legacy lock or invalid synchronization guard is present')
    try:
        content=path.read_bytes();value=json.loads(content)
        if not isinstance(value,dict) or set(value)!={'format','scratch'} or value['format']!='boosterai-archive-sync-guard-v1' or not isinstance(value['scratch'],str):
            raise ValueError('invalid guard')
        if content!=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8'):
            raise ValueError('invalid guard')
    except PermissionError:
        raise
    except (OSError,ValueError,TypeError,UnicodeError):
        raise FileExistsError('legacy lock or incomplete synchronization guard is present')
    return value


def scratch_path(work, requested):
    """Resolve a project-contained scratch directory on the snapshot filesystem.

    A caller may explicitly choose an existing directory. Without a selection,
    reuse the recognized guard's location or plan the documented default. Unknown
    legacy locks are rejected before creating a default scratch directory.
    """
    project=work.parent.parent
    existing=read_guard(work)
    create_default=requested is None and existing is None
    if requested is None and existing is not None:requested=existing['scratch']
    raw=Path(requested) if requested is not None else Path('_boosterAI/.booster-tmp')
    require('..' not in raw.parts,'unsafe scratch path')
    if raw.is_absolute():
        try:relative=raw.relative_to(project)
        except ValueError:raise ValueError('scratch must stay within project')
    else:relative=raw
    relative_text=relative.as_posix()
    if existing is not None:require(existing['scratch']==relative_text,'scratch differs from permanent synchronization guard')
    scratch=safe(project,relative_text)
    if not create_default or scratch.exists():
        require(scratch.is_dir(),'scratch directory missing')
        require(scratch.stat().st_dev==work.stat().st_dev,'scratch and snapshot must share a filesystem')
    return scratch,relative_text,create_default


def compatibility_guard(work,scratch_relative):
    """Install or validate the permanent legacy-writer barrier without overwriting.

    The barrier occupies the old O_EXCL lock path permanently. An older writer and
    first installation cannot both win; unknown, incomplete and active old locks
    are preserved and cause refusal. New writers use a separate short-lived lock.
    """
    guard=safe(work,'sync.lock')
    expected={'format':'boosterai-archive-sync-guard-v1','scratch':scratch_relative}
    content=(json.dumps(expected,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
    try:fd=os.open(guard,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:
        existing=read_guard(work)
        require(existing==expected,'scratch differs from permanent synchronization guard')
        return
    # A failed or interrupted guard write remains in place and fails closed.
    with os.fdopen(fd,'wb') as stream:
        stream.write(content);stream.flush();os.fsync(stream.fileno())


def created_directory_identity(path):
    """Validate a newly created directory or report its exact retained path and cause."""
    try:info=path.lstat()
    except OSError as error:
        raise ValueError('scratch directory identity unavailable; preserved '+str(path)+'; first error: '+str(error)) from error
    require(stat.S_ISDIR(info.st_mode) and not (getattr(info,'st_file_attributes',0)&0x400),
            'scratch directory identity invalid; preserved '+str(path))
    return info


class OwnedOperation:
    """Track only this invocation's known scratch files for safe bounded cleanup."""
    def __init__(self,scratch,prefix):
        self.root=safe(scratch,prefix+uuid.uuid4().hex)
        self.files=[];self.directories=[];self.unverified=[]
        self.root.mkdir();self.directories.append((self.root,created_directory_identity(self.root)))

    def directory(self,relative):
        """Create and track one directory inside this invocation's scratch root."""
        path=safe(self.root,relative);path.mkdir()
        try:identity=created_directory_identity(path)
        except ValueError as error:
            self.unverified.append(str(error));raise
        self.directories.append((path,identity));return path

    def write(self,relative,content):
        """Create and track an exclusive scratch file before writing any bytes."""
        expected_size=len(content);expected_digest=hashlib.sha256(content).digest()
        path=safe(self.root,relative);fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        self.files.append([path,None,expected_size,expected_digest])
        try:
            self.files[-1][1]=os.fstat(fd)
            with os.fdopen(fd,'wb') as stream:
                fd=None;stream.write(content)
        finally:
            if fd is not None:os.close(fd)
        return path

    def cleanup(self):
        """Remove only tracked regular files and empty owned directories, never unknown files."""
        # The created tree cannot be safely reconciled when even one identity is unknown.
        if self.unverified:raise ValueError(self.unverified[0])
        for path,identity,expected_size,expected_digest in reversed(self.files):
            try:current=path.lstat()
            except FileNotFoundError:continue
            require(stat.S_ISREG(current.st_mode) and not (getattr(current,'st_file_attributes',0)&0x400) and current.st_nlink==1,'scratch file link/reparse forbidden')
            safe(self.root,path.relative_to(self.root).as_posix())
            require(identity is not None and (current.st_dev,current.st_ino)==(identity.st_dev,identity.st_ino),'scratch file identity changed')
            require(current.st_size==expected_size,'scratch file content changed')
            with path.open('rb') as stream:content=stream.read(expected_size+1)
            require(len(content)==expected_size and hashlib.sha256(content).digest()==expected_digest,'scratch file content changed')
            path.unlink()
        for path,identity in reversed(self.directories):
            try:info=path.lstat()
            except FileNotFoundError:continue
            require(stat.S_ISDIR(info.st_mode) and not (getattr(info,'st_file_attributes',0)&0x400),'operation scratch link/reparse forbidden')
            if path!=self.root:safe(self.root,path.relative_to(self.root).as_posix())
            require((info.st_dev,info.st_ino)==(identity.st_dev,identity.st_ino),'scratch directory identity changed')
            path.rmdir()


@contextmanager
def sync_operation(work,requested):
    """Acquire the configured project-wide writer lock and isolated owned scratch.

    Refuse unreconciled operation directories rather than silently removing them.
    Both sync and rollback use the same guard, lock and cleanup protocol.
    """
    scratch,relative,create_default=scratch_path(work,requested);compatibility_guard(work,relative)
    if create_default:
        scratch=safe(work.parent.parent,relative);scratch.mkdir(parents=True,exist_ok=True)
        require(scratch.is_dir() and scratch.stat().st_dev==work.stat().st_dev,'scratch and snapshot must share a filesystem')
    identity=hashlib.sha256(os.path.normcase(str(work.parent.parent)).encode('utf-8')).hexdigest()[:16]
    lock=safe(scratch,'.archive-sync-'+identity+'.lock')
    token=(uuid.uuid4().hex+'\n').encode('ascii')
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_BINARY',0))
    lock_info=None;token_complete=False
    try:
        try:
            lock_info=os.fstat(fd);offset=0
            while offset<len(token):
                count=os.write(fd,token[offset:]);require(count>0,'short sync lock write');offset+=count
            token_complete=True
        except (OSError,ValueError) as error:
            raise ValueError('sync lock initialization failed; preserved '+str(lock)+'; first error: '+str(error)) from error
        os.close(fd);fd=None
        prefix='.archive-sync-op-'+identity+'-'
        require(not any(path.name.startswith(prefix) for path in scratch.iterdir()),'unreconciled sync scratch preserved')
        operation=OwnedOperation(scratch,prefix)
        try:yield operation
        finally:operation.cleanup()
    finally:
        if fd is not None:os.close(fd)
        if lock_info is not None and token_complete:
            current=safe(scratch,lock.name).lstat()
            require(stat.S_ISREG(current.st_mode) and not (getattr(current,'st_file_attributes',0)&0x400) and current.st_nlink==1,'sync lock link/reparse forbidden')
            require((current.st_dev,current.st_ino)==(lock_info.st_dev,lock_info.st_ino),'sync lock identity changed')
            require(current.st_size==len(token),'sync lock content changed')
            with lock.open('rb') as stream:observed=stream.read(len(token)+1)
            require(observed==token,'sync lock content changed')
            lock.unlink()


def atomic_json(root,name,data,operation):
    """Atomically replace a persistent pointer using an owned scratch file."""
    content=pointer_content(data)
    temporary=operation.write('state-'+uuid.uuid4().hex+'.json',content)
    os.replace(temporary,safe(root,name))


POINTER_UNKNOWN=object()


def pointer_content(data):
    """Serialize a pointer deterministically for atomic writing and byte readback."""
    return json.dumps(data,indent=2).encode('utf-8')


def read_pointer(work, name):
    """Read bounded pointer bytes and return None only for a missing pointer."""
    try:
        return _read_snapshot_bytes(
            safe(work, name), MAX_METADATA, MAX_METADATA, 'archive pointer too large',
        )
    except FileNotFoundError:
        return None


def pointer_failure(work,reason,status,before_active,before_previous,desired_active=None,desired_previous=None):
    """Report observed pointer effects after a failure without assuming an atomic pair.

    An unreadable readback is UNKNOWN. A changed or unknown pointer requires
    reconciliation; callers must never infer that an attempted action did not occur.
    """
    observed={}
    for name in ('active.json','previous.json'):
        try:observed[name]=read_pointer(work,name)
        except (ValueError,OSError,TypeError,RecursionError):observed[name]=POINTER_UNKNOWN
    active=observed['active.json'];previous=observed['previous.json']
    def preserved(before,after):
        return None if before is POINTER_UNKNOWN or after is POINTER_UNKNOWN else before==after
    active_unchanged=preserved(before_active,active);previous_unchanged=preserved(before_previous,previous)
    def effect(before,after,desired):
        if after is POINTER_UNKNOWN:return 'UNKNOWN'
        if before is POINTER_UNKNOWN:
            return 'APPLIED' if desired is not None and after==desired else 'UNKNOWN'
        if before is not POINTER_UNKNOWN and after==before:return 'UNCHANGED'
        if desired is not None and after==desired:return 'APPLIED'
        return 'OTHER'
    active_effect=effect(before_active,active,desired_active)
    previous_effect=effect(before_previous,previous,desired_previous)
    current_commit=None
    if isinstance(active,bytes):
        try:
            value=json.loads(active)
            candidate=value.get('commit') if isinstance(value,dict) else None
            if isinstance(candidate,str) and re.fullmatch(r'[0-9a-f]{40}',candidate):current_commit=candidate
        except (ValueError,UnicodeError):pass
    return {'status':status,'reason':str(reason),'active_snapshot_preserved':None,
            'active_pointer_preserved':active_unchanged,
            'active_pointer_effect':active_effect,'previous_pointer_preserved':previous_unchanged,
            'previous_pointer_effect':previous_effect,'active_commit_readback':current_commit,
            'reconciliation_required':active_effect!='UNCHANGED' or previous_effect!='UNCHANGED',
            'incomplete_stage':None,'downloaded_code_executed':False}


def active_commit(active):
    """Read the active commit ID from canonical local pointer metadata."""
    require(isinstance(active,dict),'active pointer must be an object')
    require(active.get('repository', REPOSITORY) in LOCAL_REPOSITORIES,
            'active pointer repository mismatch')
    return active['commit']


def sync(repository,force=False,fetch=remote_get,now=None,scratch=None):
    """Refresh one project's pinned snapshot under the compatibility and scratch locks."""
    work=storage(repository)
    with sync_operation(work,scratch) as operation:
      before_active=before_previous=POINTER_UNKNOWN
      desired_active=desired_previous=None
      try:
        timestamp=(now or dt.datetime.now(dt.timezone.utc));require(timestamp.tzinfo is not None,'timezone required');timestamp=timestamp.astimezone(dt.timezone.utc)
        before_active=read_pointer(work,'active.json');before_previous=read_pointer(work,'previous.json')
        old=json.loads(before_active) if before_active is not None else None
        if before_active is not None:
            verify_snapshot(work,active_commit(old))
        if (old and old.get('repository')==REPOSITORY and not force
                and old.get('checked_utc_date')==timestamp.date().isoformat()):
            return {'status':'ALREADY_CHECKED_TODAY','commit':old['commit'],'downloaded_code_executed':False}
        commit_data=json.loads(fetch(COMMIT_URL,MAX_INDEX));require(isinstance(commit_data,dict),'commit response must be an object');commit=commit_data.get('sha')
        require(isinstance(commit,str) and re.fullmatch(r'[0-9a-f]{40}',commit),'invalid main commit response')
        raw=fetch(RAW+commit+'/solutions/index.json',MAX_INDEX);data=validate_manifest(raw)
        downloaded={};total=len(raw)
        for entry in data['entries']:
            content=fetch(RAW+commit+'/'+entry['code'],MAX_CODE)
            require(len(content)<=MAX_CODE and digest(content)==entry['code_sha256'],'remote code size/hash mismatch')
            total+=len(content);require(total<=MAX_TOTAL,'snapshot total size exceeded');downloaded[entry['code_sha256']]=content
        final=safe(work,'snapshots/'+commit)
        if final.exists():
            verify_snapshot(work,commit)
            require(safe(final,'index.json').read_bytes()==raw,'existing snapshot differs from remote')
        else:
            stage=operation.directory('stage');operation.directory('stage/code')
            operation.write('stage/index.json',raw)
            for sha,content in downloaded.items():operation.write('stage/code/'+sha+'.txt',content)
            snapshot={'commit':commit,'repository':REPOSITORY,'branch':'main','index_sha256':digest(raw),'fetched_at':timestamp.isoformat(),'trusted':False}
            operation.write('stage/snapshot.json',json.dumps(snapshot).encode('utf-8'))
            final.parent.mkdir(exist_ok=True);os.replace(stage,final)
        verify_snapshot(work,commit)
        changed=old is not None and old['commit']!=commit
        next_active={'commit':commit,'checked_utc_date':timestamp.date().isoformat(),'checked_at':timestamp.isoformat(),'repository':REPOSITORY}
        rollback_commit=old['commit'] if changed else (old.get('rollback_commit') if old else None)
        if isinstance(rollback_commit,str) and re.fullmatch(r'[0-9a-f]{40}',rollback_commit):next_active['rollback_commit']=rollback_commit
        desired_active=pointer_content(next_active);desired_previous=pointer_content(old) if changed else None
        atomic_json(work,'active.json',next_active,operation)
        if changed:atomic_json(work,'previous.json',old,operation)
        return {'status':'UPDATED' if not old or old['commit']!=commit else 'UNCHANGED','commit':commit,'entries':len(data['entries']),'previous_commit':old['commit'] if old else None,'downloaded_code_executed':False,'remote_success_claims_independently_verified':False}
      except (ValueError,OSError,KeyError,TypeError,RecursionError) as error:
        return pointer_failure(work,error,'SYNC_FAILED',before_active,before_previous,desired_active,desired_previous)


def search(repository,query='',category=None,tag=None):
    """Read and filter an existing snapshot without creating archive storage."""
    work = storage(repository, create=False)
    active = _read_metadata_json(safe(work, 'active.json'), 'active pointer too large')
    data=verify_snapshot(work,active_commit(active));results=[];related_by_record={}
    for entry in data['entries']:
        aliases=entry.get('categoryAliases',[])
        text=' '.join([entry['name'],entry['title'],*entry['categories'],*aliases,*entry['tags']]).lower()
        if all(w in text for w in query.lower().split()) and (category is None or category in entry['categories'] or category in aliases or category=='/'.join(entry['categories'])) and (tag is None or tag in entry['tags']):
            result=dict(entry);result['local_inert_source']='snapshots/'+active['commit']+'/code/'+entry['code_sha256']+'.txt';result['untrusted']=True
            # A snapshot downloads only current sources. Historical relationships
            # remain discoverable metadata, not an assertion that their bytes exist locally.
            links = (profile._related_links_from_validated_index(data, entry)
                     if data.get('format') == 'boosterai-index-v3' else [])
            current={item['code']:item for item in data['entries']}
            related_links=[]
            for link in links:
                related=dict(link);target=current.get(link['relatedFile'])
                related['local_inert_source']=('snapshots/'+active['commit']+'/code/'+target['code_sha256']+'.txt') if target else None
                related['untrusted']=True;related_links.append(related)
            related_by_record[entry['name']]=related_links
            if 'related_files' not in result:result['related_files']=related_links
            results.append(result)
    return {'commit':active['commit'],'results':results,'related_files_by_record':related_by_record,'execution_authorized':False}


def rollback(repository,commit,scratch=None):
    """Switch to a verified stored snapshot under the same writer lock as sync."""
    work=storage(repository)
    with sync_operation(work,scratch) as operation:
      before_active=before_previous=POINTER_UNKNOWN
      desired_active=desired_previous=None
      try:
        verify_snapshot(work,commit)
        before_active=read_pointer(work,'active.json');before_previous=read_pointer(work,'previous.json')
        require(before_active is not None,'active pointer missing')
        old=json.loads(before_active);verify_snapshot(work,active_commit(old))
        if old['commit']==commit:return {'status':'UNCHANGED','commit':commit,'deleted_snapshots':0}
        next_active={'commit':commit,'repository':REPOSITORY,'rollback':True,'rollback_commit':old['commit']}
        desired_active=pointer_content(next_active);desired_previous=pointer_content(old)
        atomic_json(work,'active.json',next_active,operation)
        atomic_json(work,'previous.json',old,operation)
        return {'status':'ROLLED_BACK','commit':commit,'deleted_snapshots':0}
      except (ValueError,OSError,KeyError,TypeError,RecursionError) as error:
        return pointer_failure(work,error,'ROLLBACK_FAILED',before_active,before_previous,desired_active,desired_previous)


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    update=sub.add_parser('sync');update.add_argument('--root',required=True);update.add_argument('--force',action='store_true');update.add_argument('--scratch')
    find = sub.add_parser('search')
    find.add_argument('--root', required=True, action='append')
    profile.add_archive_search_filters(find)
    back=sub.add_parser('rollback');back.add_argument('--root',required=True);back.add_argument('--commit',required=True);back.add_argument('--scratch')
    args=parser.parse_args()
    try:
        if args.command=='sync':result=sync(args.root,args.force,scratch=args.scratch)
        elif args.command == 'search':
            if len(args.root) != 1 or not args.root[0].strip():
                parser.exit(2, 'archive search requires exactly one --root\n')
            result = search(args.root[0], args.query or '', args.category, args.tag)
        else:result=rollback(args.root,args.commit,args.scratch)
        print(json.dumps(result,indent=2));return 1 if result.get('status') in ('SYNC_FAILED','ROLLBACK_FAILED') else 0
    except (ValueError,OSError,KeyError,TypeError,RecursionError) as error:
        result={'status':'FAILED','reason':str(error)}
        if args.command in ('sync','rollback'):
            result.update({'active_pointer_effect':'UNKNOWN','previous_pointer_effect':'UNKNOWN',
                           'reconciliation_required':True,'downloaded_code_executed':False})
        print(json.dumps(result));return 1

if __name__=='__main__':sys.exit(main())
