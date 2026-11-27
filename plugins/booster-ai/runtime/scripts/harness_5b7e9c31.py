"""Single-executor, project-local acceptance harness for BoosterAI.

The host performs development and authorizes each explicitly named test. This
tool records one selected task-card graph and executes only a trusted Python
unittest case; it does not spawn an agent, interpret plan prose as commands,
grant permissions, or control processes which do not participate in its lock.
"""

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid


def _load(name, filename):
    """Load a fixed, bundled helper without a third-party package or configuration."""
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CARDS = _load('booster_harness_cards', 'task_cards_a61e2c97.py')
_CORE = _CARDS._CORE
_STATE = _load('booster_harness_state', 'harness_state_2fc47a91.py')
_TOOLS = _STATE.project_tools
_PACKAGE = _load('booster_harness_package', 'booster.py')
_RUNNER = Path(__file__).resolve().with_name('harness_unittest_b913ed42.py')
_BEGIN = '<!-- BOOSTERAI_HARNESS_SOURCE_BEGIN -->'
_END = '<!-- BOOSTERAI_HARNESS_SOURCE_END -->'
_CASE = re.compile(r'[A-Za-z_]\w*\.test_[A-Za-z_]\w*\Z', re.ASCII)
_ACTIVE = {'ACTIVE', 'VERIFYING', 'AMBIGUOUS'}
_MAX_SOURCE = 4 * 1024 * 1024
_MAX_REPORT = 65536
_TOOL_FILES = ('scripts/harness_5b7e9c31.py', 'scripts/harness_state_2fc47a91.py',
               'scripts/harness_unittest_b913ed42.py', 'scripts/task_cards_a61e2c97.py',
               'scripts/continuity_plan_64e9b1c2.py', 'scripts/claim_guard_8d31f72a.py',
               'scripts/project_tools.py', 'scripts/archive_profile.py', 'scripts/booster.py')
_POLICY_FILES = ('SKILL.md', 'AGENTS.md',
                 'references/community-contributions.md',
                 'references/daily-archive-sync.md',
                 'references/design-evidence.md',
                 'references/engineering-procedures.md',
                 'references/functional-partials.md',
                 'references/host-adapter.md',
                 'references/image-reading-procedure.md',
                 'references/in-flight-messages.md',
                 'references/independent-acceptance.md',
                 'references/logs-and-helpers.md',
                 'references/operating-algorithm.md',
                 'references/optional-harness.md',
                 'references/owned-cleanup.md',
                 'references/project-workspace.md',
                 'references/publication-privacy.md',
                 'references/records-and-continuity.md',
                 'references/solution-archive.md',
                 'references/user-delivery.md',
)


class VerificationUncertain(RuntimeError):
    """A verifier may have started or changed state and requires reconciliation."""


class VerificationAccessDenied(VerificationUncertain):
    """An access denial occurred after verification state may have changed."""


def _require(condition, message):
    """Fail closed when a required state, input or explicit boundary is absent."""
    if not condition:
        raise ValueError(message)


def _now():
    """Return an aware UTC time; wall time is observational, not a trusted clock."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _digest(value):
    """Fingerprint canonical JSON metadata without treating it as an attestation."""
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _source(root, relative):
    """Read one explicit JSON source or one marked JSON block in project Markdown.

    Reuse the task-card validator for the entire selected graph. The digest and
    cards cover only that selection, not omitted historical plans or undeclared
    dependencies. Repeated keys, markers, unsafe paths and changing files fail.
    """
    path, exists = _CORE._path(root, relative)
    _require(exists and path.stat().st_size <= _MAX_SOURCE, 'source missing or too large')
    before = _CORE._read_hash(root, relative)
    with path.open('rb') as stream:
        raw = stream.read(_MAX_SOURCE + 1)
    _require(len(raw) <= _MAX_SOURCE and hashlib.sha256(raw).hexdigest() == before
             and _CORE._read_hash(root, relative) == before, 'source changed while reading')
    text = raw.decode('utf-8-sig').replace('\r\n', '\n')
    if path.suffix.lower() == '.md':
        _require(text.count(_BEGIN) == 1 and text.count(_END) == 1,
                 'Markdown must contain exactly one harness source block')
        start = text.index(_BEGIN) + len(_BEGIN)
        end = text.index(_END)
        _require(start < end, 'source markers are reversed')
        text = text[start:end].strip()
        _require(text.startswith('```json\n') and text.endswith('\n```'),
                 'harness source must be one JSON fence')
        text = text[len('```json\n'):-len('\n```')]
    else:
        _require(path.suffix.lower() == '.json', 'source must be JSON or Markdown')
    source = json.loads(text, object_pairs_hook=_CARDS._unique_object,
                        parse_constant=lambda _x: (_ for _ in ()).throw(ValueError('non-finite JSON')))
    bundle = _CARDS.generate_cards(source)
    _CARDS.validate_cards(source, bundle)
    return bundle, {item['id']: item for item in bundle['cards']}


def _snapshot(root, card, test):
    """Hash the declared inputs, owned outputs and explicit test, including absences.

    The caller must declare every relevant file. This is a dirty-worktree-aware
    revision observation, not discovery of undeclared dependencies or proof of
    execution; safe path and stable file-read checks come from continuity planning.
    """
    paths = set(card['read_files']) | set(card['file_owners']) | {test}
    return {name: _CORE._read_hash(root, name) for name in sorted(paths)}


def _tool_snapshot():
    """Bind bundled code and explicit governing rules, not consumer project data.

    Rule changes invalidate earlier acceptance observations. This fixed manifest
    avoids scanning unrelated project records; new governing references must be
    added here and are checked by the package-policy coverage regression.
    """
    package = _CORE._root(Path(__file__).resolve().parents[1])
    return {name: _CORE._read_hash(package, name) for name in (*_TOOL_FILES, *_POLICY_FILES)}


def _revision(card, snapshot, case, tools):
    """Bind a test contract to observed code bytes, including uncommitted changes."""
    return _digest({'card': card, 'files': snapshot, 'case': case, 'tools': tools})


def _fresh(root, tasks, cards, identifier, visited=None, *, context=None):
    """Check one graph observation, memoizing only within this read operation.

    A shared refresh context checks each reachable card once and reads the tool
    snapshot once. It is never persisted or reused by a later operation: changed
    files, rules and task states are observed again. Cycles fail closed. This is
    evidence freshness, not proof of test quality or an atomic filesystem view.
    """
    context = {'results': {}} if context is None else context
    visited = set() if visited is None else visited
    if identifier in visited:
        return False
    results = context['results']
    if identifier in results:
        return results[identifier]
    visited.add(identifier)
    try:
        item = tasks.get(identifier)
        card = cards.get(identifier)
        fresh = (isinstance(item, dict) and item.get('status') == 'VERIFIED'
                 and card is not None and item.get('card_sha256') == _digest(card))
        if fresh:
            fresh = _snapshot(root, card, item['test']) == item.get('verified_files')
        if fresh:
            if 'tools' not in context:
                context['tools'] = _tool_snapshot()
            fresh = context['tools'] == item.get('verified_tools')
        if fresh:
            fresh = all(_fresh(root, tasks, cards, dependency, visited, context=context)
                        for dependency in card['depends_on'])
        results[identifier] = fresh
        return fresh
    finally:
        visited.remove(identifier)


def _refresh(root, data, cards):
    """Evaluate a shared graph observation before marking invalid cards stale."""
    tasks = data['tasks']
    context = {'results': {}}
    stale = [name for name, item in tasks.items()
             if item.get('status') == 'VERIFIED'
             and not _fresh(root, tasks, cards, name, context=context)]
    for name in stale:
        tasks[name]['status'] = 'STALE'
    return stale


def _data(data, source):
    """Recognize the one-state-file format and preserve unknown or foreign state."""
    if data is None:
        return {'format': _STATE.FORMAT, 'source': source, 'tasks': {}, 'stopped': False}
    _require(set(data) in ({'format', 'source', 'tasks'}, {'format', 'source', 'tasks', 'stopped'})
             and data['source'] == source and type(data.get('stopped', False)) is bool
             and isinstance(data['tasks'], dict), 'unrecognized or different harness plan; preserved')
    _require(all(isinstance(item, dict) for item in data['tasks'].values()),
             'unrecognized task record; preserved')
    data.setdefault('stopped', False)
    return data


def _item(data, identifier, run_id):
    """Select an existing task only with its exact issued run identifier."""
    item = data['tasks'].get(identifier)
    _require(isinstance(item, dict) and item.get('run_id') == run_id,
             'task or run identifier does not match')
    return item


def preflight(root, source, requested_python=None):
    """Observe package, actual Python, graph and cooperative harness state together.

    The lock may create normal project workspace/claim guards, but no task,
    acceptance test, agent or hook is started and no state file is changed.
    READY means eligible to plan a new run, not authorization or acceptance.
    Existing active/uncertain work and STOP remain barriers to a new run.
    """
    package = _PACKAGE.self_check(Path(__file__).resolve().parents[1])
    runtime = _PACKAGE.runtime_check('python', requested_version=requested_python)
    _require(runtime['valid'], 'Python does not satisfy the default or explicitly requested version')
    with _STATE.locked(root) as root:
        bundle, cards = _source(root, source)
        data, raw = _STATE.read(root)
        data = _data(data, source)
        stale = _refresh(root, data, cards)
        active = sorted(name for name, item in data['tasks'].items()
                        if item.get('status') in _ACTIVE | {'STOP_REQUESTED'})
        reasons = []
        if data['stopped']:
            reasons.append('project_stopped')
        if active:
            reasons.append('existing_active_or_uncertain_work')
        known = _ACTIVE | {'STOP_REQUESTED', 'STOPPED', 'DENIED', 'FAILED',
                           'STALE', 'VERIFIED', 'NOT_RUN', 'WAITING_PREREQUISITE'}
        if any(item.get('status') not in known for item in data['tasks'].values()):
            reasons.append('unrecognized_task_state')
        if any(name not in cards or item.get('card_sha256') != _digest(cards[name])
               for name, item in data['tasks'].items()):
            reasons.append('task_contract_review_required')
        return {'status': 'BLOCKED' if reasons else 'READY',
                'package_valid': package['valid'], 'runtime': runtime,
                'source_revision': bundle['source_revision'], 'card_count': len(cards),
                'state_present': raw is not None, 'stale_observed': stale,
                'active_tasks': active, 'blocked_reasons': reasons,
                'task_start_authorized': False, 'product_acceptance_verified': False,
                'codex_host_verified': False, 'automatic_hook_verified': False,
                'state_modified': False}


def _require_test_path(test):
    relative = _CORE._relative(test)
    key = _CORE._file_key(relative)
    protected = ('_partials', '_boosterai/_partials')
    _require(not any(key == path or key.startswith(path + '/') for path in protected),
             'acceptance test must be outside protected partials directories')
    _require(PurePosixPath(relative).suffix == '.py', 'acceptance test must be a project Python file')


def start(root, source, identifier, test, case, attempts, timeout, max_elapsed=3600):
    """Register one host-owned task without dispatching development or tests.

    The source graph, complete card bundle, ownership and prerequisites are
    checked before a unique run ID and start snapshot are recorded. A task
    already stopped, running, ambiguous or present is never silently restarted.
    """
    _require(_CASE.fullmatch(case) is not None, 'case must be Class.test_method')
    _require(type(attempts) is int and 1 <= attempts <= 10, 'attempt limit must be 1..10')
    _require(type(timeout) is int and 1 <= timeout <= 600, 'test timeout must be 1..600 seconds')
    _require(type(max_elapsed) is int and 1 <= max_elapsed <= 604800,
             'elapsed limit must be 1..604800 seconds')
    _require_test_path(test)
    with _STATE.locked(root) as root:
        bundle, cards = _source(root, source)
        data, raw = _STATE.read(root)
        data = _data(data, source)
        _require(not data['stopped'], 'project harness is stopped')
        _refresh(root, data, cards)
        _require(identifier in cards and identifier not in data['tasks'], 'unknown or already registered task')
        _require(not any(item.get('status') in _ACTIVE | {'STOP_REQUESTED'}
                         for item in data['tasks'].values()),
                 'another task is active or ambiguous')
        card = cards[identifier]
        _require(all(_fresh(root, data['tasks'], cards, dep) for dep in card['depends_on']),
                 'prerequisite lacks fresh acceptance')
        files = _snapshot(root, card, test)
        tools = _tool_snapshot()
        _require(files[test] is not None, 'explicit acceptance test is missing')
        item = {'status': 'ACTIVE', 'run_id': uuid.uuid4().hex, 'started_at': _now(),
                'ended_at': None, 'source_revision': bundle['source_revision'],
                'source_sha256': bundle['source_sha256'], 'card_sha256': _digest(card),
                'test': test, 'case': case, 'started_files': files,
                'started_revision': _revision(card, files, case, tools),
                'attempts': 0, 'max_attempts': attempts, 'timeout_seconds': timeout,
                'max_elapsed_seconds': max_elapsed,
                'cost': None, 'cost_status': 'NOT_AVAILABLE', 'rework_attempts': 0,
                'verification': None, 'attempt_history': [], 'retry_requires_change': False,
                'verified_files': None, 'verified_tools': None,
                'verified_revision': None,
                'scratch': None, 'effect_review': None}
        data['tasks'][identifier] = item
        _STATE.write(root, raw, data)
        return dict(item, task=identifier)


def status(root, source):
    """Return current state after detecting stale evidence or interrupted tests.

    A previously persisted VERIFYING means the verifier disappeared without a
    durable outcome; its effects and child processes remain unknown. Do not rerun.
    """
    with _STATE.locked(root) as root:
        _, cards = _source(root, source)
        data, raw = _STATE.read(root)
        data = _data(data, source)
        changed = bool(_refresh(root, data, cards))
        for item in data['tasks'].values():
            if item.get('status') in {'VERIFYING', 'STOP_REQUESTED'}:
                item['status'] = 'AMBIGUOUS'
                item['ended_at'] = _now()
                changed = True
        if changed:
            _STATE.write(root, raw, data)
        return data


def reconcile(root, source, identifier, run_id, effects_reviewed=False):
    """Observe declared effects after interruption without repeating any action.

    A first call observes current files after interruption or failure. An
    explicit later host declaration is accepted only while those observations
    remain unchanged. This is not proof of external effects or permission.
    A failed test also needs a changed declared file or verifier before retry;
    denied and stopped operations cannot be resumed here.
    """
    with _STATE.locked(root) as root:
        _, cards = _source(root, source)
        data, raw = _STATE.read(root)
        data = _data(data, source)
        item = _item(data, identifier, run_id)
        _require(not data['stopped'], 'project harness is stopped')
        _require(not any(name != identifier and other.get('status') in _ACTIVE
                         for name, other in data['tasks'].items()), 'another task is active or ambiguous')
        _require(item['status'] in {'ACTIVE', 'VERIFYING', 'AMBIGUOUS', 'FAILED'},
                 'task is not recoverable')
        _require(not item.get('access_denial', {}).get('retry_prohibited'),
                 'access denial prohibits reconciliation or replay')
        _require(identifier in cards and item['card_sha256'] == _digest(cards[identifier]),
                 'task contract changed; manual review required')
        current = _snapshot(root, cards[identifier], item['test'])
        baseline = item.get('attempt_files') or item['started_files']
        changed = sorted(path for path, value in current.items()
                         if baseline.get(path) != value)
        previous = item.get('effect_review')
        if effects_reviewed:
            _require(item['status'] in {'AMBIGUOUS', 'FAILED'} and isinstance(previous, dict)
                     and previous.get('host_review_declared') is False,
                     'observe effects before declaring review')
            _require(previous.get('observed_files') == current,
                     'effects changed after observation; inspect again')
        original_status = item['status']
        item['status'] = ('ACTIVE' if effects_reviewed else
                          'FAILED' if original_status == 'FAILED' else 'AMBIGUOUS')
        # A late report cannot prove child reaping or exclude a later refusal
        # whose state write failed. Only durable FAILED has the retry path.
        if effects_reviewed and original_status == 'AMBIGUOUS' and item.get('attempts', 0) > 0:
            observed = None
            if item.get('scratch'):
                relative = item['scratch'] + '/result.json'
                path, exists = _CORE._path(root, relative)
                observed = _report(root, path, item['case']) if exists else None
            report = observed[0] if observed else None
            if report is None:
                # A failed denial-state write can leave only VERIFYING on disk.
                # File review cannot establish why its verifier disappeared or
                # whether a refusal occurred. Preserve the slot, not replay.
                item['status'] = 'AMBIGUOUS'
            elif report['access_denied']:
                # The report prohibits replay but does not prove that the
                # disappeared verifier or its child has stopped. Keep its slot.
                item['status'] = 'AMBIGUOUS'
                item['access_denial'] = {'retry_prohibited': True,
                                         'child_outcome_unknown': True,
                                         'stop_outcome_unknown': bool(data['stopped'])}
                item['verification'] = {'case': item['case'], 'report': report,
                                        'elapsed_seconds': None, 'reconciled': True,
                                        'replay_authorized': False}
            else:
                item['status'] = 'AMBIGUOUS'
                item['verification'] = {'case': item['case'], 'report': report,
                                        'elapsed_seconds': None, 'reconciled': True,
                                        'replay_authorized': False}
        # Only never-run work or a durably completed FAILED attempt may yield
        # the serial slot. Ambiguous/orphaned verifiers retain their slot. Keep
        # failed baselines and retry gates so yielding never authorizes replay.
        never_run = (item.get('attempts') == 0 and item.get('verification') is None
                     and item.get('scratch') is None and not item.get('retry_requires_change'))
        completed_failure = (
            original_status == 'FAILED' and item.get('attempts', 0) > 0
            and item.get('retry_requires_change') is True
            and isinstance(item.get('failed_files'), dict)
            and isinstance(item.get('failed_tools'), dict)
            and isinstance(item.get('verification'), dict)
            and item['verification'].get('outcome') == 'FAILED')
        if (effects_reviewed and item['status'] == 'ACTIVE'
                and (never_run or completed_failure)
                and not all(_fresh(root, data['tasks'], cards, dep)
                            for dep in cards[identifier]['depends_on'])):
            item['status'] = 'WAITING_PREREQUISITE'
        item['effect_review'] = {'observed_at': _now(), 'changed_paths': changed,
                                 'observed_files': current,
                                 'host_review_declared': bool(effects_reviewed),
                                 'external_effects_authenticated': False}
        _STATE.write(root, raw, data)
        return dict(item['effect_review'], status=item['status'], task=identifier)


def stop(root, source, identifier, run_id):
    """Stop future harness actions for this run; already running outsiders remain outside control."""
    with _STATE.locked(root) as root:
        data, raw = _STATE.read(root)
        data = _data(data, source)
        item = _item(data, identifier, run_id)
        _require(item['status'] != 'VERIFYING', 'running test requires interruption reconciliation')
        item['status'] = 'STOPPED'
        item['ended_at'] = _now()
        data['stopped'] = True
        _STATE.write(root, raw, data)
        return {'task': identifier, 'status': 'STOPPED'}


def _report(root, path, case):
    """Read a bounded structured report for the exact explicitly requested test."""
    relative = path.relative_to(root).as_posix()
    checked, exists = _CORE._path(root, relative)
    if not exists or checked != path or path.stat().st_size > _MAX_REPORT:
        return None
    try:
        initial = path.lstat()
        before = _CORE._read_hash(root, relative)
        with path.open('rb') as stream:
            raw = stream.read(_MAX_REPORT + 1)
        if (len(raw) > _MAX_REPORT or hashlib.sha256(raw).hexdigest() != before
                or _CORE._read_hash(root, relative) != before
                or _CORE._identity(path.lstat()) != _CORE._identity(initial)):
            return None
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_CARDS._unique_object)
    except PermissionError:
        raise
    except (OSError, UnicodeError, ValueError):
        return None
    fields = {'format', 'case', 'tests_run', 'failures', 'errors', 'skipped',
              'expected_failures', 'unexpected_successes', 'success', 'access_denied'}
    if (not isinstance(result, dict) or set(result) != fields
            or result['format'] != 'boosterai-harness-unittest-v1' or result['case'] != case
            or type(result['success']) is not bool or type(result['access_denied']) is not bool):
        return None
    for field in fields - {'format', 'case', 'success', 'access_denied'}:
        # A single unittest can report multiple failed subtests.
        if type(result[field]) is not int or not 0 <= result[field] <= 10000:
            return None
    return result, before, _CORE._identity(initial)


def _interruptible(command, root, environment, timeout, stop_stream, on_stop=None):
    """Run the owned test child and honor an exact STOP line on its host channel.

    Only the direct child created here is terminated and reaped. Its descendants
    and external effects may persist. A daemon reader lasts only for this CLI
    process; EOF is not STOP. Access failures are surfaced and never bypassed.
    """
    stopped = threading.Event()

    def listen():
        """Observe bounded STOP lines without holding the host text-stream lock."""
        try:
            descriptor = stop_stream.fileno()
            pending = bytearray()
            overflow = False
            while True:
                byte = os.read(descriptor, 1)
                if not byte:
                    return
                if byte == b'\n':
                    if not overflow and bytes(pending).rstrip(b'\r') == b'STOP':
                        stopped.set()
                        return
                    pending.clear()
                    overflow = False
                elif len(pending) < 32:
                    pending.extend(byte)
                else:
                    overflow = True
        except (OSError, ValueError):
            stopped.set()

    threading.Thread(target=listen, daemon=True, name='booster-stop-input').start()
    if stopped.wait(0.02):
        if on_stop is not None:
            on_stop()
        return None, True
    child = subprocess.Popen(command, cwd=root, env=environment,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    while True:
        if stopped.is_set():
            persistence_error = None
            if on_stop is not None:
                try:
                    on_stop()
                except Exception as error:
                    persistence_error = error
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            if persistence_error is not None:
                if isinstance(persistence_error, PermissionError):
                    raise VerificationAccessDenied('STOP barrier access denied; do not retry') from persistence_error
                raise VerificationUncertain('STOP barrier could not be persisted') from persistence_error
            return None, True
        code = child.poll()
        if code is not None:
            if stopped.is_set():
                if on_stop is not None:
                    on_stop()
                return None, True
            return subprocess.CompletedProcess(command, code), False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            child.kill()
            child.wait()
            raise subprocess.TimeoutExpired(command, timeout)
        stopped.wait(min(0.1, remaining))


def verify(root, source, identifier, run_id, test, case, stop_stream=None):
    """Run one explicitly named trusted acceptance case under the cooperative lock.

    Persist VERIFYING before starting the bounded child. A valid success requires
    the exact structured case, one actually run test, zero failure/error/skip or
    expected-failure counts, exit zero, and unchanged declared inputs/outputs.
    Timeout, missing report or interruption are AMBIGUOUS, never auto-retried.
    With an explicitly supplied host stream, a STOP line terminates only the
    direct child and sets a persistent project STOP barrier. This executes
    project code: the caller must inspect and authorize the test.
    """
    _require_test_path(test)
    root = _CORE._root(root)
    _, initial_cards = _source(root, source)
    _require(identifier in initial_cards, 'unknown task')
    with _STATE.locked(root, list(initial_cards[identifier]['file_owners'])) as root:
        _, cards = _source(root, source)
        _require(cards.get(identifier) == initial_cards[identifier],
                 'task changed while acquiring file claims')
        data, raw = _STATE.read(root)
        data = _data(data, source)
        _require(not data['stopped'], 'project harness is stopped')
        _refresh(root, data, cards)
        item = _item(data, identifier, run_id)
        _require(not any(name != identifier and other.get('status') in _ACTIVE
                         for name, other in data['tasks'].items()), 'another task is active or ambiguous')
        _require(item['attempts'] < item['max_attempts'], 'explicit attempt limit reached')
        _require(item['status'] in {'ACTIVE', 'STALE', 'WAITING_PREREQUISITE'},
                 'task is stopped, denied, or requires effect review')
        _require(identifier in cards and item['card_sha256'] == _digest(cards[identifier]),
                 'task contract changed; manual review required')
        _require(item['test'] == test and item['case'] == case, 'test differs from explicitly registered case')
        started = dt.datetime.fromisoformat(item['started_at'])
        elapsed = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
        _require(0 <= elapsed < item.get('max_elapsed_seconds', 3600), 'elapsed task limit reached')
        card = cards[identifier]
        _require(all(_fresh(root, data['tasks'], cards, dep) for dep in card['depends_on']),
                 'prerequisite lacks fresh acceptance')
        before = _snapshot(root, card, test)
        tools_before = _tool_snapshot()
        _require(before[test] is not None, 'acceptance test is missing')
        if item.get('retry_requires_change'):
            old_files = item.get('failed_files')
            old_tools = item.get('failed_tools')
            _require(isinstance(old_files, dict) and
                     (before != old_files or (isinstance(old_tools, dict) and tools_before != old_tools)),
                     'failed test requires a changed declared file or verifier before retry')
        scratch = _TOOLS.scratch_directory(root)
        operation = Path(tempfile.mkdtemp(prefix='harness-', dir=scratch))
        try:
            operation_identity = operation.lstat()
        except OSError as error:
            raise VerificationUncertain('operation directory identity unavailable; preserved') from error
        report_path = operation / 'result.json'
        item['attempts'] += 1
        item['rework_attempts'] = max(0, item['attempts'] - 1)
        item['status'] = 'VERIFYING'
        item['attempt_files'] = before
        item['attempt_tools'] = tools_before
        item['scratch'] = str(operation.relative_to(root).as_posix())
        item['ended_at'] = None
        report = None
        observed = None
        clean = True
        begin = time.monotonic()
        attempt_started = _now()
        child = None
        after = None
        state_write_failed = False
        child_may_be_live = False

        def persist_state():
            """Write once; never retry failed persistence merely to record denial."""
            nonlocal raw, state_write_failed
            try:
                raw = _STATE.write(root, raw, data)
            except Exception:
                state_write_failed = True
                raise

        def persist_stop():
            """Record the STOP request before attempting to terminate the child."""
            nonlocal raw
            data['stopped'] = True
            item['status'] = 'STOP_REQUESTED'
            item['stop_requested_at'] = _now()
            persist_state()

        try:
            persist_state()
            env = os.environ.copy()
            for name in ('TEMP', 'TMP', 'TMPDIR'):
                env[name] = str(operation)
            try:
                elapsed = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
                remaining = item.get('max_elapsed_seconds', 3600) - elapsed
                if remaining <= 0 or elapsed < 0:
                    result = 'NOT_RUN'
                    item['attempts'] -= 1
                    item['rework_attempts'] = max(0, item['attempts'] - 1)
                else:
                    command = [sys.executable, '-I', '-S', '-B', str(_RUNNER),
                               '--root', str(root), '--test', test, '--case', case,
                               '--report', str(report_path)]
                    timeout = min(item['timeout_seconds'], remaining)
                    clean = False
                    child_may_be_live = True
                    if stop_stream is None:
                        child = subprocess.run(command, cwd=root, env=env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               timeout=timeout, check=False)
                        stopped = False
                    else:
                        child, stopped = _interruptible(command, root, env, timeout, stop_stream,
                                                        persist_stop)
                    child_may_be_live = False
                    if stopped:
                        data['stopped'] = True
                        result = 'STOPPED'
                    elif child.returncode == 77:
                        # The runner directly observed access denial before it
                        # could write a report. Never repeat the denied read.
                        result = 'DENIED'
                    else:
                        observed = _report(root, report_path, case)
                        report = observed[0] if observed else None
                        clean = report is not None  # Unknown child effects stay preserved.
                        result = 'AMBIGUOUS'
                if report is not None and report['access_denied']:
                    result = 'DENIED'
                elif (report is not None and child.returncode == 1 and not report['success']
                      and report['tests_run'] > 0 and any(report[key] for key in
                          ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))):
                    after = _snapshot(root, card, test)
                    item['retry_requires_change'] = True
                    item['failed_files'] = after
                    item['failed_tools'] = _tool_snapshot()
                    result = 'FAILED'
                elif report is not None and child.returncode == 0 and report == {
                        'format': 'boosterai-harness-unittest-v1', 'case': case,
                        'tests_run': 1, 'failures': 0, 'errors': 0, 'skipped': 0,
                        'expected_failures': 0, 'unexpected_successes': 0,
                        'success': True, 'access_denied': False}:
                    after = _snapshot(root, card, test)
                    _, updated_cards = _source(root, source)
                    if (before == after and tools_before == _tool_snapshot()
                            and updated_cards.get(identifier) == card
                            and all(_fresh(root, data['tasks'], updated_cards, dep)
                                    for dep in card['depends_on'])):
                        result = 'VERIFIED'
                        item['verified_files'] = after
                        item['verified_tools'] = tools_before
                        item['verified_revision'] = _revision(card, after, case, tools_before)
                        item['retry_requires_change'] = False
                    else:
                        result = 'STALE'
            except subprocess.TimeoutExpired:
                result = 'AMBIGUOUS'
            if result in {'AMBIGUOUS', 'DENIED', 'STOPPED'}:
                clean = False
            item['verification'] = {'case': case, 'report': report, 'outcome': result,
                                    'started_at': attempt_started, 'ended_at': _now(),
                                    'revision': _revision(card, before, case, tools_before),
                                    'elapsed_seconds': round(time.monotonic() - begin, 3),
                                    'observed_files_after': after,
                                    'external_effects_authenticated': False,
                                    'reconciliation_required': result in {
                                        'FAILED', 'DENIED', 'AMBIGUOUS', 'STOPPED'},
                                    'returncode': child.returncode if child is not None else None}
            history = item.setdefault('attempt_history', [])
            _require(isinstance(history, list) and len(history) <= item['max_attempts']
                     and (result == 'NOT_RUN' or len(history) < item['max_attempts']),
                     'invalid attempt history; preserved')
            if result != 'NOT_RUN':
                history.append(item['verification'])
            item['status'] = result
            item['ended_at'] = _now()
        except PermissionError as error:
            # Preserve known denial instead of allowing reconciliation to replay it.
            # Do not retry a failed state write or touch uncertain child effects.
            clean = False
            # A denied termination does not establish that the child stopped.
            # Retain the pending STOP and project barrier; status() exposes its
            # unknown outcome as AMBIGUOUS, with the denial recorded separately.
            stop_pending = item['status'] == 'STOP_REQUESTED'
            if not stop_pending:
                item['status'] = 'AMBIGUOUS' if child_may_be_live else 'DENIED'
                item['ended_at'] = _now()
            item['access_denial'] = {'retry_prohibited': True,
                                     'stop_outcome_unknown': stop_pending,
                                     'child_outcome_unknown': child_may_be_live}
            item['cleanup'] = {'status': 'PRESERVED', 'retry_prohibited': True}
            persisted = False
            if not state_write_failed:
                try:
                    persist_state()
                    persisted = True
                except Exception:
                    pass
            detail = ('denial recorded' if persisted else
                      'denial state not confirmed; preserve uncertainty')
            raise VerificationAccessDenied(
                'access denied; do not repeat the operation; ' + detail) from error
        except VerificationAccessDenied:
            raise
        except Exception as error:
            raise VerificationUncertain('verification state or effects need reconciliation') from error
        finally:
            if clean:
                # Remove only our exact report and empty owned directory. Unknown
                # child outputs remain untouched and visible for reconciliation.
                try:
                    directory = operation.lstat()
                    _require(stat.S_ISDIR(directory.st_mode)
                             and not (getattr(directory, 'st_file_attributes', 0) & 0x400)
                             and (directory.st_dev, directory.st_ino)
                             == (operation_identity.st_dev, operation_identity.st_ino),
                             'harness scratch identity changed; preserved')
                    if report_path.exists() and report is not None:
                        checked, exists = _CORE._path(root, report_path.relative_to(root).as_posix())
                        _require(exists and checked == report_path
                                 and _CORE._identity(report_path.lstat()) == observed[2]
                                 and _CORE._read_hash(root, report_path.relative_to(root).as_posix()) == observed[1],
                                 'harness report changed; preserved')
                        report_path.unlink()
                    operation.rmdir()
                    item['scratch'] = None
                    item['cleanup'] = {'status': 'CLEANED', 'retry_prohibited': False}
                except PermissionError:
                    item['cleanup'] = {'status': 'ACCESS_DENIED', 'retry_prohibited': True}
                except (OSError, ValueError):
                    item['cleanup'] = {'status': 'PRESERVED', 'retry_prohibited': True}
            else:
                item['cleanup'] = {'status': 'PRESERVED', 'retry_prohibited': True}
        try:
            _STATE.write(root, raw, data)
        except PermissionError as error:
            raise VerificationAccessDenied('state access denied; do not repeat the write') from error
        except Exception as error:
            raise VerificationUncertain('final verification state needs reconciliation') from error
        return dict(item, task=identifier)


def main(argv=None):
    """Expose bounded host-driven operations; no plan field becomes a command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--source', required=True, help='project-relative JSON or marked Markdown plan')
    sub = parser.add_subparsers(dest='command', required=True)
    begin = sub.add_parser('start')
    begin.add_argument('--task', required=True)
    begin.add_argument('--test', required=True)
    begin.add_argument('--case', required=True)
    begin.add_argument('--max-attempts', type=int, default=3)
    begin.add_argument('--timeout', type=int, default=60)
    begin.add_argument('--max-elapsed', type=int, default=3600)
    sub.add_parser('status')
    check = sub.add_parser('preflight')
    check.add_argument('--requested-python', help='Only for an explicit user-requested version')
    for name in ('verify', 'reconcile', 'stop'):
        command = sub.add_parser(name)
        command.add_argument('--task', required=True)
        command.add_argument('--run-id', required=True)
        if name == 'verify':
            command.add_argument('--test', required=True)
            command.add_argument('--case', required=True)
            command.add_argument('--listen-stop', action='store_true',
                                 help='honor an exact STOP line on this process stdin')
        if name == 'reconcile':
            command.add_argument('--effects-reviewed', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'preflight':
            result = preflight(args.root, args.source, args.requested_python)
        elif args.command == 'start':
            result = start(args.root, args.source, args.task, args.test, args.case,
                           args.max_attempts, args.timeout, args.max_elapsed)
        elif args.command == 'status':
            result = status(args.root, args.source)
        elif args.command == 'verify':
            result = verify(args.root, args.source, args.task, args.run_id, args.test, args.case,
                            sys.stdin if args.listen_stop else None)
        elif args.command == 'reconcile':
            result = reconcile(args.root, args.source, args.task, args.run_id, args.effects_reviewed)
        else:
            result = stop(args.root, args.source, args.task, args.run_id)
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        if result.get('status') == 'STOPPED' and args.command == 'verify':
            return 130
        return 0 if result.get('status') not in {'FAILED', 'DENIED', 'AMBIGUOUS', 'STALE', 'NOT_RUN', 'BLOCKED'} else 1
    except VerificationAccessDenied as error:
        print(json.dumps({'error': str(error), 'status': 'ACCESS_DENIED',
                          'reconciliation_required': True, 'retry_prohibited': True}, ensure_ascii=True))
        return 2
    except VerificationUncertain as error:
        print(json.dumps({'error': str(error), 'status': 'AMBIGUOUS',
                          'reconciliation_required': True}, ensure_ascii=True))
        return 2
    except PermissionError:
        print(json.dumps({'error': 'access denied; do not repeat the operation',
                          'status': 'ACCESS_DENIED', 'reconciliation_required': True,
                          'retry_prohibited': True}, ensure_ascii=True))
        return 2
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as error:
        print(json.dumps({'error': str(error), 'status': 'NOT_RUN',
                          'reconciliation_required': False}, ensure_ascii=True))
        return 2


if __name__ == '__main__':
    sys.exit(main())
