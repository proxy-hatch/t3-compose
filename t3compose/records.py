"""Durable immutable records and validated history (RC-1b, part 2 of 3)."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from . import cli, jsonio, process, schema

DEFERRED = frozenset({"review", "finding-disposition", "lead-assignment", "restart", "handoff", "writer-transfer"})
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_RECORD_BYTES = 1024 * 1024


class RecordError(ValueError):
    """Public diagnostic with no supplied values or backend details."""


def require(condition, message="invalid record history"):
    if not condition:
        raise RecordError(message)


def boundary(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (RecordError, schema.SchemaError):
            raise
        except (OSError, process.ProcessError, ValueError, TypeError, RecursionError):
            raise RecordError("invalid records or local filesystem/process failure") from None
    return call


def git(checkout, *arguments):
    return process.run(["git", "-C", str(checkout), *arguments], check=True,
                       capture_output=True, text=True).stdout.strip()


@boundary
def normalize_repository(repo):
    raw = os.fspath(repo)
    if Path(raw).is_dir():
        raw = git(Path(raw).resolve(), "config", "--get", "remote.origin.url")
    if "://" in raw:
        url = urlsplit(raw)
        require(url.scheme in {"https", "ssh"} and url.hostname and not url.query
                and not url.fragment and url.password is None and url.port is None,
                "invalid repository identity")
        raw = url.hostname.lower() + "/" + url.path.removeprefix("/")
    elif re.fullmatch(r"[^/@:]+@[^/:]+:.+", raw):
        host, path = raw.split("@", 1)[1].split(":", 1)
        raw = host.lower() + "/" + path
    raw = raw.removesuffix(".git")
    parts = raw.split("/")
    require(all(SEGMENT.fullmatch(p) for p in parts), "invalid repository identity")
    require((len(parts) == 2 and parts[0] == "synthetic") or
            (len(parts) == 3 and parts[0] != "synthetic" and
             ("." in parts[0] or parts[0] == "localhost")), "invalid repository identity")
    return "/".join([parts[0].lower(), *parts[1:]])


@contextmanager
def directory(path, *, create=False):
    """Pin each directory inode; never traverse a symlink below the root."""
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            require(part not in {".", ".."}, "unsafe record path")
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def location(local_dir, repo, work_id=None):
    identity = normalize_repository(repo)
    root = Path(local_dir).absolute()
    # System temp roots can contain platform symlinks; the selected root is trusted.
    root = root.parent.resolve() / root.name
    path = root / "state/records" / identity / "work"
    if work_id is not None:
        require(isinstance(work_id, str) and SEGMENT.fullmatch(work_id), "invalid work identifier")
        path /= work_id
    return identity, path


def read_file(fd, name):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(handle)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600, "unsafe record file")
        # read_json owns/closes the duplicate descriptor, leaving this fd pinned.
        return jsonio.read_json(os.dup(handle), max_bytes=MAX_RECORD_BYTES)
    finally:
        os.close(handle)


def history(work, events):
    schema.validate_work(work)
    effective = dict(work)
    intents, confirmations, tasks, results, cleared, operations = {}, {}, {}, {}, set(), set()
    prior_worker = None
    latest_result = None

    def reference(seq, *kinds):
        require(type(seq) is int and 1 <= seq <= len(seen), "missing history reference")
        event = seen[seq - 1]
        require(event["kind"] in kinds, "wrong history reference kind")
        return event["data"]

    seen = []
    for event in events:
        require(isinstance(event, dict), "invalid event object")
        kind, data = event.get("kind"), event.get("data")
        require(kind not in DEFERRED, "transition requires RC-1c")
        schema.validate_event(event)
        require(event["seq"] == len(seen) + 1 and event["writer"] == work["writer"])
        if kind == "dispatch-intent":
            request = data["clientRequestId"]
            require(request not in intents, "duplicate dispatch request")
            require(data["reviewBase"] == work["reviewBase"], "dispatch review base mismatch")
            if data["role"] in {"worker", "feature-lead"}:
                require(all(key in cleared or old["role"] not in {"worker", "feature-lead"}
                            for key, old in intents.items()), "prior writer is unresolved")
            if data["role"] == "worker":
                expected = latest_result or (prior_worker["startCommit"] if prior_worker else work["reviewBase"])
                require(data["startCommit"] == expected and
                        data["round"] == (prior_worker["round"] + 1 if prior_worker else 1), "worker round/start mismatch")
                prior_worker = data
            intents[request] = data
        elif kind == "dispatch-confirmed":
            request, task = data["clientRequestId"], data["taskId"]
            require(request in intents and request not in confirmations and request not in cleared
                    and task not in tasks and all(data["childThread"] != c["childThread"] for c in confirmations.values()),
                    "invalid dispatch confirmation")
            confirmations[request], tasks[task] = data, request
        elif kind == "result":
            task = data["taskId"]
            require(task in tasks and task not in results and data["t3Status"]["state"] == "completed",
                    "result requires confirmed completed native task")
            request = tasks[task]
            observed = confirmations[request]["worktreePath"]
            require(isinstance(observed, dict) or observed == data["worktreePath"], "result worktree mismatch")
            require(request not in cleared, "dispatch already reconciled")
            results[task] = data
            cleared.add(request)
            if intents[request]["role"] == "worker":
                latest_result = data["resultCommit"]
        elif kind == "reconciliation" and data["item"]["kind"] == "dispatch":
            ref = data["item"]["ref"]
            request = reference(ref, "dispatch-intent")["clientRequestId"] if type(ref) is int else ref
            require(request in intents, "unknown reconciled dispatch")
            if data["outcome"] == "stopped-and-preserved":
                native, artifacts = data["t3Facts"], data["gitFacts"]
                require(native.get("clientRequestId") == request and native.get("state") == "stopped"
                        and native.get("childrenReconciled") is True and artifacts.get("commitsPreserved") is True
                        and artifacts.get("uncommittedPreserved") is True, "preservation evidence required")
                for facts, key in ((native, "evidence"), (artifacts, "evidence"), (artifacts, "worktreePath")):
                    require(isinstance(facts.get(key), str) and facts[key].strip(), "preservation evidence required")
                if request in confirmations:
                    confirmed = confirmations[request]
                    require(native.get("taskId") == confirmed["taskId"] and
                            (isinstance(confirmed["worktreePath"], dict) or artifacts["worktreePath"] == confirmed["worktreePath"]),
                            "preservation native provenance mismatch")
                cleared.add(request)
        elif kind == "routing-input":
            if data["context"] and data["context"].get("priorClassificationRef") is not None:
                reference(data["context"]["priorClassificationRef"], "routing-classification")
        elif kind in {"routing-classification", "routing-judgment", "routing-selection"}:
            source = reference(data["inputRef"], "routing-input")
            require(all(data[k] == source[k] for k in ("inputDigest", "policyDigest")), "routing digest mismatch")
            if kind == "routing-classification":
                require(data["riskFacts"] == source["task"]["riskFacts"] and data["criteriaDigest"] ==
                        "sha256:" + hashlib.sha256(jsonio.canonical_json(data["criteria"])).hexdigest(), "classification input mismatch")
            if kind == "routing-selection" and data["classificationRef"] is not None:
                classified = reference(data["classificationRef"], "routing-classification", "routing-judgment")
                require(all(classified[k] == data[k] for k in ("inputRef", "inputDigest", "policyDigest")), "selection input mismatch")
                require(classified.get("outcome", "accepted") == "accepted", "classification not accepted")
        elif kind == "op-intent":
            require(data["opId"] not in operations, "duplicate operation intent")
            operations.add(data["opId"])
        elif kind == "op-result":
            intent = reference(data["intentRef"], "op-intent")
            require(all(data[k] == intent[k] for k in ("opId", "operation", "target", "commit")), "operation result mismatch")
        elif kind == "scope":
            if type(data["decisionRef"]) is int:
                decision = reference(data["decisionRef"], "decision")
                require(isinstance(decision["answer"], str), "scope decision unresolved")
            effective.update({k: data[k] for k in ("scope", "acceptance", "requiredChecks") if data.get(k) is not None})
        elif kind in {"check", "criterion-evidence"}:
            field, key = ("requiredChecks", "checkId") if kind == "check" else ("acceptance", "criterionId")
            require(data[key] in {entry["id"] for entry in effective[field]}, "unknown check or criterion")
        seen.append(event)
    return effective


def read_work(path, identity, work_id):
    with directory(path) as fd:
        work = read_file(fd, "work.json")
        schema.validate_work(work)
        require(work["repository"] == identity and work["workId"] == work_id, "work identity mismatch")
        with directory(path / "events") as events_fd:
            names = os.listdir(events_fd)
            for name in names:
                if jsonio.TEMP_NAME.fullmatch(name):
                    try:
                        info = os.stat(name, dir_fd=events_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue  # Its writer completed cleanup after the directory listing.
                    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink in {1, 2},
                            "unsafe temporary record file")
            names = [name for name in names if not jsonio.TEMP_NAME.fullmatch(name)]
            require(all(re.fullmatch(r"[1-9][0-9]*\.json", name) for name in names), "invalid event filename")
            names.sort(key=lambda name: int(name[:-5]))
            require(names == [f"{seq}.json" for seq in range(1, len(names) + 1)], "event sequence gap")
            events = [read_file(events_fd, name) for name in names]
    return {"work": work, "events": events, "effectiveWork": history(work, events)}


@boundary
def create_work(local_dir, repo, work_id, value, *, writer):
    schema.validate_work(value)
    identity, path = location(local_dir, repo, work_id)
    require((value["repository"], value["workId"], value["writer"]) == (identity, work_id, writer), "work request identity mismatch")
    require(len(jsonio.canonical_json(value)) <= MAX_RECORD_BYTES, "work exceeds size limit")
    with directory(path.parent, create=True) as parent:
        os.mkdir(work_id, 0o700, dir_fd=parent)
        os.fsync(parent)
    with directory(path) as fd:
        os.mkdir("events", 0o700, dir_fd=fd)
        os.fsync(fd)
        jsonio.atomic_create("work.json", value, dir_fd=fd)
    return {"work": value, "events": [], "effectiveWork": dict(value)}


@boundary
def load_work(local_dir, repo, work_id):
    identity, path = location(local_dir, repo, work_id)
    return read_work(path, identity, work_id)


@boundary
def list_work(local_dir, repo):
    identity, path = location(local_dir, repo)
    works, damaged = [], []
    try:
        with directory(path) as fd:
            names = sorted(os.listdir(fd))
    except FileNotFoundError:
        return {"works": works, "damaged": damaged}
    for name in names:
        try:
            works.append(load_work(local_dir, identity, name)["work"])
        except ValueError:
            damaged.append({"workId": name if SEGMENT.fullmatch(name) else None, "error": "invalid or unsupported work records"})
    return {"works": works, "damaged": damaged}


@boundary
def append_event(local_dir, repo, work_id, kind, data, *, writer, checkout=None):
    require(kind not in DEFERRED, "transition requires RC-1c")
    schema.validate_event_data(kind, data)
    identity, path = location(local_dir, repo, work_id)
    loaded = read_work(path, identity, work_id)
    require(writer == loaded["work"]["writer"], "writer identity mismatch")
    require(checkout is None or kind == "dispatch-intent", "checkout requires dispatch intent")
    if kind == "dispatch-intent":
        for old in loaded["events"]:
            if old["kind"] == kind and old["data"]["clientRequestId"] == data["clientRequestId"]:
                require(jsonio.canonical_json(old["data"]) == jsonio.canonical_json(data), "conflicting dispatch retry")
                return old
        dispatching = Path(data["dispatchingCheckout"]).resolve()
        require(git(dispatching, "rev-parse", "HEAD") == data["provisionedCommit"], "provisioned HEAD mismatch")
        if checkout is not None:
            require(Path(checkout).resolve() == dispatching, "dispatch checkout mismatch")
            git(dispatching, "cat-file", "-e", data["startCommit"] + "^{commit}")
    event = {"schemaVersion": schema.SCHEMA_VERSION, "seq": len(loaded["events"]) + 1,
             "kind": kind, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
             "writer": writer, "helperCommit": cli.helper_info()["commit"], "data": data}
    history(loaded["work"], [*loaded["events"], event])
    require(len(jsonio.canonical_json(event)) <= MAX_RECORD_BYTES, "event exceeds size limit")
    with directory(path / "events") as fd:
        jsonio.atomic_create(f'{event["seq"]}.json', event, dir_fd=fd)
    return event


@boundary
def record_routing_input(local_dir, repo, work_id, *, task, context, policy_digest, writer):
    digest = schema.validate_routing_input({"task": task, "context": context})
    event = append_event(local_dir, repo, work_id, "routing-input",
                         {"task": task, "context": context, "inputDigest": digest, "policyDigest": policy_digest}, writer=writer)
    return {"inputRef": event["seq"], "inputDigest": digest, "policyDigest": policy_digest}


def command(args):
    try:
        common = (args.local_dir, args.repo)
        action = args.record_action
        if action == "list":
            result = list_work(*common)
            return cli.Outcome(result, 2 if result["damaged"] else 0)
        if action == "show":
            return load_work(*common, args.work)
        data = jsonio.read_json(args.file, max_bytes=MAX_RECORD_BYTES)
        if action == "new":
            return create_work(*common, args.work, data, writer=args.writer)
        return append_event(*common, args.work, args.kind, data, writer=args.writer, checkout=args.checkout)
    except (RecordError, schema.SchemaError) as error:
        raise cli.CommandError(str(error)) from None
    except (OSError, ValueError, TypeError, RecursionError):
        raise cli.CommandError("invalid record input file") from None


def register(registry):
    for group, action in (("work", "new"), ("work", "show"), ("work", "list"), ("event", "add")):
        parser = registry.group(group).add_parser(action)
        parser.add_argument("--repo", required=True)
        if action != "list":
            parser.add_argument("--work", required=True)
        if action in {"new", "add"}:
            parser.add_argument("--file", required=True)
            parser.add_argument("--writer", required=True)
        if action == "add":
            parser.add_argument("--kind", required=True)
            parser.add_argument("--checkout")
        parser.set_defaults(handler=command, record_action=action)
