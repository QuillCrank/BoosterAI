"""Read one narrow Mermaid control-flow schema without running its actions.

The supported source is one triple-backtick ``mermaid`` block in Markdown,
containing ``flowchart TD`` and one plain directed edge per nonblank line.
Rectangles define actions, braces define decisions, and only decision edges
may have ``|label|`` branches. Unsupported syntax is rejected rather than
silently ignored. Returned nodes and edges are a control-flow inventory, not an
executable dependency DAG or proof that any action or acceptance test ran.
"""

import hashlib
from io import StringIO
import re
import unicodedata


_MAX_DOCUMENT_CHARS = 4 * 1024 * 1024
_MAX_NODES = 256
_MAX_EDGES = 1024
_MAX_LABEL_CHARS = 512
_MAX_LINE_CHARS = 2048
_IDENTIFIER = r"[A-Za-z][A-Za-z0-9_]{0,63}"
_TEXT = r'[^"\\\[\]{}|<>\r\n]+'
_ENDPOINT = rf"{_IDENTIFIER}(?:\[{_TEXT}\]|\{{{_TEXT}\}})?"
_EDGE = re.compile(rf"^\s*(?P<left>{_ENDPOINT})\s*-->(?:\|(?P<branch>{_TEXT})\|)?\s*(?P<right>{_ENDPOINT})\s*$")
_NODE = re.compile(rf"^(?P<id>{_IDENTIFIER})(?:\[(?P<action>{_TEXT})\]|\{{(?P<decision>{_TEXT})\}})?$")
_FENCE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>`{3,}|~{3,})(?P<rest>[^\r\n]*)$")
_MERMAID_INFO = re.compile(r"^mermaid(?:[ \t]|$)", re.IGNORECASE)
_CONTAINER_PREFIX = r"[ \t]*(?:(?:>[ \t]*)|(?:(?:[-+*]|[0-9]+[.)])[ \t]+))*"
_CONTAINER_MERMAID = re.compile(
    rf"^{_CONTAINER_PREFIX}(?:`{{3,}}|~{{3,}})[ \t]*mermaid(?:[ \t]|$)",
    re.IGNORECASE,
)
_RAW_HTML = re.compile(
    rf"^{_CONTAINER_PREFIX}<(?:[A-Za-z][A-Za-z0-9-]*(?:[ \t/>]|$)|[!?/])",
    re.IGNORECASE,
)
_ENTITY_ESCAPE = re.compile(r"#(?:[0-9]+|[A-Za-z][A-Za-z0-9]*);")


def _require(condition, message):
    """Raise ValueError for an unsupported or incomplete source condition.

    ``condition`` is truth-tested and ``message`` identifies the failure.
    Return None if valid; do not read or modify external state.
    """
    if not condition:
        raise ValueError(message)


def _label(value, name):
    """Validate and normalize one explicit node or decision-branch label.

    ``value`` must contain nonempty plain text at most 512 characters long;
    ``name`` labels errors. Leading/trailing whitespace, Unicode category Cc
    control characters and Mermaid entity escapes such as ``#35;`` or
    ``#quot;`` are rejected instead of silently changing the source. Return the
    text as supplied, without reading files or interpreting it as executable.
    """
    _require(isinstance(value, str) and bool(value) and value == value.strip()
             and len(value) <= _MAX_LABEL_CHARS
             and not any(unicodedata.category(character) == "Cc" for character in value)
             and not _ENTITY_ESCAPE.search(value),
             "invalid " + name)
    return value


def _diagram(document):
    """Extract the one exactly spelled Mermaid fenced block from Markdown text.

    ``document`` must be a string no longer than the fixed input bound. Return
    the exact inner block text, preserving original line endings for hashing.
    Recognize enclosing ordinary code fences before considering Mermaid fences,
    so example text inside another fenced block is never treated as a graph.
    A missing, repeated, unsupported, ambiguous or unterminated fence raises
    ValueError. HTML blocks and Mermaid fences inside list or quote containers
    are rejected rather than interpreted. This is a narrow Markdown profile, not
    a complete CommonMark implementation; it performs no filesystem access or
    modification.
    """
    _require(isinstance(document, str) and len(document) <= _MAX_DOCUMENT_CHARS,
             "invalid or oversized Markdown document")
    state = None
    captured = []
    content = []
    # Recognize CR/LF endings and retain them for hashing.
    for line in StringIO(document, newline=""):
        text = line.rstrip("\r\n")
        match = _FENCE.fullmatch(text)
        if state is None:
            _require("<!--" not in text, "unsupported Markdown HTML comment context")
            _require(not _RAW_HTML.match(text), "unsupported Markdown HTML block")
            if not match:
                _require(not _CONTAINER_MERMAID.match(text), "unsupported Mermaid container")
            if not match:
                continue
            indent, marker, rest = (match.group(name) for name in ("indent", "marker", "rest"))
            _require("\t" not in indent and len(indent) <= 3, "ambiguous Markdown fence")
            _require(marker[0] != "`" or "`" not in rest, "ambiguous Markdown fence")
            info = rest.strip()
            mermaid = bool(_MERMAID_INFO.match(info))
            if mermaid:
                _require(text == "```mermaid", "unsupported Mermaid fence")
                content = []
            state = {"marker": marker, "mermaid": mermaid}
            continue
        if match:
            indent, marker, rest = (match.group(name) for name in ("indent", "marker", "rest"))
            opening = state["marker"]
            closes = (marker[0] == opening[0] and len(marker) >= len(opening)
                      and not rest.strip())
            if closes:
                _require("\t" not in indent and len(indent) <= 3, "ambiguous Markdown fence")
                if state["mermaid"]:
                    _require(text == "```", "unsupported Mermaid closing fence")
                    captured.append("".join(content))
                state = None
                continue
        if state["mermaid"]:
            content.append(line)
    _require(state is None, "unterminated Markdown fence")
    _require(len(captured) == 1, "exactly one Mermaid block is required")
    return captured[0]


def _endpoint(value, definitions, mentioned):
    """Collect an edge endpoint and its optional explicit node definition.

    ``value`` is a lexical endpoint, ``definitions`` stores defined nodes, and
    ``mentioned`` records IDs referenced by edges. Return the identifier and
    update only those caller-owned in-memory collections. Unsupported endpoints,
    invalid labels or a repeated explicit definition raise ValueError; a bare
    reference is checked against all definitions once the diagram is parsed.
    """
    match = _NODE.fullmatch(value)
    _require(match is not None, "unsupported node syntax")
    identifier = match.group("id")
    mentioned.add(identifier)
    action, decision = match.group("action"), match.group("decision")
    if action is not None or decision is not None:
        _require(identifier not in definitions, "duplicate node definition: " + identifier)
        text = action if action is not None else decision
        definitions[identifier] = {"id": identifier, "label": _label(text, "node label"),
                                   "kind": "action" if action is not None else "decision"}
    return identifier


def _cyclic_components(node_ids, edges):
    """Find strongly connected components that actually contain a cycle.

    ``node_ids`` is the complete ID set and ``edges`` is the validated edge
    list. Return sorted components, each containing sorted node IDs. A singleton
    is cyclic only when it has an explicit self-edge. This uses only in-memory
    source data; it does not unroll loops or establish their safety or budgets.
    """
    adjacent = {node: set() for node in node_ids}
    for edge in edges:
        adjacent[edge["from"]].add(edge["to"])
    index = 0
    indices = {}
    low = {}
    stack = []
    on_stack = set()
    components = []

    def visit(node):
        """Update Tarjan traversal state for one bounded control-flow node."""
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for successor in sorted(adjacent[node]):
            if successor not in indices:
                visit(successor)
                low[node] = min(low[node], low[successor])
            elif successor in on_stack:
                low[node] = min(low[node], indices[successor])
        if low[node] == indices[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            component.sort()
            if len(component) > 1 or node in adjacent[node]:
                components.append(component)

    for node in sorted(node_ids):
        if node not in indices:
            visit(node)
    return sorted(components)


def parse_mermaid(document):
    """Parse a narrowly supported real Mermaid graph into a complete inventory.

    ``document`` is the exact Markdown text from one explicitly chosen source.
    Return its UTF-8 SHA-256, the exact fenced diagram's UTF-8 SHA-256, sorted
    node/edge inventories, entry/terminal IDs and cyclic strongly connected
    components. Node IDs must be explicitly defined once; decisions require at
    least two branch labels distinct under canonical, case-insensitive comparison,
    while actions have at most one unlabeled outgoing edge. Undefined nodes,
    duplicate definitions, edges or branch labels, and unsupported syntax or
    bounds raise ValueError. This function reads no files, executes nothing
    and does not infer branch exhaustiveness, loop budgets, semantic atomicity,
    authorization, or successful test evidence.
    """
    diagram = _diagram(document)
    # Keep statement boundaries limited to physical CR/LF line endings.
    _require(re.search(r"[\v\f\x1c-\x1e\x85\u2028\u2029]", diagram) is None,
             "unsupported Mermaid line separator")
    lines = iter(diagram.splitlines())
    header = next(lines, "")
    _require(len(header) <= _MAX_LINE_CHARS, "diagram line length limit exceeded")
    _require(header.strip() == "flowchart TD", "expected flowchart TD")
    definitions = {}
    mentioned = set()
    edges = []
    edge_keys = set()
    for line in lines:
        _require(len(line) <= _MAX_LINE_CHARS, "diagram line length limit exceeded")
        if not line.strip():
            continue
        match = _EDGE.fullmatch(line)
        _require(match is not None, "unsupported Mermaid edge syntax")
        source = _endpoint(match.group("left"), definitions, mentioned)
        target = _endpoint(match.group("right"), definitions, mentioned)
        branch = match.group("branch")
        if branch is not None:
            branch = _label(branch, "branch label")
        key = (source, target, branch)
        _require(key not in edge_keys, "duplicate edge")
        edge_keys.add(key)
        edges.append({"from": source, "to": target, "label": branch})
        _require(len(edges) <= _MAX_EDGES and len(mentioned) <= _MAX_NODES,
                 "diagram size limit exceeded")
    _require(edges and mentioned == set(definitions), "missing edge or undefined node")
    outgoing = {identifier: [] for identifier in mentioned}
    incoming = {identifier: [] for identifier in mentioned}
    for edge in edges:
        outgoing[edge["from"]].append(edge)
        incoming[edge["to"]].append(edge)
    for identifier in sorted(mentioned):
        claims = outgoing[identifier]
        if definitions[identifier]["kind"] == "action":
            _require(len(claims) <= 1 and all(edge["label"] is None for edge in claims),
                     "action must have at most one unlabeled outgoing edge")
        else:
            _require(len(claims) >= 2 and all(edge["label"] is not None for edge in claims),
                     "decision requires at least two labeled branches")
            # Case folding can change the normalization form. Compare normalized
            # keys while retaining the original labels in the graph.
            keys = [
                unicodedata.normalize("NFC", unicodedata.normalize("NFD", edge["label"]).casefold())
                for edge in claims
            ]
            _require(len(keys) == len(set(keys)), "duplicate decision branch")
    nodes = [definitions[identifier] for identifier in sorted(definitions)]
    ordered_edges = sorted(edges, key=lambda edge: (edge["from"], edge["to"], edge["label"] or ""))
    return {
        "format": "boosterai-mermaid-td-v1",
        "source_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "diagram_sha256": hashlib.sha256(diagram.encode("utf-8")).hexdigest(),
        "nodes": nodes,
        "edges": ordered_edges,
        "entry_nodes": sorted(identifier for identifier in mentioned if not incoming[identifier]),
        "terminal_nodes": sorted(identifier for identifier in mentioned if not outgoing[identifier]),
        "cyclic_components": _cyclic_components(mentioned, edges),
    }
