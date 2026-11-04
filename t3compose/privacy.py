"""Read-only publication screening of every selected commit and supplied text."""
import os
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit
import re
import stat
from collections.abc import Mapping, Sequence

from . import cli, identifier_list, jsonio, process, records

MAX_BYTES = identifier_list.MAX_TEXT_BYTES
TIMEOUT_SECONDS = 30
_SHA = re.compile(r"[0-9a-f]{40}")
_REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*(?:[~^][0-9]*)*")


def _fail(message):
    raise cli.CommandError(message) from None


def _text(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES or b"\0" in raw:
        _fail("privacy input must be bounded UTF-8 text without NUL")
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        _fail("privacy input must be bounded UTF-8 text without NUL")


def _git(repo, *arguments, input=None, allow_detached=False):
    # Ignore ambient repository selectors, replacement objects and lazy fetches.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
               GIT_GRAFT_FILE=os.devnull, GIT_OPTIONAL_LOCKS="0",
               GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="")
    try:
        result = process.run(["git", "--no-optional-locks", *arguments], cwd=repo,
                             env=env, input=input, capture_output=True, check=False,
                             timeout=TIMEOUT_SECONDS)
    except (process.ProcessError, OSError, ValueError):
        _fail("privacy Git read failed")
    code, data = getattr(result, "returncode", None), getattr(result, "stdout", None)
    if type(code) is not int or code not in ({0, 1} if allow_detached else {0}):
        _fail("privacy Git read failed")
    if code == 1:
        _fail("privacy requires --branch for a detached head")
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        _fail("privacy Git response is invalid or exceeds byte limit")
    return data


def _resolve(repo, ref):
    # Ref names go on stdin, never argv. Peel tags and require a commit.
    raw = _git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)",
               input=(ref + "^{commit}\n").encode("utf-8"))
    if not re.fullmatch(rb"[0-9a-f]{40} commit\n", raw):
        _fail("privacy range endpoint is not a commit")
    return raw[:40].decode("ascii")


def _object(repo, kind, sha):
    size = _git(repo, "cat-file", "-s", sha)
    if not re.fullmatch(rb"[0-9]{1,10}\n", size) or int(size) > MAX_BYTES:
        _fail("privacy object exceeds byte limit or has invalid size")
    raw = _git(repo, "cat-file", kind, sha)
    if len(raw) != int(size):
        _fail("privacy object size differs from its contents")
    return raw


def _file(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail("privacy text file is not a regular file")
            return _text(stream.read(MAX_BYTES + 1))
    except (OSError, ValueError):
        _fail("privacy text file is unreadable")


def check(args):
    value = args.range
    if not isinstance(value, str) or value.count("..") != 1:
        _fail("privacy requires a BASE..HEAD range")
    refs = value.split("..")
    if any(not _REF.fullmatch(ref) or ref.endswith(".") or ref.startswith(".") for ref in refs):
        _fail("privacy range endpoint is invalid")
    if not args.repo_path or "\0" in args.repo_path:
        _fail("privacy repository path is invalid")
    texts = []
    for spec in args.text_file:
        label, sep, path = spec.partition("=")
        if not sep or not label or not path or "\0" in spec:
            _fail("privacy text file requires LABEL=FILE")
        texts.append((label, path))
    repo = args.repo_path
    _repository(repo)
    base, head = (_resolve(repo, ref) for ref in refs)
    raw = _git(repo, "rev-list", base + ".." + head, "--")
    commits = _text(raw).split("\n") if raw else []
    if commits:
        if commits.pop() != "" or any(not _SHA.fullmatch(sha) for sha in commits):
            _fail("privacy commit enumeration is invalid")
    branch = args.branch
    if branch is None:
        raw = _git(repo, "symbolic-ref", "--quiet", "HEAD", allow_detached=True)
        prefix = b"refs/heads/"
        if not raw.startswith(prefix) or not raw.endswith(b"\n"):
            _fail("privacy branch response is invalid")
        branch = _text(raw[len(prefix):-1])
    data = scan_commits(repo, args.local_dir, commits, base=base, head=head,
                        branch=branch, texts=[(label, _file(path)) for label, path in texts],
                        privacy_policy=getattr(args, "privacy_policy", None))
    return cli.Outcome(data, cli.ExitCode.CHECK_FAILED) if not data["ok"] else data


def _repository(repo):
    if _git(repo, "rev-parse", "--show-object-format") != b"sha1\n":
        _fail("privacy supports SHA-1 repositories only")
    if _git(repo, "rev-parse", "--is-shallow-repository") != b"false\n":
        _fail("privacy requires complete repository history")


def _decoded(value):
    if not isinstance(value, str):
        _fail("privacy input must be decoded text")
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        _fail("privacy input must be bounded UTF-8 text without NUL")
    return _text(raw)


def _sequence(values):
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        _fail("privacy input must be an ordered sequence")
    return tuple(values)


def _commit_id(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail("privacy requires full lowercase SHA-1 commit IDs")
    return value


def scan_commits(repo, local_dir, commits, *, base, head, branch, texts=(), privacy_policy=None):
    """Screen exactly the ordered SHA-1 commits and labeled decoded texts.

    The trusted caller supplies the complete selection; base/head describe its
    immutable endpoints, not a request to recompute BASE..HEAD. A raw clean
    report covers only these inputs and grants no publication permission.
    Invalid or incomplete input raises CommandError without a partial report.
    """
    patterns = identifier_list.load_identifiers(local_dir)
    if not isinstance(repo, (str, os.PathLike)):
        _fail("privacy repository path is invalid")
    try:
        path = os.fsdecode(repo)
    except (TypeError, ValueError):
        _fail("privacy repository path is invalid")
    if not path or "\0" in path:
        _fail("privacy repository path is invalid")
    _repository(repo)
    base, head = _commit_id(base), _commit_id(head)
    commits = _sequence(commits)
    for sha in commits:
        _commit_id(sha)
    if len(set(commits)) != len(commits):
        _fail("privacy commit selection contains duplicates")
    if len(commits) * 41 > MAX_BYTES:
        _fail("privacy commit selection exceeds byte limit")
    # Do not peel tags or accept refs here. Verify object identity and type,
    # including endpoints and empty selections, using immutable IDs on stdin.
    for sha in dict.fromkeys((base, head, *commits)):
        raw = _git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)",
                   input=(sha + "\n").encode("ascii"))
        if raw != (sha + " commit\n").encode("ascii"):
            _fail("privacy selected object is not a commit")
    branch = _decoded(branch)
    if not branch:
        _fail("privacy branch is empty")
    pairs = []
    for pair in _sequence(texts):
        pair = _sequence(pair)
        if len(pair) != 2:
            _fail("privacy text requires a label and decoded text")
        label, text = (_decoded(value) for value in pair)
        if not label:
            _fail("privacy text label is empty")
        pairs.append((label, text))
    findings = []
    coverage = None
    if privacy_policy is not None:
        snapshot = load_artifact_policy(repo, local_dir, privacy_policy, base=base, patterns=patterns)
        inventory = _artifact_inventory(repo, snapshot)
        coverage = dict(version='artifact-coverage/v1', repository=snapshot['repository'],
            base=base, head=head, commits=list(commits), selectionDigest=_canonical_digest(list(commits)),
            branchDigest=_digest(branch.encode('utf-8')),
            texts=[dict(labelDigest=_digest(label.encode('utf-8')), textDigest=_digest(text.encode('utf-8')))
                   for label, text in pairs],
            provenance={k: v for k, v in snapshot.items() if k not in {'manifest', 'base', 'repository'}},
            screened=[], approvedUnscanned=[], excluded=[])
        coverage['provenance']['baseline'] = snapshot['manifest']['baseline']

    def match(text):
        return identifier_list.match_identifiers(patterns, text)

    def safe(value):
        return "[redacted]" if value is not None and match(value) else value

    def screen(field, text, commit=None, file=None, label=None):
        if coverage is not None:
            coverage['screened'].append(dict(field=field, commit=commit, file=file, label=label,
                                              contentDigest=_digest(text.encode('utf-8'))))
        lines = match(text)
        if lines:
            findings.append({"field": field, "commit": safe(commit), "file": safe(file),
                             "label": safe(label), "lines": lines})

    screen("branch", branch)
    for label, text in pairs:
        screen("label", label, label=label)
        screen("text", text, label=label)
    for sha in commits:
        raw = _object(repo, "commit", sha)
        _, separator, message = raw.partition(b"\n\n")
        if not separator:
            _fail("privacy commit has no message separator")
        screen("message", _text(message), commit=sha)
        if coverage is not None:
            _scan_artifact_tree(repo, sha, raw, inventory, snapshot, coverage, screen)
            continue
        tree = _git(repo, "ls-tree", "-r", "-t", "-z", "--full-tree", sha, "--")
        if tree and not tree.endswith(b"\0"):
            _fail("privacy tree response is invalid")
        for entry in tree[:-1].split(b"\0") if tree else []:
            metadata, tab, path = entry.partition(b"\t")
            if not tab or not path:
                _fail("privacy tree entry is invalid")
            filename = _text(path)
            if filename.endswith(".lock"):
                continue
            if not re.fullmatch(rb"(?:(?:100644|100755|120000) blob|040000 tree) [0-9a-f]{40}", metadata):
                _fail("privacy tree contains an unsupported entry")
            screen("path", filename, commit=sha, file=filename)
            if metadata.startswith(b"040000 tree "):
                continue
            blob = metadata[-40:].decode("ascii")
            screen("file", _text(_object(repo, "blob", blob)), commit=sha, file=filename)
    data = {"ok": not findings, "commits": len(commits), "findings": findings,
            "action": "publication blocked; Corrections or Shawn must resolve matches" if findings else None}
    if coverage is not None:
        if coverage['approvedUnscanned']:
            data.update(ok=False, action='policy-evaluation-required')
        # Every dynamic output string uses the same captured matcher, including
        # object IDs/digests. Bindings remain available privately by re-running.
        def redacted(value):
            if isinstance(value, str):
                return safe(value)
            if isinstance(value, list):
                return [redacted(v) for v in value]
            if isinstance(value, Mapping):
                return {k: redacted(v) for k, v in value.items()}
            return value
        data['coverage'] = redacted(coverage)
    return data


def _artifact_tree(repo, sha):
    """Read exact immutable tree facts; filenames never enter process argv."""
    raw = _git(repo, 'ls-tree', '-r', '-t', '-z', '--full-tree', sha, '--')
    _require_policy(not raw or raw.endswith(b'\0'))
    result = {}
    for entry in raw[:-1].split(b'\0') if raw else []:
        metadata, tab, path = entry.partition(b'\t')
        _require_policy(bool(tab) and bool(path))
        name = _text(path)
        _require_policy(name not in result)
        if name.endswith('.lock'):
            result[name] = None
            continue
        _require_policy(re.fullmatch(rb'(?:(?:100644|100755|120000) blob|040000 tree) [0-9a-f]{40}', metadata))
        mode, kind, oid = metadata.decode('ascii').split(' ')
        result[name] = (mode, kind, oid)
    return result


def _artifact_blob_size(repo, oid):
    raw = _git(repo, 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)',
               input=(oid+'\n').encode('ascii'))
    _require_policy(re.fullmatch((oid+r' blob [0-9]{1,19}\n').encode('ascii'), raw))
    size = int(raw.split()[2])
    _require_policy(size <= 2**63 - 1)
    return size


def _artifact_inventory(repo, snapshot):
    inventory = {entry['path']: entry for entry in snapshot['manifest']['entries']}
    for sha in dict.fromkeys((snapshot['manifest']['baseline'], snapshot['base'])):
        tree = _artifact_tree(repo, sha)
        for name, entry in inventory.items():
            _require_policy(tree.get(name) == ('100644', 'blob', entry['blobOid']))
            _require_policy(_artifact_blob_size(repo, entry['blobOid']) == entry['size'])
    return inventory


def _artifact_parents(repo, raw):
    headers, separator, _ = raw.partition(b'\n\n')
    _require_policy(bool(separator))
    parents = []
    for line in headers.split(b'\n'):
        if line.startswith(b'parent '):
            _require_policy(re.fullmatch(rb'parent [0-9a-f]{40}', line))
            parent = line[7:].decode('ascii')
            _require_policy(parent not in parents)
            fact = _git(repo, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                        input=(parent+'\n').encode('ascii'))
            _require_policy(fact == (parent+' commit\n').encode('ascii'))
            parents.append(parent)
    return parents


def _scan_artifact_tree(repo, sha, raw, inventory, snapshot, coverage, screen):
    tree = _artifact_tree(repo, sha)
    # Parents are facts for eligibility, never additional selected scan coverage.
    parent_trees = None
    for name, fact in tree.items():
        if fact is None:
            coverage['excluded'].append(dict(commit=sha, file=name, reason='lock-suffix'))
            continue
        mode, kind, oid = fact
        screen('path', name, commit=sha, file=name)
        if kind == 'tree':
            continue
        size = _artifact_blob_size(repo, oid)
        text = None
        if size <= MAX_BYTES:
            body = _object(repo, 'blob', oid)  # Read failures must propagate.
            if b'\0' not in body:
                try:
                    text = body.decode('utf-8')
                except UnicodeError:
                    pass
        if text is not None:
            screen('file', text, commit=sha, file=name)
            continue
        entry = inventory.get(name)
        _require_policy(entry is not None and mode == '100644' and
                        oid == entry['blobOid'] and size == entry['size'])
        if parent_trees is None:
            parents = _artifact_parents(repo, raw)
            _require_policy(bool(parents))
            parent_trees = [_artifact_tree(repo, parent) for parent in parents]
        for parent in parent_trees:
            _require_policy(parent.get(name) == fact)
            # Exact OID binds the size already checked against the inventory.
        coverage['approvedUnscanned'].append(dict(commit=sha, file=name, mode=mode,
            blobOid=oid, size=size, receiptId=snapshot['receiptId'],
            contextDigest=snapshot['contextDigest'], contentScreened=False))


# Cooperative provenance foundation, consumed only by explicit scanner opt-in.
POLICY_VERSION = 'artifact-provenance/v1'
_POLICY_DESCRIPTOR = dict(policyVersion=POLICY_VERSION, schemaVersion=1,
                          objectFormat='sha1', authority='owner',
                          verification='git-object-inventory', contextVersion=1)


def _digest(value):
    return 'sha256:' + hashlib.sha256(value).hexdigest()


def _canonical_digest(value):
    return _digest(jsonio.canonical_json(value))


def _policy_identifiers(patterns):
    return dict(policyVersion=POLICY_VERSION, policyDigest=_canonical_digest(_POLICY_DESCRIPTOR),
                listDigest=_canonical_digest(list(patterns)), patternCount=len(patterns))


def prepare_policy_identifiers(local_dir):
    """Freshly validate the private list; return counts/digests, never patterns."""
    return _policy_identifiers(identifier_list.load_identifiers(local_dir))


def _require_policy(condition):
    if not condition:
        _fail('invalid artifact provenance input')


def _policy_text(value):
    _require_policy(isinstance(value, str) and bool(value.strip()))
    _require_policy('\0' not in value)
    value.encode('utf-8')
    return value


def _policy_path(value):
    _policy_text(value)
    _require_policy(not any(c in value for c in '\\*?[]') and
                    all(p not in {'', '.', '..'} for p in value.split('/')))
    return value


def _policy_segment(value):
    _policy_text(value)
    _require_policy(len(value) <= 128 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value))


def _policy_digest(value):
    _require_policy(isinstance(value, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', value))


def _closed(value, keys, *, version=True):
    _require_policy(type(value) is dict and set(value) == set(keys.split()))
    if version:
        _require_policy(type(value['schemaVersion']) is int and value['schemaVersion'] == 1)


def _json_tree(value, depth=0):
    _require_policy(depth <= 32)
    if isinstance(value, str):
        _require_policy('\0' not in value)
        value.encode('utf-8')
    elif isinstance(value, (list, dict)):
        for item in (value.items() if isinstance(value, dict) else value):
            if isinstance(value, dict):
                _json_tree(item[0], depth + 1)
                _json_tree(item[1], depth + 1)
            else:
                _json_tree(item, depth + 1)


def _policy_snapshot(path):
    # Pin the descriptor before testing type. No FIFO open/read can block.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        stream = os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)  # fdopen has not taken ownership when setup fails.
        raise
    with stream:
        _require_policy(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
        raw = stream.read(MAX_BYTES + 1)
    _require_policy(len(raw) <= MAX_BYTES)
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=jsonio._unique_object,
                       parse_constant=jsonio._invalid_constant, parse_float=jsonio._parse_float,
                       parse_int=jsonio._parse_int)
    _json_tree(value)
    return value, _digest(raw)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _authority(local_dir, repository, receipt, source, context):
    loaded = records.load_work(local_dir, repository, receipt['authorityWorkId'])
    events = loaded['events']
    seq = receipt['approvalSeq']
    _require_policy(type(seq) is int and 1 <= seq <= len(events))
    expected = 'privacy-artifact/v1 approve ' + receipt['receiptId'] + ' ' + context
    approval = events[seq - 1]
    _require_policy(approval['kind'] == 'decision' and approval['data'] == dict(
        question=expected, answer=source['quote'], date=source['date'], decidedBy='owner'))
    for event in events:
        if event['kind'] != 'decision':
            continue
        data = event['data']
        question = data['question']
        if not question.startswith('privacy-artifact'):
            continue
        match = re.fullmatch(r'privacy-artifact/v1 (approve|revoke) '
                             r'([A-Za-z0-9][A-Za-z0-9._-]{0,127}) (sha256:[0-9a-f]{64})', question)
        _require_policy(match is not None and data['decidedBy'] == 'owner' and
                        isinstance(data['answer'], str) and bool(data['answer'].strip()) and
                        isinstance(data['date'], str))
        action, identity, bound = match.groups()
        if identity == receipt['receiptId']:
            # A revoke is terminal even when it names an earlier full context.
            _require_policy(action != 'revoke' and bound == context)
    return dict(authorityEventCount=len(events), authorityCheckpoint=records.checkpoint(
        loaded['work'], events), authorityWorkDigest=_canonical_digest(loaded['work']),
        authorityEventsDigest=_canonical_digest(events))


def _load_artifact_policy(repo, local_dir, policy_path, *, base, patterns):
    started = datetime.now(timezone.utc).isoformat()
    # The trusted caller supplies the same validated snapshot used by its matcher.
    _require_policy(isinstance(patterns, (list, tuple)))
    patterns = tuple(patterns)
    identifier_list.match_identifiers(patterns, '')
    identifiers = _policy_identifiers(patterns)
    policy_path = Path(policy_path)
    policy, policy_raw_digest = _policy_snapshot(policy_path)
    _closed(policy, 'schemaVersion policyVersion manifestFile manifestDigest receiptFile receiptDigest authorityWorkId')
    _require_policy(policy['policyVersion'] == POLICY_VERSION)
    _policy_segment(policy['authorityWorkId'])

    def referenced(document, kind):
        name, digest = document[kind + 'File'], document[kind + 'Digest']
        _policy_path(name)
        _policy_digest(digest)
        value, observed = _policy_snapshot(policy_path.parent / name)
        _require_policy(observed == digest)
        return value

    manifest, receipt = referenced(policy, 'manifest'), referenced(policy, 'receipt')
    _closed(manifest, 'schemaVersion repository objectFormat baseline entries')
    _closed(receipt, 'schemaVersion receiptId repository baseline manifestDigest policyVersion policyDigest '
                     'listDigest authorityWorkId approvalSeq sourceFile sourceDigest verificationFile verificationDigest')
    _policy_segment(receipt['receiptId'])
    _require_policy(receipt['authorityWorkId'] == policy['authorityWorkId'])
    for key in ('policyVersion', 'policyDigest', 'listDigest'):
        _require_policy(receipt[key] == identifiers[key])
    _require_policy(receipt['manifestDigest'] == policy['manifestDigest'])
    _repository(repo)
    # Only the hardened seam reads origin; normalization is pure parsing.
    origin = _text(_git(repo, 'config', '--get', 'remote.origin.url'))
    _require_policy(origin.endswith('\n') and origin.count('\n') == 1)
    origin = origin[:-1]
    if '://' in origin:
        url = urlsplit(origin)
        _require_policy(url.scheme in {'https', 'ssh'} and url.hostname and not url.query
                        and not url.fragment and url.password is None and url.port is None)
        identity = url.hostname.lower() + '/' + url.path.removeprefix('/').removesuffix('.git')
    else:
        _require_policy(re.fullmatch(r'[^/@:]+@[^/:]+:.+', origin))
        host, path = origin.split('@', 1)[1].split(':', 1)
        identity = host.lower() + '/' + path.removesuffix('.git')
    repository = records.canonical_identity(identity)
    _require_policy(manifest['repository'] == repository == receipt['repository'] and
                    manifest['objectFormat'] == 'sha1')
    baseline = _commit_id(manifest['baseline'])
    base = _commit_id(base)
    _require_policy(receipt['baseline'] == baseline)
    for sha in dict.fromkeys((baseline, base)):
        _require_policy(_git(repo, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                            input=(sha+'\n').encode('ascii')) == (sha+' commit\n').encode('ascii'))
    _require_policy(_git(repo, 'merge-base', baseline, base) == (baseline+'\n').encode('ascii'))
    entries = manifest['entries']
    _require_policy(type(entries) is list and 1 <= len(entries) <= 4096)
    paths = set()
    for entry in entries:
        _closed(entry, 'path mode blobOid size', version=False)
        path = _policy_path(entry['path'])
        _require_policy(path not in paths and entry['mode'] == '100644')
        paths.add(path)
        _commit_id(entry['blobOid'])
        _require_policy(type(entry['size']) is int and 0 <= entry['size'] <= 2**63 - 1)
    keys = ('receiptId', 'repository', 'baseline', 'manifestDigest', 'policyVersion',
            'policyDigest', 'listDigest', 'authorityWorkId')
    context = dict(schemaVersion=1, **{k: receipt[k] for k in keys})
    inventory_context = _canonical_digest(context)
    source, verification = referenced(receipt, 'source'), referenced(receipt, 'verification')
    _closed(source, 'schemaVersion receiptId contextDigest sourceRef date quote')
    _closed(verification, 'schemaVersion receiptId contextDigest verifier sourceRef method '
                          'baselineEvidence inventoryDigest result')
    for evidence in (source, verification):
        _require_policy(evidence['receiptId'] == receipt['receiptId'] and
                        evidence['contextDigest'] == inventory_context)
        _policy_text(evidence['sourceRef'])
    _policy_text(source['quote'])
    _policy_text(source['date'])
    _require_policy(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', source['date']) and
                    date.fromisoformat(source['date']).isoformat() == source['date'])
    _policy_text(verification['verifier'])
    _require_policy(verification['method'] == 'git-object-inventory' and
                    verification['result'] == 'verified' and
                    verification['inventoryDigest'] == policy['manifestDigest'])
    evidence = verification['baselineEvidence']
    _closed(evidence, 'repository commit objectFormat entriesDigest', version=False)
    _require_policy(evidence == dict(repository=repository, commit=baseline, objectFormat='sha1',
                                    entriesDigest=_canonical_digest(entries)))
    context.update(sourceDigest=receipt['sourceDigest'], verificationDigest=receipt['verificationDigest'])
    full_context = _canonical_digest(context)
    authority = _authority(local_dir, repository, receipt, source, full_context)
    return _freeze(dict(manifest=manifest, receiptId=receipt['receiptId'], base=base,
        repository=repository, authorityWorkId=receipt['authorityWorkId'], approvalSeq=receipt['approvalSeq'],
        inventoryContextDigest=inventory_context, contextDigest=full_context,
        policyFileDigest=policy_raw_digest, manifestDigest=policy['manifestDigest'],
        receiptDigest=policy['receiptDigest'], sourceDigest=receipt['sourceDigest'],
        verificationDigest=receipt['verificationDigest'], **identifiers, **authority,
        observedStart=started, observedEnd=datetime.now(timezone.utc).isoformat()))


def load_artifact_policy(repo, local_dir, policy_path, *, base, patterns):
    """Validate private cooperative provenance; return immutable internal inputs.

    Never grants eligibility, completed coverage or publication permission.
    patterns must be the trusted caller's captured validated matcher sequence.
    Current authority is observed freshly, without an atomic revocation claim.
    """
    try:
        return _load_artifact_policy(repo, local_dir, policy_path, base=base, patterns=patterns)
    except (cli.CommandError, OSError, ValueError, TypeError, RecursionError, OverflowError):
        _fail('invalid artifact provenance input')


def register(registry):
    parser = registry.group("privacy").add_parser("check")
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--range", required=True)
    parser.add_argument("--branch")
    parser.add_argument("--text-file", action="append", default=[])
    parser.add_argument("--privacy-policy")
    parser.set_defaults(handler=check)


# Private evidence only. No caller of the raw scanner or publisher uses this seam.
_LITERAL_VERSION = 'literal-proof/v1'
_LITERAL_OCCURRENCES = 4096


def _literal_environment():
    return {k: v for k, v in os.environ.items()
            if k in {'LANG', 'LANGUAGE', 'GREP_OPTIONS', 'GREP_COLOR', 'GREP_COLORS'}
            or k.startswith('LC_')}


def _literal_grammar(encoded):
    """Recognize a finite nonnullable subset, never implement an ERE matcher.

    Exclude all potential overlap regardless of anchors: grep -o cannot reveal
    every overlapping alternative. Refusal may be stricter than actual matches.
    """
    rows, literals = [], []
    for pattern in encoded:
        branches = []
        for branch in pattern.decode('utf-8').split('|'):
            start, end = branch.startswith('^'), branch.endswith('$')
            literal = branch[int(start):len(branch)-int(end) if end else None]
            if not literal or any(c in r'\.^$[]()*+?{}|' or ord(c) < 32 or ord(c) == 127
                                  for c in literal):
                return None
            raw = literal.encode('utf-8')
            branches.append((raw, start, end))
            literals.append(raw)
        rows.append(branches)
    if len(literals) > 64 or sum(map(len, literals)) > 8192:
        return None
    for i, left in enumerate(literals):
        for j, right in enumerate(literals):
            if i != j and (left in right or right in left):
                return None
            if any(left[-n:] == right[:n] for n in range(1, min(len(left), len(right)))):
                return None
    return rows


def _literal_grep(directory, pattern, data, env, *, fixed=False):
    """Bound reads after capture; stdout/stderr never enter public diagnostics.

    Like the shared Git seam, this bounds reads, not subprocess disk writes.
    The timeout bounds execution; scratch is private and always removed.
    """
    pattern_path = directory / 'pattern'
    identifier_list._write_pattern(pattern_path, pattern + b'\n')
    output_path, error_path = directory / 'output', directory / 'error'
    for path in (output_path, error_path):
        identifier_list._write_pattern(path, b'')
    with output_path.open('wb') as output, error_path.open('wb') as error:
        result = process.run(['grep', '-F' if fixed else '-E', '--color=never',
                              '-a', *(['-q'] if fixed else ['-b', '-o']),
                              '-f', str(pattern_path)], input=data, env=env,
                             stdout=output, stderr=error, check=False,
                             timeout=identifier_list.GREP_TIMEOUT_SECONDS)
    with output_path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    with error_path.open('rb') as stream:
        diagnostic = stream.read(1)
    code = getattr(result, 'returncode', None)
    if type(code) is not int or code not in {0, 1} or diagnostic or len(raw) > MAX_BYTES:
        _fail('literal proof command failed')
    if fixed:
        if raw:
            _fail('literal proof command failed')
    elif (code == 1 and raw) or (code == 0 and not raw):
        _fail('literal proof command failed')
    return code, raw


def _literal_proofs(repo, patterns, text, *, public_commit):
    """Return private exact-byte evidence, never authority or a policy verdict.

    Invalid arguments/snapshot raise CommandError. Unsupported grammar, missing
    witnesses, command/read failures or changed locale return a fixed refusal.
    Operational refusal discards all partial occurrences. No list is reloaded.
    """
    import tempfile

    # Copy before validating so mutation of the caller's list cannot split facts.
    if not isinstance(patterns, (list, tuple)):
        _fail('literal proof requires captured patterns')
    patterns = tuple(patterns)
    encoded = identifier_list._encode_patterns(patterns)
    data = identifier_list._encode_text(text)
    public_commit = _commit_id(public_commit)
    if not isinstance(repo, (str, os.PathLike)):
        _fail('literal proof repository is invalid')
    try:
        path = os.fsdecode(repo)
    except (TypeError, ValueError):
        _fail('literal proof repository is invalid')
    if not path or '\0' in path:
        _fail('literal proof repository is invalid')
    _repository(repo)
    if _git(repo, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
            input=(public_commit+'\n').encode('ascii')) != (public_commit+' commit\n').encode('ascii'):
        _fail('literal proof snapshot is not a commit')
    commit_body = _object(repo, 'commit', public_commit)
    if hashlib.sha1(b'commit '+str(len(commit_body)).encode()+b'\0'+commit_body).hexdigest() != public_commit:
        _fail('literal proof snapshot is invalid')
    locale = _literal_environment()
    try:
        locale_digest = _canonical_digest(locale)
    except (UnicodeError, ValueError):
        _fail('literal proof environment is invalid')
    result = dict(proofVersion=_LITERAL_VERSION, publicCommit=public_commit,
                  listDigest=_canonical_digest(list(patterns)), inputDigest=_digest(data),
                  localeDigest=locale_digest, complete=False,
                  occurrences=[], reason=None)

    def refuse(reason):
        result.update(complete=False, occurrences=[], reason=reason)
        return result

    grammar = _literal_grammar(encoded)
    if grammar is None:
        return refuse('unsupported-grammar')
    if 'GREP_OPTIONS' in locale:
        return refuse('unsupported-environment')
    env = dict(os.environ)
    fixed_env = dict(env, LC_ALL='C')
    for key in ('GREP_OPTIONS', 'GREP_COLOR', 'GREP_COLORS'):
        fixed_env.pop(key, None)
    occurrences, values = [], []
    try:
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='t3c-literal-') as scratch:
            directory = Path(scratch)
            os.chmod(directory, 0o700)
            for row, (pattern, branches) in enumerate(zip(encoded, grammar), 1):
                code, raw = _literal_grep(directory, pattern, data, env)
                previous_end = -1
                remainder = bytearray(data)
                for record in raw.split(b'\n')[:-1] if raw else []:
                    offset_raw, sep, literal = record.partition(b':')
                    if (not sep or not re.fullmatch(rb'0|[1-9][0-9]{0,7}', offset_raw)
                            or not literal or not raw.endswith(b'\n')):
                        return refuse('invalid-match-output')
                    offset = int(offset_raw)
                    end = offset + len(literal)
                    if (offset < previous_end or end > len(data) or data[offset:end] != literal
                            or not any(literal == value
                                       and (not start or offset == 0 or data[offset-1:offset] == b'\n')
                                       and (not stop or end == len(data) or data[end:end+1] == b'\n')
                                       for value, start, stop in branches)):
                        return refuse('invalid-match-output')
                    previous_end = end
                    remainder[offset:end] = b'\0' * len(literal)
                    occurrences.append(dict(patternLine=row, byteOffset=offset,
                        byteLength=len(literal), literalDigest=_digest(literal),
                        publicBlobOid=None, publicContentDigest=None))
                    values.append(literal)
                    if len(occurrences) > _LITERAL_OCCURRENCES:
                        return refuse('proof-limit')
                if raw and not raw.endswith(b'\n'):
                    return refuse('invalid-match-output')
                # NUL is outside this grammar. Preserve LF and byte positions:
                # any remaining match proves extraction omitted an occurrence.
                residual_code, _ = _literal_grep(directory, pattern, bytes(remainder), env)
                if residual_code == 0:
                    return refuse('unaccounted-match')
            # A separate raw matcher verifies row coverage under inherited semantics.
            expected = identifier_list.match_identifiers(patterns, text)
            if expected != sorted({o['patternLine'] for o in occurrences}):
                return refuse('unaccounted-match')
            intervals = sorted((o['byteOffset'], o['byteOffset']+o['byteLength']) for o in occurrences)
            if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
                return refuse('overlapping-match')
            witnesses, seen, read_bytes = {}, set(), 0
            needed = set(values)
            for fact in _artifact_tree(repo, public_commit).values():
                if len(witnesses) == len(needed):
                    break
                if fact is None or fact[0] not in {'100644', '100755'}:
                    continue
                oid = fact[2]
                if oid in seen:
                    continue
                seen.add(oid)
                if len(seen) > 128:
                    return refuse('proof-limit')
                size = _artifact_blob_size(repo, oid)
                if size > MAX_BYTES:
                    continue
                read_bytes += size
                if read_bytes > 8 * MAX_BYTES:
                    return refuse('proof-limit')
                body = _object(repo, 'blob', oid)
                if (len(body) != size or hashlib.sha1(b'blob '+str(size).encode()+b'\0'+body).hexdigest() != oid):
                    return refuse('invalid-object')
                try:
                    _text(body)
                except cli.CommandError:
                    continue  # Opaque bytes cannot supply text evidence.
                for literal in dict.fromkeys(values):
                    if literal in witnesses:
                        continue
                    code, _ = _literal_grep(directory, literal, body, fixed_env, fixed=True)
                    if (code == 0) != (literal in body):
                        return refuse('invalid-witness-output')
                    if code == 0:
                        witnesses[literal] = (oid, _digest(body))
            if _literal_environment() != locale:
                return refuse('environment-changed')
            for occurrence, literal in zip(occurrences, values):
                if literal in witnesses:
                    occurrence['publicBlobOid'], occurrence['publicContentDigest'] = witnesses[literal]
            complete = all(o['publicBlobOid'] is not None for o in occurrences)
            result.update(complete=complete, occurrences=occurrences,
                          reason=None if complete else 'missing-witness')
            return result
    except (cli.CommandError, process.ProcessError, OSError, ValueError):
        return refuse('proof-read-failed')
