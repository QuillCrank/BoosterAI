"""Optional BoosterAI metadata gate and recoverable owned-scratch collector."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import sys
import uuid


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_json_object(pairs):
    """Reject ambiguous repeated keys while decoding every JSON object.

    ``pairs`` is the ordered sequence supplied by ``json.loads``. Return a new
    mapping only when no key appears twice; raise ValueError otherwise. Object
    order is retained. This applies equally to nested objects and performs no
    file or network writes.
    """
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON object key')
        result[key] = value
    return result


def reject_json_constant(_value):
    """Reject a bare nonstandard number literal during plan JSON decoding."""
    raise ValueError('non-finite JSON number')


def instant(value):
    require(isinstance(value, str), 'timestamp must be string')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'timestamp requires timezone')
    return result


def validate(plan):
    require(isinstance(plan, dict), 'plan must be object')
    for key in ('revision', 'goal'):
        require(isinstance(plan.get(key), str) and plan[key].strip(), f'missing {key}')
    nodes = plan.get('nodes')
    require(isinstance(nodes, list) and nodes, 'nodes must be nonempty list')
    ids = {}
    for node in nodes:
        require(isinstance(node, dict), 'node must be object')
        for key in ('id', 'owner', 'acceptance', 'target', 'revision'):
            require(isinstance(node.get(key), str) and node[key].strip(), f'missing node {key}')
        require(node['id'] not in ids, 'duplicate node')
        require(node.get('status') in ('planned', 'active', 'blocked', 'verified'), 'invalid status')
        require(isinstance(node.get('depends_on'), list) and all(isinstance(x, str) for x in node['depends_on']), 'invalid dependencies')
        ids[node['id']] = node
    visiting, done = set(), set()
    for identifier in ids:
        if identifier in done:
            continue
        # Keep each node's unfinished dependency iterator on an explicit stack.
        stack = [(identifier, iter(ids[identifier]['depends_on']))]
        visiting.add(identifier)
        while stack:
            node_id, predecessors = stack[-1]
            dependency = next(predecessors, None)
            if dependency is None:
                stack.pop()
                visiting.remove(node_id)
                done.add(node_id)
                continue
            require(dependency in ids, f'unknown dependency {dependency}')
            require(dependency not in visiting, 'dependency cycle')
            if dependency not in done:
                visiting.add(dependency)
                stack.append((dependency, iter(ids[dependency]['depends_on'])))
    active = []
    ready = []
    for node in nodes:
        dependencies_ok = all(ids[x]['status'] == 'verified' for x in node['depends_on'])
        if node['status'] in ('active', 'verified'):
            require(dependencies_ok, 'cannot advance unverified dependency')
        if node['status'] == 'active':
            active.append(node['id'])
        if node['status'] in ('planned', 'active') and dependencies_ok:
            ready.append(node['id'])
        if node['status'] == 'verified':
            e = node.get('evidence', {})
            require(isinstance(e, dict), 'evidence must be object')
            require(e.get('kind') == 'real' and e.get('result') == 'pass', 'real pass evidence required')
            require(e.get('revision') == node['revision'] and e.get('target') == node['target'], 'stale evidence')
            require(isinstance(e.get('reference'), str) and e['reference'].strip(), 'evidence reference required')
            require(instant(e['observed_at']) <= dt.datetime.now(dt.timezone.utc), 'future evidence')
    require(len(active) <= 1, 'multiple critical-path executors')
    require('next' in plan, 'next required')
    require(plan['next'] in ready if ready else plan['next'] is None, 'next must be ready or null when blocked/complete')
    if active:
        require(plan['next'] == active[0], 'next must preserve active owner')
    return {'valid': True, 'ready': ready, 'evidence_authenticated': False}


def checked_root(root, label='project'):
    """Resolve an existing root only after rejecting links in its original path.

    Each ancestor is checked before resolution so a junction cannot silently change
    the caller's intended scope. Concurrent external replacement is not prevented.
    """
    root = Path(root).absolute()
    require(root.is_dir(), label + ' root missing')
    for parent in (root, *root.parents):
        info = parent.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400), label + ' root link forbidden')
    return root.resolve()


def checked(root, relative):
    require(isinstance(relative, str) and relative, 'empty path')
    require(':' not in relative, 'alternate streams forbidden')
    raw = Path(relative)
    require(not raw.is_absolute() and not raw.drive and '..' not in raw.parts, 'unsafe relative path')
    current = root
    for part in raw.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400), 'links/reparse points forbidden')
    require(current.resolve().is_relative_to(root), 'path escapes root')
    return current


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_collector_lock(lock, identity, token):
    """Verify that an exclusively created lock still belongs to this operation.

    Refuse a missing, linked, replaced or modified file rather than removing it.
    This check does not prevent arbitrary external path replacement after inspection.
    """
    current = lock.lstat()
    require(stat.S_ISREG(current.st_mode) and not (getattr(current, 'st_file_attributes', 0) & 0x400) and current.st_nlink == 1, 'collector lock link/reparse forbidden')
    require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), 'collector lock identity changed')
    require(current.st_size == len(token), 'collector lock content changed')
    with lock.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and (opened.st_dev, opened.st_ino) == (identity.st_dev, identity.st_ino), 'collector lock identity changed')
        observed = stream.read(len(token) + 1)
    require(observed == token, 'collector lock content changed')
    after = lock.lstat()
    require(stat.S_ISREG(after.st_mode) and not (getattr(after, 'st_file_attributes', 0) & 0x400) and after.st_nlink == 1 and (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns), 'collector lock identity changed')


def collect(root, manifest_path, owner, apply=False):
    root_input = Path(root).absolute()
    require(root_input.is_dir(), 'root missing')
    # Reject reparse ancestors, not merely resolved containment.
    for parent in (root_input, *root_input.parents):
        s = parent.lstat()
        require(not stat.S_ISLNK(s.st_mode) and not (getattr(s, 'st_file_attributes', 0) & 0x400), 'root link forbidden')
    root = root_input.resolve()
    manifest = checked(root, manifest_path)
    data = json.loads(manifest.read_text(encoding='utf-8-sig'))
    require(isinstance(data, dict), 'manifest must be object')
    require(data.get('owner') == owner and bool(owner), 'manifest owner mismatch')
    require(isinstance(data.get('files'), list), 'files required')
    candidates, skipped = [], []
    seen = set()
    for item in data['files']:
        require(isinstance(item, dict), 'manifest file must be object')
        name = item.get('path')
        try:
            require(name not in seen, 'duplicate path')
            seen.add(name)
            path = checked(root, name)
            require(Path(name).parts[0] == '.booster-tmp', 'only .booster-tmp allowed')
            require(item.get('owner') == owner and item.get('inactive') is True and item.get('retain') is False, 'not inactive disposable owned scratch')
            require(instant(item['expires_at']) < dt.datetime.now(dt.timezone.utc), 'not expired')
            info = path.stat()
            require(path.is_file() and info.st_nlink == 1, 'not ordinary single-link file')
            require(digest(path) == item.get('sha256'), 'content changed')
            candidates.append((name, path, item, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)))
        except (ValueError, OSError, KeyError, TypeError) as error:
            skipped.append({'path': name, 'reason': str(error)})
    report = {'eligible': [c[0] for c in candidates], 'skipped': skipped, 'moved': []}
    if not apply or not candidates:
        return report
    lock = checked(root, '.booster-gc.lock')
    token = (uuid.uuid4().hex + '\n').encode('ascii')
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0))
    lock_identity = None
    token_complete = False
    try:
        try:
            lock_identity = os.fstat(fd)
            offset = 0
            while offset < len(token):
                count = os.write(fd, token[offset:])
                require(count > 0, 'short collector lock write')
                offset += count
            token_complete = True
        except PermissionError as error:
            raise PermissionError('collector lock initialization failed; retained ' + str(lock) + '; first error: ' + str(error)) from error
        except (OSError, ValueError) as error:
            raise ValueError('collector lock initialization failed; retained ' + str(lock) + '; first error: ' + str(error)) from error
        os.close(fd)
        fd = None
        verify_collector_lock(lock, lock_identity, token)
        run = '.booster-quarantine/' + uuid.uuid4().hex
        destination = checked(root, run)
        destination.mkdir(parents=True)
        recovery = destination / 'recovery.json'
        # Write the intended recovery map BEFORE the first move, retaining it on error.
        mapping = [{'source': c[0], 'destination': run + '/' + c[0], 'sha256': c[2]['sha256']} for c in candidates]
        recovery.write_text(json.dumps(mapping, indent=2), encoding='utf-8')
        for name, path, item, identity in candidates:
            verify_collector_lock(lock, lock_identity, token)
            checked(root, name)
            s = path.stat()
            require((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) == identity and s.st_nlink == 1 and digest(path) == item['sha256'], 'file changed during collection')
            target = checked(root, run + '/' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            require(not target.exists(), 'destination collision')
            verify_collector_lock(lock, lock_identity, token)
            path.rename(target)
            report['moved'].append({'source': name, 'destination': str(target.relative_to(root))})
        report['recovery'] = str(recovery.relative_to(root))
    finally:
        if fd is not None:
            os.close(fd)
        if lock_identity is not None and token_complete:
            # Refuse replacement or changed contents even when the inode stayed the same.
            verify_collector_lock(lock, lock_identity, token)
            lock.unlink()
    return report


def partials_init(root):
    """Create the standard nested partials index without modifying existing work.

    ``root`` must be an existing project root. Create ``_boosterAI/_partials``
    and its README only when absent; a previously existing README stays unchanged.
    Return the standard directory/index, creation state and whether an older root
    ``_partials`` path exists. That root path is checked by metadata only: it
    may be a file or directory and is never read, moved or overwritten here.
    Keep ``legacy_location`` compatible with the earlier initializer: it reports
    the nested directory if it existed before this call. New callers can inspect
    ``legacy_root_location`` for the former project-root location instead.
    Unsafe nested paths or filesystem errors fail without replacing prior files.
    """
    root = checked_root(root)
    legacy = root / '_partials'
    try:
        legacy.lstat()
    except FileNotFoundError:
        legacy_root_location = None
    else:
        legacy_root_location = '_partials'
    directory = checked(root, '_boosterAI/_partials')
    legacy_location = '_boosterAI/_partials' if directory.exists() else None
    directory.mkdir(parents=True, exist_ok=True)
    index = checked(root, '_boosterAI/_partials/README.md')
    if index.exists():
        require(index.is_file(), 'partials index is not a file')
        return {'directory': '_boosterAI/_partials', 'index': '_boosterAI/_partials/README.md', 'created': False, 'preserved_existing': True, 'legacy_location': legacy_location, 'legacy_root_location': legacy_root_location}
    text = '''# Verified functional partials

This index tracks executable functions, not a claim that files or builds are working products.
Reuse the project algorithm and backlog; do not create a second authoritative plan here.
Language priority: explicit request > existing module language > Python.
Prior partials in the project-root `_partials/`, if present, stay in place and must be inspected before creating replacements.

| ID | Function / contract | Code file | Dependencies | Owner / active run | Target / revision | Evidence / test state | Integration | Optional backup |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Current slice
- Algorithm node and acceptance:
- Actual prerequisites / target:
- Next action or exact blocker:

## Rules
Implement the first smallest independently invocable function. Keep one function's code in its own file when compatible with the existing module; preserve project conventions and working code. Use a short descriptive filename with a unique suffix, and a detailed function comment or docstring.
Test through its real target before implementing the next dependent function. Use NOT_RUN, FAIL, AMBIGUOUS or VERIFIED with evidence, never infer success from a build.
Examples in project-root `_partials/` and `_boosterAI/_partials/` are standalone, read-only learning references for consuming tasks. Read them for guidance; write production implementations in the project's own source files outside both directories. Never import, link, include, dynamically load or build against these examples. Authorized example maintenance is a separate operation. This guidance does not change OS permissions or scan dependencies.
Verify the consuming product through its actual entry point without sample-code dependencies.
At task completion, list the created or saved function files as verified clickable file links when the host supports them. Give each file's purpose and observed test status. If the host cannot provide an openable link, give the exact absolute path in the authorized local audience and state the limitation. Check that targets exist; never invent links, expose private paths to an unauthorized audience, or confuse reading links with production dependencies.
If per-step GitHub backup was requested and authorized, record remote/branch/commit and verified result; a failed push is BACKUP_FAILED, not success.
'''
    # Exclusive creation avoids replacing an index created concurrently.
    with index.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    return {'directory': '_boosterAI/_partials', 'index': '_boosterAI/_partials/README.md', 'created': True, 'preserved_existing': False, 'legacy_location': legacy_location, 'legacy_root_location': legacy_root_location}

def workspace_init(root, records_existing=None, create_records=False, schema=None):
    """Scaffold owned work; explicit record selection prevents guessing an existing ledger."""
    require(records_existing != '', 'empty project record selector')
    require(not (records_existing and create_records), 'choose existing or new records, not both')
    root = checked_root(root)
    record = None
    create_record = False
    if records_existing:
        record = checked(root, records_existing)
        require(record.is_file(), 'existing project record not found')
    elif create_records:
        legacy_record = checked(root, 'PROJECT_RECORDS.md')
        nested_record = checked(root, '_boosterAI/PROJECT_RECORDS.md')
        legacy_exists = legacy_record.exists()
        nested_exists = nested_record.exists()
        require(not (legacy_exists and nested_exists),
                'multiple project records exist; select one with --records-existing')
        if legacy_exists:
            record = legacy_record
        else:
            record = nested_record
            create_record = not nested_exists
        if not create_record:
            require(record.is_file(), 'project records path is not a file')
    # Select records and validate a selected schema before creating the scaffold.
    algorithm_path = '_boosterAI/algorithm.md'
    provenance_path = '_boosterAI/algorithm.provenance.json'
    if schema:
        algorithm = checked(root, algorithm_path)
        provenance = checked(root, provenance_path)
        source = checked(root, schema)
        require(source.is_file() and source.stat().st_nlink == 1, 'schema must be an ordinary file')
        require(source != algorithm, 'source is already the owned algorithm')
        require(not algorithm.exists() and not provenance.exists(), 'algorithm or provenance exists; reconcile instead of overwriting')
        content = source.read_bytes()
        source_sha256 = hashlib.sha256(content).hexdigest()
        source_mtime_ns = source.stat().st_mtime_ns
    report = partials_init(root)
    owned = checked(root, '_boosterAI')
    owned.mkdir(exist_ok=True)
    if create_record:
        # This recheck narrows, but cannot eliminate, races with nonparticipants.
        require(not checked(root, 'PROJECT_RECORDS.md').exists(),
                'project records changed; reconcile before creating another record')
        record = checked(root, '_boosterAI/PROJECT_RECORDS.md')
        with record.open('x', encoding='utf-8') as stream:
            stream.write('# Project records\n\n## Goal and acceptance\n\n## Current state and next action\n\n## Backlog\n\n## Decisions and evidence\n\nBoosterAI algorithm: `algorithm.md`.\n')
    report['project_record'] = str(record.relative_to(root)) if record else None
    report['records_status'] = 'selected' if record else 'not_selected_inspect_existing_system'
    algorithm = checked(root, algorithm_path)
    provenance = checked(root, provenance_path)
    if schema:
        # Destinations may have appeared while scaffolding.
        require(not algorithm.exists() and not provenance.exists(), 'algorithm or provenance exists; reconcile instead of overwriting')
        origin = {'source': schema, 'source_sha256': source_sha256, 'source_mtime_ns': source_mtime_ns, 'copied_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'relationship': 'working-copy; source preserved; compare hash before reuse'}
        with algorithm.open('xb') as stream:
            stream.write(content)
        with provenance.open('x', encoding='utf-8') as stream:
            json.dump(origin, stream, indent=2)
        report['algorithm'] = 'copied_with_provenance'
    elif not algorithm.exists():
        with algorithm.open('x', encoding='utf-8') as stream:
            stream.write('# Working algorithm\n\nSource: new request; record its source/time and scope revision here.\n\n| Node | Input / prerequisites | Function / effect | Dependencies / owner | Real target acceptance | Evidence / state | Recovery / next |\n| --- | --- | --- | --- | --- | --- | --- |\n')
        report['algorithm'] = 'created_empty_not_verified'
    else:
        require(algorithm.is_file(), 'algorithm path is not a file')
        report['algorithm'] = 'preserved_existing'
    overview = checked(root, '_boosterAI/README.md')
    if not overview.exists():
        with overview.open('x', encoding='utf-8') as stream:
            stream.write('# BoosterAI project work\n\nAlgorithm: `algorithm.md`. Functional pieces and index: `_partials/README.md`.\nKeep BoosterAI-owned evidence, handoffs and helper state here; respect the owner-selected scratch location. Existing project records and legacy partials remain authoritative in their original location.\nProject record selected at initialization: ' + (report['project_record'] or 'not selected; inspect existing records before creating another ledger') + '\n')
    return report

def schema_status(root):
    root = checked_root(root)
    provenance = checked(root, '_boosterAI/algorithm.provenance.json')
    try:
        text = provenance.read_text(encoding='utf-8')
    except FileNotFoundError:
        raise ValueError('schema provenance record is missing') from None
    data = json.loads(text)
    require(isinstance(data, dict) and isinstance(data.get('source'), str), 'invalid provenance')
    recorded_sha256 = data.get('source_sha256')
    require(isinstance(recorded_sha256, str) and re.fullmatch(r'[0-9A-Fa-f]{64}', recorded_sha256) is not None,
            'invalid provenance')
    recorded_sha256 = recorded_sha256.lower()
    source = checked(root, data['source'])
    working = checked(root, '_boosterAI/algorithm.md')
    return {
        'source': data['source'],
        'source_changed': digest(source) != recorded_sha256,
        'working_copy_changed': digest(working) != recorded_sha256,
        'acceptance_verified': False,
    }

def plain_link_targets(text, target_pattern):
    """Collect complete plain inline links with visible, balanced label brackets.

    Treat inline markup as opaque label content and keep it out of destinations.
    Blank lines, ATX headings, thematic breaks, Setext underlines, fences and
    block comments bound supported link labels.
    Apply the caller-specific path pattern after recognizing a supported link.
    Backtick spans may cross soft line breaks up to a recognized block start.
    Indented code at the start, after a blank, or after a completed top-level
    ATX heading, thematic break, fence or standalone comment is opaque.
    Recognize a single letter-leading Setext title at a known block start or
    directly after a supported completed top-level block.
    Leave possible list content and paragraph continuations visible.
    The source profile does not normalize Markdown container prefixes.
    """
    atx_start = r'\#{1,6}(?=[ \t]|\r?$)'
    thematic_line = r'(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})'
    setext_line = r'(?:=+|-+)[ \t]*'
    setext_heading = rf'[ ]{{0,3}}(?P<setext_text>[^\W\d_][^\r\n]*)\r?\n[ ]{{0,3}}{setext_line}(?=\r?$)'
    separator_line = rf'(?:{thematic_line}|{setext_line})(?=\r?$)'
    fence_line = r'(?:`{3,}[^`\r\n]*|~{3,}[^\r\n]*)(?=\r?$)'
    def compile_markup(block_start):
        return re.compile(
            rf"""
        ^[ ]{{0,3}}{atx_start}(?P<heading_text>[^\r\n]*)
        | {block_start}{setext_heading}
        | (?P<escape>\\[\\`<\[\]!])  # Escaped punctuation cannot open markup.
        | (?P<skip>
            (?P<indented_code>{block_start}{indented_code})
            | (?P<separator>^[ ]{{0,3}}{separator_line})
            | ^[ ]{{0,3}}  # A fence must start on its own source line.
            (?P<fence>(?P<backtick_fence>`{{3,}})|~{{3,}})
            (?(backtick_fence)[^`\r\n]*|[^\r\n]*)  # Only tildes allow backticks in fence info.
            (?:\r?\n|\Z)
            .*?(?:^[ ]{{0,3}}(?P=fence)(?(backtick_fence)`*|~*)[ \t]*(?:\r?\n|\Z)|\Z)
            | (?P<ticks>`+)(?!`)
            (?:
                [^\r\n]
                | \r?\n(?!  # Leave following blank lines and recognized blocks visible.
                    [ \t]*(?:\r?\n|\Z)
                    | [ ]{{0,3}}(?:
                        <!-- | > | {atx_start}
                        | (?:[-+*]|1[.)])[ \t]+(?=\S)
                        | {separator_line}
                        | {fence_line}
                    )
                )
            )*?
            (?<!`)(?P=ticks)(?!`)
            | (?P<block_comment>^[ ]{{0,3}})<!--.*?(?:-->|\Z)
            | <!--  # Inline comments cannot span a blank or a recognized block start.
            (?:[^\r\n]|\r?\n(?!
                [ \t]*\r?\n
                | [ ]{{0,3}}(?:<!--|{atx_start}|{separator_line}|{fence_line})
            ))*?
            (?:-->|\Z)
        )
        | (?P<list_marker>^[ ]{{0,3}}(?:[-+*]|[0-9]{{1,9}}[.)])(?=[ \t]|\r?$))
        | (?P<dedented>^[ \t]*\r?\n(?=[^ \t\r\n]))
        | `+  # An unmatched run remains ordinary text.
            """,
            flags=re.DOTALL | re.MULTILINE | re.VERBOSE,
        )

    indented_line = r'(?:[ ]{4}|[ ]{0,3}\t)[ \t]*[^ \t\r\n][^\r\n]*'
    indented_code = rf'{indented_line}(?:\r?\n(?:[ \t]*\r?\n)*{indented_line})*'
    top_level_markup = compile_markup(r'(?:\A|^[ \t]*\r?\n)')
    list_markup = compile_markup(r'(?!)')
    # A closing fence consumes its newline. Other blocks must end on their line.
    after_block = r'(?:(?<=\n)|[ \t]*\r?\n)'
    adjacent_code = re.compile(after_block + indented_code)
    adjacent_setext = re.compile(after_block + setext_heading, re.MULTILINE)

    spans = []
    start = 0
    paragraph_break = r'\r?\n[ \t]*\r?\n'
    block_break = '\0\n\n\0'

    def append_opaque(token):
        nonlocal start
        escape = token.group('escape')
        if token.group('skip') is None and (escape is None or escape[-1] not in '[]!'):
            return
        spans.append(text[start:token.start()])
        if (token.group('indented_code') is not None or token.group('separator') is not None
                or token.group('fence') is not None
                or token.group('block_comment') is not None
                or re.search(paragraph_break, token.group())):
            spans.append(block_break)
        else:
            # Opaque content keeps label brackets apart from any destination.
            spans.append('\0')
        start = token.end()

    def append_heading(token, heading_start, heading_end):
        """Scan a heading's inline content with label boundaries on both sides."""
        nonlocal start
        spans.extend((text[start:token.start()], block_break))
        start = heading_start
        for inline in markup.finditer(text, start, heading_end):
            append_opaque(inline)
        spans.extend((text[start:heading_end], block_break))
        start = token.end()

    markup = top_level_markup
    position = 0
    while (token := markup.search(text, position)) is not None:
        position = token.end()
        if token.group('list_marker') is not None:
            # Keep possible list continuations visible until a definite dedent.
            markup = list_markup
            continue
        if token.group('dedented') is not None:
            if markup is list_markup:
                # Reconsider the blank as a block start outside the list.
                markup = top_level_markup
                position = token.start()
            continue
        heading_start, heading_end = token.span('heading_text')
        if heading_start < 0:
            heading_start, heading_end = token.span('setext_text')
        completed_block = (
            heading_start >= 0
            or token.group('fence') is not None
            or token.group('block_comment') is not None
            or (token.group('separator') is not None
                and re.fullmatch(thematic_line, token.group('separator').lstrip(' ')))
        )
        if completed_block and text[token.start()] not in ' \t':
            # Only a definite column-zero block ends a possible list continuation.
            markup = top_level_markup
        if heading_start < 0:
            append_opaque(token)
        else:
            append_heading(token, heading_start, heading_end)
        if completed_block and markup is top_level_markup:
            while (heading := adjacent_setext.match(text, position)) is not None:
                append_heading(heading, *heading.span('setext_text'))
                position = heading.end()
            code = adjacent_code.match(text, position)
            if code is not None:
                start = position = code.end()
    spans.append(text[start:])
    visible = ''.join(spans)

    destination = re.compile(r'\]\(([^()\s\0]*)\)')
    targets = []
    labels = []
    destination_end = 0
    for marker in re.finditer(r'!?\[|\]|' + paragraph_break, visible):
        if marker.start() < destination_end:
            continue
        value = marker.group()
        if value.endswith('['):
            labels.append(value == '[')
        elif value != ']':
            labels.clear()
        elif labels:
            is_link = labels.pop()
            match = destination.match(visible, marker.start())
            if match is None:
                continue
            route = re.fullmatch(target_pattern, match.group())
            destination_end = match.end()
            if is_link:
                # A link inside a label prevents that outer label from linking.
                labels.clear()
                if route is not None:
                    targets.append(route[1])
    return targets


def check_codex_entry(text, expected_fields):
    """Check the stub metadata and its single plain inline route to the root skill."""
    if not text.startswith('---\n'):
        raise ValueError('Codex registration frontmatter must begin with ---')
    parts = text.split('---\n', 2)
    if len(parts) != 3:
        raise ValueError('Codex registration frontmatter terminator missing')
    fields = {}
    for line in parts[1].splitlines():
        key, separator, value = line.partition(':')
        if not separator or key not in ('name', 'description') or key in fields:
            raise ValueError('invalid Codex registration frontmatter field')
        fields[key] = value.strip()
    if fields != expected_fields:
        raise ValueError('Codex registration metadata differs from canonical skill')
    canonical_link = '../../../SKILL.md'
    links = plain_link_targets(parts[2], r'\]\(([^)\s]+)\)')
    require(canonical_link in links, 'Codex registration is missing its canonical skill link')
    canonical_target = Path(canonical_link)
    canonical_routes = sum(
        Path(os.path.normpath(link)) == canonical_target
        for link in links
        if not link.startswith('/') and not any(marker in link for marker in ':?#\\')
    )
    require(canonical_routes == 1, 'Codex registration repeats its canonical skill link')
    return True


def self_check(root):
    """Check the package's narrow frontmatter and bundled route contract."""
    root = checked_root(root, 'package')
    require(sys.version_info >= (3, 10), 'Python 3.10 or newer required')
    canonical = checked(root, 'SKILL.md')
    text = canonical.read_text(encoding='utf-8')
    require(text.startswith('---\n'), 'frontmatter must begin with ---')
    parts = text.split('---\n', 2)
    require(len(parts) == 3, 'frontmatter terminator missing')
    fields = {}
    for line in parts[1].splitlines():
        key, separator, value = line.partition(':')
        require(separator and key in ('name', 'description') and key not in fields, 'unsupported or duplicate frontmatter field')
        fields[key] = value.strip()
    require(fields.get('name') == 'booster-ai', 'invalid skill name')
    require(0 < len(fields.get('description', '')) <= 1024, 'invalid description')
    require('<' not in fields['description'] and '>' not in fields['description'], 'invalid description markup')
    resources = plain_link_targets(parts[2], r'\]\((references/[^)]+)\)')
    require(resources, 'no bundled references')
    targets = set()
    for resource in resources:
        path = checked(root, resource)
        require(path.is_file(), 'missing bundled resource: ' + resource)
        target = path.resolve(strict=True)
        require(target not in targets, 'duplicate bundled resource: ' + resource)
        targets.add(target)
    for resource in ('scripts/booster.py', 'scripts/project_tools.py', 'scripts/archive_sync.py',
                     'scripts/project_index_freshness_a62e9d47.py',
                     'scripts/archive_profile.py', 'scripts/find_directory.py',
                     'scripts/contribution_preflight_5c294f8d.py', 'scripts/publication_scan_91c4e6b2.py',
                     'scripts/claim_guard_8d31f72a.py', 'scripts/continuity_plan_64e9b1c2.py',
                     'scripts/task_cards_a61e2c97.py', 'scripts/flow_schema_42d8e671.py',
                     'scripts/flow_cards_f930b1a6.py', 'scripts/harness_5b7e9c31.py',
                     'scripts/harness_state_2fc47a91.py', 'scripts/harness_unittest_b913ed42.py',
                     'solutions/archive.schema.json',
                     'tests/test_booster.py', 'tests/test_project_tools.py', 'tests/test_archive_sync.py',
                     'tests/test_archive_profile.py', 'tests/test_contribution_preflight.py',
                     'tests/test_publication_scan.py', 'tests/test_claim_guard.py',
                     'tests/test_continuity_plan.py', 'tests/test_task_cards.py',
                     'tests/test_harness_state.py', 'tests/test_harness_flow.py',
                     'tests/test_harness_pilot_acceptance.py', 'tests/test_harness_unittest.py',
                     'tests/test_harness_recovery_acceptance.py',
                     'tests/test_flow_schema.py', 'tests/test_flow_cards.py', 'tests/repo_temp.py',
                     'tests/test_repo_temp.py', 'tests/test_find_directory.py', 'README.md'):
        require(checked(root, resource).is_file(), 'missing package file: ' + resource)
    registration = checked(root, '.agents/skills/booster-ai/SKILL.md')
    if registration.exists():
        require(registration.is_file(), 'Codex registration is not a file')
        check_codex_entry(registration.read_text(encoding='utf-8'), fields)
        target = (registration.parent / '../../../SKILL.md').resolve(strict=True)
        require(target == canonical.resolve(), 'Codex registration resolves to a different source')
        target_hash = digest(target)
        registration_status = 'present_valid'
    else:
        registration_status = 'not_present'
        target_hash = None
    return {'valid': True, 'validator': 'bundled-stdlib', 'references_checked': resources,
            'python_version': '.'.join(str(n) for n in sys.version_info[:3]),
            'canonical_source_sha256': digest(canonical), 'registration_target_sha256': target_hash,
            'codex_repo_registration': registration_status, 'agent_discovery_verified': False}

def runtime_check(language, observed_version=None, requested_version=None):
    """Check one observed runtime against defaults or an explicit user request.

    This selects no executable and installs nothing. Python defaults to this
    process's actual interpreter; externally reported SDK/runtime versions are
    labelled unverified observations. An override is a caller declaration of a
    user's explicit request, never authorization inferred from installed tools.
    Existing helper compatibility and CI matrices are unchanged.
    """
    require(language in ('python', 'dotnet'), 'language must be python or dotnet')
    source = 'caller_reported'
    if observed_version is None:
        require(language == 'python', 'dotnet requires an observed SDK version, not a runtime version')
        require(sys.version_info.releaselevel == 'final', 'current Python interpreter must be a stable release')
        observed_version = '.'.join(str(n) for n in sys.version_info[:3])
        source = 'current_python_process'
    def version(value):
        require(isinstance(value, str) and re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?', value) is not None,
                'version must be a stable numeric major.minor or major.minor.patch')
        return tuple(int(n) for n in value.split('.'))
    observed = version(observed_version)
    minimum = (3, 12) if language == 'python' else (10, 0)
    if requested_version is not None:
        requested = version(requested_version)
        valid = observed[:len(requested)] == requested
        policy = 'explicit_user_version_request'
    else:
        valid = observed >= minimum
        policy = 'default_minimum'
    return {'valid': valid, 'language': language, 'observed_version': observed_version,
            'observation_source': source, 'environment_verified': source == 'current_python_process',
            'default_minimum': '.'.join(str(n) for n in minimum),
            'requested_version': requested_version, 'policy': policy,
            'runtime_changed': False, 'product_acceptance_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate').add_argument('plan')
    runtime = sub.add_parser('runtime-check')
    runtime.add_argument('--language', choices=('python', 'dotnet'), required=True, action='append')
    runtime.add_argument('--observed-version', action='append')
    runtime.add_argument('--requested-version', action='append',
                         help='Only for the user\'s explicit version request')
    sub.add_parser('partials-init').add_argument('--root', required=True)
    schema_status_parser = sub.add_parser('schema-status')
    schema_status_parser.add_argument('--root', required=True, action='append')
    workspace = sub.add_parser('workspace-init')
    workspace.add_argument('--root', required=True)
    workspace.add_argument('--records-existing', action='append')
    workspace.add_argument('--create-records', action='store_true')
    workspace.add_argument('--schema')
    self_check_parser = sub.add_parser('self-check')
    self_check_parser.add_argument('--root', action='append')
    for command in ('gc-plan', 'gc-apply'):
        child = sub.add_parser(command)
        child.add_argument('--root', required=True)
        child.add_argument('--manifest', required=True, help='root-relative JSON path')
        child.add_argument('--owner', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'runtime-check':
            requested_versions = args.requested_version or ()
            if len(set(requested_versions)) > 1:
                runtime.error('conflicting --requested-version values')
            requested_version = requested_versions[0] if requested_versions else None
            observed_versions = args.observed_version or ()
            if len(set(observed_versions)) > 1:
                runtime.error('conflicting --observed-version values')
            observed_version = observed_versions[0] if observed_versions else None
            languages = args.language
            if len(set(languages)) > 1:
                runtime.error('conflicting --language values')
            result = runtime_check(languages[0], observed_version, requested_version)
        elif args.command == 'schema-status':
            roots = args.root
            if len(roots) != 1 or not roots[0].strip():
                schema_status_parser.error('--root may be supplied only once with a nonempty value')
            result = schema_status(roots[0])
        elif args.command == 'workspace-init':
            selectors = args.records_existing
            if selectors and len(selectors) > 1:
                parser.error('--records-existing may only be supplied once')
            result = workspace_init(args.root, selectors[0] if selectors else None,
                                    args.create_records, args.schema)
        elif args.command == 'partials-init':
            result = partials_init(args.root)
        elif args.command == 'self-check':
            roots = args.root
            if roots is not None and (len(roots) != 1 or not roots[0].strip()):
                self_check_parser.error('--root may be supplied only once with a nonempty value')
            root = roots[0] if roots else str(Path(__file__).resolve().parents[1])
            result = self_check(root)
        elif args.command == 'validate':
            plan_text = Path(args.plan).read_text(encoding='utf-8-sig')
            try:
                plan = json.loads(plan_text, object_pairs_hook=unique_json_object,
                                  parse_constant=reject_json_constant)
            except RecursionError:
                raise ValueError('plan JSON nesting is too deep') from None
            result = validate(plan)
        else:
            result = collect(args.root, args.manifest, args.owner, args.command == 'gc-apply')
        print(json.dumps(result, indent=2))
        return 1 if args.command == 'runtime-check' and not result['valid'] else 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        result = {'error': str(error)}
        if args.command == 'gc-apply':
            result.update({'error_type': type(error).__name__, 'effect': 'UNKNOWN', 'reconciliation_required': True})
            first = error
            while first.__context__ is not None:
                first = first.__context__
            if first is not error:
                result.update({'first_error_type': type(first).__name__, 'first_error': str(first)})
        print(json.dumps(result))
        return 1

if __name__ == '__main__':
    sys.exit(main())
