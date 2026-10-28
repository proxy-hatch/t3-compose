"""Durable records, validated history, prefix checkpoints and conservative recovery."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from . import cli, jsonio, process, schema

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


def canonical_identity(raw):
    parts = raw.split("/")
    require(all(SEGMENT.fullmatch(p) for p in parts), "invalid repository identity")
    require((len(parts) == 2 and parts[0] == "synthetic") or
            (len(parts) == 3 and parts[0] != "synthetic" and
             ("." in parts[0] or parts[0] == "localhost")), "invalid repository identity")
    return "/".join([parts[0].lower(), *parts[1:]])


@boundary
def normalize_repository(repo):
    raw = os.fspath(repo)
    try:
        return canonical_identity(raw)
    except RecordError:
        pass  # Canonical identities take precedence over ambiguous relative paths.
    if Path(raw).is_dir():
        raw = git(Path(raw).resolve(), "config", "--get", "remote.origin.url")
    if "://" in raw:
        url = urlsplit(raw)
        require(url.scheme in {"https", "ssh"} and url.hostname and not url.query
                and not url.fragment and url.password is None and url.port is None,
                "invalid repository identity")
        raw = url.hostname.lower() + "/" + url.path.removeprefix("/").removesuffix(".git")
    elif re.fullmatch(r"[^/@:]+@[^/:]+:.+", raw):
        host, path = raw.split("@", 1)[1].split(":", 1)
        raw = host.lower() + "/" + path
        raw = raw.removesuffix(".git")
    return canonical_identity(raw)


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
                except FileExistsError:
                    pass
                os.fsync(fd)  # EEXIST may follow another creator's failed sync.
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


def checkpoint(work, events, through_seq=None):
    count = len(events) if through_seq is None else through_seq
    require(type(count) is int and 0 <= count <= len(events), "invalid checkpoint sequence")
    payload = {"work": work, "events": events[:count]}
    return {"lastSeq": count, "digest": "sha256:" + hashlib.sha256(jsonio.canonical_json(payload)).hexdigest()}


def preserved(data):
    native, artifacts = data["t3Facts"], data["gitFacts"]
    require(data["outcome"] == "stopped-and-preserved" and native.get("state") == "stopped"
            and native.get("childrenReconciled") is True and artifacts.get("commitsPreserved") is True
            and artifacts.get("uncommittedPreserved") is True, "preservation evidence required")
    for facts, key in ((native, "evidence"), (artifacts, "evidence"), (artifacts, "worktreePath")):
        require(isinstance(facts.get(key), str) and facts[key].strip(), "preservation evidence required")


def concrete(value):
    if isinstance(value, dict):
        return bool(value) and "unknown" not in value and all(concrete(v) for v in value.values())
    if isinstance(value, list):
        return bool(value) and all(concrete(v) for v in value)
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def review_author(review):
    actor = review["reviewer"]
    return actor["kind"] + ":" + actor["taskId" if actor["kind"] == "task" else "thread"]


def history(work, events):
    schema.validate_work(work)
    effective = dict(work)
    intents, confirmations, tasks, results, cleared, operations = {}, {}, {}, {}, set(), set()
    prior_worker = None
    latest_result = None
    lead, tenure, restarted = None, 0, set()
    manager = "manager@" + work["repository"]
    lead_writer = "lead:" + work["workId"] + "@" + work["repository"]

    def reference(seq, *kinds):
        require(type(seq) is int and 1 <= seq <= len(seen), "missing history reference")
        event = seen[seq - 1]
        require(event["kind"] in kinds, "wrong history reference kind")
        return event["data"]

    seen = []
    for event in events:
        require(isinstance(event, dict), "invalid event object")
        kind, data = event.get("kind"), event.get("data")
        schema.validate_event(event)
        require(event["seq"] == len(seen) + 1 and event["writer"] == effective["writer"])
        if kind in {"handoff", "restart", "writer-transfer"}:
            saved = data["checkpoint"]
            require(saved == checkpoint(work, seen, saved["lastSeq"]), "checkpoint digest mismatch")
            if kind == "restart" and data["phase"] == "after":
                before = reference(data["beforeRef"], "restart")
                require(before["phase"] == "before" and before["checkpoint"] == saved
                        and data["beforeRef"] not in restarted, "invalid restart reference")
                restarted.add(data["beforeRef"])
            else:
                require(saved["lastSeq"] == len(seen), "checkpoint must cover pre-event prefix")
            if kind == "handoff":
                require(data["pointers"]["repository"] == work["repository"], "handoff repository mismatch")
        if kind == "writer-transfer":
            proof = reference(data["reconciliationRef"], "reconciliation")
            preserved(proof)
            require(data["from"] == effective["writer"] and data["to"] in {manager, lead_writer}
                    and (data["to"] != lead_writer or lead is not None), "invalid writer transfer")
            require(data["reconciliationRef"] > tenure and proof["item"] ==
                    {"kind": "takeover", "ref": data["from"]} and
                    proof["t3Facts"].get("writer") == data["from"], "writer reconciliation mismatch")
            require(set(intents) <= cleared and not any(e["kind"] == "dispatch-intent"
                    for e in seen[data["reconciliationRef"]:]), "writer children unresolved")
            require(not any(e["kind"] == "reconciliation" and e["data"]["item"] == proof["item"]
                            for e in seen[data["reconciliationRef"]:]), "writer reconciliation superseded")
            if data["from"] == lead_writer and lead is not None:
                require(proof["t3Facts"].get("thread") == lead, "lead reconciliation mismatch")
            effective["writer"], tenure = data["to"], event["seq"]
        elif kind == "lead-assignment":
            require(event["writer"] == manager and data["assignedBy"] == manager, "manager ownership required")
            lead = data["thread"]
        elif kind == "review":
            require(data["base"] == work["reviewBase"] and data["head"] in
                    {work["reviewBase"], *(r["resultCommit"] for t, r in results.items()
                     if intents[tasks[t]]["role"] == "worker")}, "review work provenance mismatch")
            if data["reviewer"]["kind"] == "lead":
                require(data["reviewer"]["thread"] == lead, "review requires current lead")
            else:
                task = data["reviewer"]["taskId"]
                require(task in results, "review requires completed native result")
                intent = intents[tasks[task]]
                require(intent["role"] == "reviewer" and intent["reviewBase"] == data["base"]
                        and intent["startCommit"] == data["head"] == results[task]["resultCommit"],
                        "review native provenance mismatch")
        elif kind == "finding-disposition":
            review = reference(data["reviewRef"], "review")
            findings = [f for f in review["findings"] if f["id"] == data["findingId"]]
            require(findings and concrete(data["evidence"]), "finding or evidence missing")
            disposition = data["disposition"]
            if disposition == "withdrawn":
                require(data["by"] == review_author(review), "withdrawal requires review author")
            elif disposition == "declined":
                require(findings[0]["severity"] == "minor", "only minor findings may be declined")
            elif disposition in {"owner-decided", "rejected-by-second-review"}:
                evidence = data["evidence"]
                require(isinstance(evidence, dict), "disposition reference required")
                if disposition == "owner-decided":
                    decision = reference(evidence.get("decisionRef"), "decision")
                    require(isinstance(decision["answer"], str) and decision["decidedBy"] == "owner",
                            "resolved owner decision required")
                else:
                    other = reference(evidence.get("reviewRef"), "review")
                    require(evidence["reviewRef"] > data["reviewRef"] and other["reviewer"]["kind"] == "task"
                            and other["reviewer"] != review["reviewer"] and
                            (other["base"], other["head"]) == (review["base"], review["head"]),
                            "independent second review required")
        elif kind == "reconciliation" and data["item"]["kind"] == "takeover":
            if data["outcome"] == "stopped-and-preserved":
                preserved(data)
                require(data["item"]["ref"] == effective["writer"] == data["t3Facts"].get("writer"),
                        "writer reconciliation mismatch")
        elif kind == "dispatch-intent":
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
            else:
                cleared.discard(request)
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
    schema.validate_event_data(kind, data)
    identity, path = location(local_dir, repo, work_id)
    loaded = read_work(path, identity, work_id)
    require(writer == loaded["effectiveWork"]["writer"], "writer identity mismatch")
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


@boundary
def snapshot(local_dir, repo, work_id, *, through_seq=None):
    loaded = load_work(local_dir, repo, work_id)
    return checkpoint(loaded["work"], loaded["events"], through_seq)


def uncertain_items(events):
    items, tasks, retained = {}, {}, {}
    for event in events:
        kind, data = event["kind"], event["data"]
        if kind in {"dispatch-intent", "op-intent"}:
            category = "dispatch" if kind == "dispatch-intent" else "operation"
            key = data["clientRequestId"] if category == "dispatch" else data["opId"]
            items[category, key] = {"kind": category, "ref": event["seq"], "intent": event,
                                    "confirmation": None, "observations": []}
            retained[category, key] = items[category, key]
        elif kind == "dispatch-confirmed":
            key = data["clientRequestId"]
            tasks[data["taskId"]] = key
            retained["dispatch", key]["confirmation"] = event
        elif kind == "result":
            key = ("dispatch", tasks[data["taskId"]])
            retained[key]["observations"].append(event)
            items.pop(key, None)
        elif kind == "op-result":
            key = ("operation", data["opId"])
            summary = data.get("statusSummary") or {}
            retained[key]["observations"].append(event)
            if data["outcome"] != "unknown" and not (summary.get("childStarted") and summary.get("childStatus") is None):
                items.pop(key, None)
            else:
                items[key] = retained[key]
        elif kind == "reconciliation" and data["item"]["kind"] in {"dispatch", "operation"}:
            category, ref = data["item"]["kind"], data["item"]["ref"]
            for key, item in retained.items():
                if key[0] == category and ref in (key[1], item["ref"]):
                    item["observations"].append(event)
                    if category == "dispatch" and data["outcome"] == "stopped-and-preserved":
                        items.pop(key, None)
                    elif (category == "dispatch" or data["outcome"] in {"unknown-liveness", "in-flight"}
                          or not concrete(data["t3Facts"]) or not concrete(data["gitFacts"])):
                        items[key] = item
    return list(items.values())


def temporary_info(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink in {1, 2})


def dead_pid(pid):
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno == errno.ESRCH
    except (OverflowError, ValueError):
        pass
    return False


def scan_temporaries(fd, prefix, repair):
    entries = []
    for name in sorted(os.listdir(fd)):
        match = jsonio.TEMP_NAME.fullmatch(name)
        if not match:
            continue
        entry = {"path": prefix + name, "state": "retained"}
        handle = None
        unlinked = False
        try:
            initial = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if temporary_info(initial) and dead_pid(int(match[1])):
                handle = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
                info = os.fstat(handle)
                if temporary_info(info) and (info.st_dev, info.st_ino) == (initial.st_dev, initial.st_ino):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    final = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if (final.st_dev, final.st_ino) == (info.st_dev, info.st_ino) and temporary_info(final) and dead_pid(int(match[1])):
                        entry["state"] = "orphan"
                        if repair:
                            os.unlink(name, dir_fd=fd)
                            unlinked = True
                            os.fsync(fd)
                            entry["state"] = "removed"
        except FileNotFoundError:
            if not unlinked:
                continue  # Another cooperative writer/repair completed its cleanup.
            entry["state"] = "error"
        except (PermissionError, BlockingIOError):
            if entry["state"] == "orphan":
                entry["state"] = "error"
        except OSError:
            entry["state"] = "error"
        finally:
            if handle is not None:
                os.close(handle)
        entries.append(entry)
    return entries


@boundary
def reconcile(local_dir, repo, work_id=None, *, repair=False):
    identity, parent = location(local_dir, repo)
    if work_id is not None:
        location(local_dir, identity, work_id)  # Validate before building a report/path.
        names = [work_id]
    else:
        try:
            with directory(parent) as fd:
                names = sorted(os.listdir(fd))
        except FileNotFoundError:
            return {"works": [], "damaged": []}
    works, damaged = [], []
    for name in names:
        report = {"workId": name if SEGMENT.fullmatch(name) else None, "uncertain": [], "temporaries": []}
        invalid = False
        try:
            loaded = load_work(local_dir, identity, name)
            report["uncertain"] = uncertain_items(loaded["events"])
        except ValueError:
            invalid = True
        try:
            require(report["workId"] is not None, "unsafe work directory")
            with directory(parent / name) as fd:
                report["temporaries"].extend(scan_temporaries(fd, "", repair))
                try:
                    events_fd = os.open("events", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    pass  # Partial creation: scan the root without adopting the work.
                else:
                    try:
                        report["temporaries"].extend(scan_temporaries(events_fd, "events/", repair))
                    finally:
                        os.close(events_fd)
        except (OSError, ValueError):
            invalid = True
        if invalid or any(t["state"] == "error" for t in report["temporaries"]):
            damaged.append({"workId": report["workId"], "error": "invalid or unsupported work records"})
        works.append(report)
    return {"works": works, "damaged": damaged}


def command(args):
    try:
        common = (args.local_dir, args.repo)
        action = args.record_action
        if action == "list":
            result = list_work(*common)
            return cli.Outcome(result, 2 if result["damaged"] else 0)
        if action == "snapshot":
            return snapshot(*common, args.work, through_seq=args.through_seq)
        if action in {"reconcile", "repair"}:
            result = reconcile(*common, args.work, repair=action == "repair")
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
    for group, action in (("work", "new"), ("work", "show"), ("work", "list"),
                          ("work", "snapshot"), ("work", "reconcile"), ("work", "repair"), ("event", "add")):
        parser = registry.group(group).add_parser(action)
        parser.add_argument("--repo", required=True)
        if action != "list":
            parser.add_argument("--work", required=action not in {"reconcile", "repair"})
        if action == "snapshot":
            parser.add_argument("--through-seq", type=int)
        if action in {"new", "add"}:
            parser.add_argument("--file", required=True)
            parser.add_argument("--writer", required=True)
        if action == "add":
            parser.add_argument("--kind", required=True)
            parser.add_argument("--checkout")
        parser.set_defaults(handler=command, record_action=action)
