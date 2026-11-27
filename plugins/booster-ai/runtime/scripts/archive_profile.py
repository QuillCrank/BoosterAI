"""Validate the deliberately limited JSON Schema vocabulary used by the archive."""

import argparse
import json
import re
import hashlib
import math
from pathlib import Path


class _ArchiveFilterAction(argparse.Action):
    """Store a supplied filter and allow subsequent identical values."""

    def __call__(self, parser, namespace, values, option_string=None):
        previous = getattr(namespace, self.dest, None)
        if previous is not None and previous != values:
            parser.exit(2, 'conflicting archive filter values\n')
        setattr(namespace, self.dest, values)


def add_archive_search_filters(parser):
    """Register the three optional scalar filters on an archive search parser."""
    for option in ('--query', '--category', '--tag'):
        parser.add_argument(option, action=_ArchiveFilterAction)


class UnsupportedSchemaError(ValueError):
    """Identify unsupported profile constructs even inside schema alternatives."""


def reject_non_finite_number(_literal):
    """Reject non-finite number literals in JSON input."""
    raise ValueError('non-finite JSON number')


def _json_equality_key(value):
    """Return a hashable JSON equality key for constants, options and uniqueness.

    Booleans are distinct from numbers, while equal integer/float values share a
    key. Object order is ignored and array order is retained recursively. Reject
    non-JSON values and non-finite numbers rather than letting Python equality
    silently broaden the supported JSON Schema contract.
    """
    if value is None:
        return ('null',)
    if isinstance(value, bool):
        return ('boolean', value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('non-finite JSON number')
        return ('number', value)
    if isinstance(value, str):
        return ('string', value)
    if isinstance(value, list):
        return ('array', tuple(_json_equality_key(item) for item in value))
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return ('object', frozenset((key, _json_equality_key(item))
                                    for key, item in value.items()))
    raise ValueError('unsupported JSON value')


def validate_schema(value, schema, document=None, location='$'):
    """Check a value against the bundled profile subset; reject unsupported schema keywords.

    The profile uses types, local references, oneOf alternatives, object and array
    constraints, string patterns and scalar bounds. This is not a general JSON Schema
    implementation. Validation is offline and raises ValueError on failure.
    """
    document = schema if document is None else document
    if not isinstance(schema, dict):
        raise UnsupportedSchemaError('unsupported schema at ' + location)
    supported = {'$schema', '$id', '$defs', '$ref', 'title', 'description', 'type', 'const',
                 'enum', 'required', 'properties', 'additionalProperties', 'items', 'minItems',
                 'maxItems', 'uniqueItems', 'minLength', 'maxLength', 'pattern', 'minimum', 'maximum',
                 'oneOf'}
    if set(schema) - supported:
        raise UnsupportedSchemaError('unsupported schema keyword at ' + location)
    if '$ref' in schema:
        ref = schema['$ref']
        if not isinstance(ref, str) or not ref.startswith('#/$defs/') or '/' in ref[8:]:
            raise UnsupportedSchemaError('unsupported schema reference')
        target = document.get('$defs', {}).get(ref[8:])
        if not isinstance(target, dict):
            raise UnsupportedSchemaError('unresolved schema reference')
        validate_schema(value, target, document, location)
    if 'oneOf' in schema:
        alternatives = schema['oneOf']
        if not isinstance(alternatives, list) or not alternatives:
            raise UnsupportedSchemaError('unsupported schema alternatives')
        matched = 0
        for alternative in alternatives:
            try:
                validate_schema(value, alternative, document, location)
            except UnsupportedSchemaError:
                raise
            except ValueError:
                continue
            matched += 1
        if matched != 1:
            raise ValueError('schema alternatives mismatch at ' + location)
    kind = schema.get('type')
    types = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list),
             'string': lambda v: isinstance(v, str), 'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),
             'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
                                 and (isinstance(v, int) or math.isfinite(v)),
             'boolean': lambda v: isinstance(v, bool)}
    if kind is not None and (kind not in types or not types[kind](value)):
        raise ValueError('invalid type at ' + location)
    if 'const' in schema and _json_equality_key(value) != _json_equality_key(schema['const']):
        raise ValueError('invalid constant at ' + location)
    if 'enum' in schema and _json_equality_key(value) not in {
            _json_equality_key(option) for option in schema['enum']}:
        raise ValueError('invalid option at ' + location)
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                raise ValueError('missing field at ' + location + '.' + key)
        properties = schema.get('properties', {})
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], document, location + '.' + key)
            elif schema.get('additionalProperties') is False:
                raise ValueError('unexpected field at ' + location + '.' + key)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', float('inf')):
            raise ValueError('invalid array length at ' + location)
        if schema.get('uniqueItems') and len({_json_equality_key(v) for v in value}) != len(value):
            raise ValueError('duplicate array item at ' + location)
        if 'items' in schema:
            for index, item in enumerate(value):
                validate_schema(item, schema['items'], document, location + '[' + str(index) + ']')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', float('inf')):
            raise ValueError('invalid string length at ' + location)
        if 'pattern' in schema and re.search(schema['pattern'], value) is None:
            raise ValueError('invalid string pattern at ' + location)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('non-finite number at ' + location)
        if value < schema.get('minimum', value) or value > schema.get('maximum', value):
            raise ValueError('number outside bounds at ' + location)


SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'solutions/archive.schema.json'
MAX_INDEX_BYTES = 1048576
MAX_CODE_BYTES = 262144
MAX_TOTAL_BYTES = 8388608
LANGUAGES = {'.py': 'Python', '.cs': 'C#', '.js': 'JavaScript', '.ts': 'TypeScript',
             '.ps1': 'PowerShell', '.sh': 'Shell', '.go': 'Go', '.rs': 'Rust',
             '.java': 'Java', '.cpp': 'C++', '.c': 'C'}


def code_filename(name, revision, digest, suffix):
    """Generate a concise, stable filename whose suffix identifies this exact revision.

    The identifier combines the public record name, revision number and source hash.
    It is deterministic, so rebuilding the index never renames an unchanged record.
    """
    if suffix not in LANGUAGES:
        raise ValueError('unsupported code extension')
    parts = [part for part in name.split('-') if part]
    short = (parts[0] + '-' + parts[-1] if len(parts) > 2 else name)[:16].rstrip('-')
    unique = hashlib.sha256((name + ':' + str(revision) + ':' + digest).encode('utf-8')).hexdigest()[:8]
    return short + '-' + unique + suffix


def archive_file_identity(path):
    """Parse a canonical archived revision path without accessing the filesystem.

    Return its category tuple, record name and revision. Filename shape is checked,
    but only a caller with the archived metadata and bytes can authenticate its hash
    or confirm that the referenced historical revision exists.
    """
    if not isinstance(path, str):
        raise ValueError('archive file path must be a string')
    parts = path.split('/')
    if len(parts) < 6 or parts[0] != 'solutions' or parts[-3] != 'revisions':
        raise ValueError('invalid archive file path')
    categories, name, revision, filename = parts[1:-4], parts[-4], parts[-2], parts[-1]
    if (not 1 <= len(categories) <= 8
            or any(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', item) is None for item in categories)
            or re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name) is None
            or re.fullmatch(r'[0-9]{4}', revision) is None or int(revision) == 0):
        raise ValueError('invalid archive file identity')
    suffix = Path(filename).suffix
    if suffix not in LANGUAGES:
        raise ValueError('unsupported archive file extension')
    expected_prefix = code_filename(name, int(revision), '0' * 64, suffix).rsplit('-', 1)[0]
    if re.fullmatch(re.escape(expected_prefix) + r'-[0-9a-f]{8}' + re.escape(suffix), filename) is None:
        raise ValueError('invalid archive file name')
    return tuple(categories), name, int(revision)


def validate_entry(entry, schema=None):
    """Validate one archive record and its path without opening or executing source code.

    In addition to the JSON Schema profile, check the category hierarchy, language,
    deterministic code filename, sorted tags, CodeMeta keywords and any declared
    sanitized-summary digest. A matching digest does not authenticate the observation.
    """
    schema = schema or json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    validate_schema(entry, schema['$defs']['entry'], schema)
    for value in [entry['name'], *entry['categories'], *entry['categoryAliases'], *entry['tags']]:
        if re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value) is None:
            raise ValueError('invalid archive slug')
    for field in ('code_sha256', 'evidence_sha256'):
        if re.fullmatch(r'[0-9a-f]{64}', entry[field]) is None:
            raise ValueError('invalid archive digest: ' + field)
    if entry['tags'] != sorted(entry['tags']) or entry['keywords'] != entry['tags']:
        raise ValueError('tags and keywords must be identical and sorted')
    if entry['categoryAliases'] != sorted(entry['categoryAliases']):
        raise ValueError('category aliases must be sorted')
    if entry.get('evidence_basis') == 'sanitized-summary-sha256':
        observed = hashlib.sha256(entry['test_summary'].encode('utf-8')).hexdigest()
        if entry['evidence_sha256'] != observed:
            raise ValueError('sanitized evidence summary hash mismatch')
    code = Path(entry['code'])
    suffix = code.suffix
    if LANGUAGES.get(suffix) != entry['programmingLanguage']:
        raise ValueError('programming language does not match code extension')
    filename = code_filename(entry['name'], entry['revision'], entry['code_sha256'], suffix)
    expected = ['solutions', *entry['categories'], entry['name'], 'revisions',
                str(entry['revision']).zfill(4), filename]
    if entry['code'] != '/'.join(expected):
        raise ValueError('code path does not match category and revision')
    return True


def build_index(entries, relationships=None):
    """Create the canonical archive index from current records, in stable order.

    Omitting relationships preserves the original v2 format. Supplying a list,
    even an empty one, creates v3 and canonically sorts its file relationships.
    Unknown per-entry fields remain opaque. Historical file existence is not checked.
    """
    entries = sorted(entries, key=lambda item: item['name'])
    categories = [list(parts) for parts in sorted({tuple(item['categories']) for item in entries})]
    tags = sorted({tag for item in entries for tag in item['tags']})
    result = {'format': 'boosterai-index-v2', 'entries': entries, 'categories': categories, 'tags': tags}
    if relationships is not None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        validate_schema(relationships, schema['$defs']['relationships'], schema)
        result['format'] = 'boosterai-index-v3'
        result['relationships'] = sorted(relationships, key=lambda item: (item['file'], item['relatedFile']))
    validate_index(result)
    return result


def validate_index(index):
    """Apply the bundled JSON Schema subset and archive-specific consistency checks.

    This does not download context files, authenticate evidence or execute examples.
    It raises ValueError when metadata, names, paths or deterministic ordering disagree.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    validate_schema(index, schema)
    names, paths, records = set(), set(), {}
    for entry in index['entries']:
        validate_entry(entry, schema)
        if entry['name'] in names or entry['code'] in paths:
            raise ValueError('duplicate archive name or path')
        names.add(entry['name']); paths.add(entry['code'])
        records[(tuple(entry['categories']), entry['name'])] = entry
    if [e['name'] for e in index['entries']] != sorted(names):
        raise ValueError('archive entries are not sorted')
    if index['categories'] != [list(p) for p in sorted({tuple(e['categories']) for e in index['entries']})]:
        raise ValueError('archive category paths are not canonical')
    if index['tags'] != sorted({tag for e in index['entries'] for tag in e['tags']}):
        raise ValueError('archive tags are not canonical')
    pairs, relationship_keys = set(), []
    for relation in index.get('relationships', []):
        for path in (relation['file'], relation['relatedFile']):
            identity = archive_file_identity(path)
            record = records.get(identity[:2])
            if (record is None or identity[2] > record['revision']
                    or (identity[2] == record['revision'] and path != record['code'])):
                raise ValueError('relationship endpoint is not an indexed archive revision')
        if relation['file'] == relation['relatedFile']:
            raise ValueError('a file cannot be related to itself')
        pair = tuple(sorted((relation['file'], relation['relatedFile'])))
        if pair in pairs:
            raise ValueError('duplicate or conflicting relationship pair')
        pairs.add(pair)
        relationship_keys.append((relation['file'], relation['relatedFile']))
    if relationship_keys != sorted(relationship_keys):
        raise ValueError('relationships must be sorted and unique')
    return index


def related_links(index, code_or_entry):
    """Return a canonical, bidirectional view of the relationships for an archive file.

    A path selects exactly that revision; a current entry selects all revisions of
    its record. `relation` is relative to the returned `file`: `derived-from`,
    `source-for`, or `related`. V1 and v2 indexes have no links and return an empty list.
    This is metadata-only: historical file existence must be checked by the writer.
    """
    if not isinstance(index, dict):
        raise ValueError('invalid archive index')
    if index.get('format') == 'boosterai-index-v1':
        return []
    validate_index(index)
    return _related_links_from_validated_index(index, code_or_entry)


def _related_links_from_validated_index(index, code_or_entry):
    """Project canonical links from an index already validated by the reader."""
    if index.get('format') == 'boosterai-index-v2':
        return []
    if isinstance(code_or_entry, dict):
        current = next((entry for entry in index['entries']
                        if entry['name'] == code_or_entry.get('name')
                        and entry['code'] == code_or_entry.get('code')), None)
        if current is None:
            raise ValueError('archive entry is not in the index')
        selected = (tuple(current['categories']), current['name'])
        matches = lambda path: archive_file_identity(path)[:2] == selected
    elif isinstance(code_or_entry, str):
        archive_file_identity(code_or_entry)
        matches = lambda path: path == code_or_entry
    else:
        raise ValueError('expected an indexed entry or archive file path')
    links = []
    for relation in index['relationships']:
        source, target = relation['file'], relation['relatedFile']
        if matches(source):
            links.append({'file': source, 'relatedFile': target, 'strength': relation['strength'],
                          'relation': 'derived-from' if relation['derivedFrom'] else 'related'})
        if matches(target):
            links.append({'file': target, 'relatedFile': source, 'strength': relation['strength'],
                          'relation': 'source-for' if relation['derivedFrom'] else 'related'})
    return sorted(links, key=lambda link: (link['file'], link['relatedFile'], link['relation']))
