"""Read-only comparison of validated coordination evidence for one head."""
import re

from . import cli, records, schema

_SHA = re.compile(r"[0-9a-f]{40}")
_TRUSTED = frozenset({"manager", "feature-lead", "reviewer", "assigned-session"})


def _index(loaded):
    """Index the validated prefix, retaining sequence and assignment identity."""
    events, work = loaded["events"], loaded["work"]
    definitions = {field: {item["id"]: item for item in work[field]}
                   for field in ("requiredChecks", "acceptance")}
    since = {field: {key: 0 for key in items} for field, items in definitions.items()}
    intents, tasks, checks, criteria, dispositions = {}, {}, {}, {}, {}
    reviews, operations, results, assignments = [], {}, {}, {}
    lead = None
    implementation = None
    for event in events:
        kind, data, seq = event["kind"], event["data"], event["seq"]
        if kind == "scope":
            for field, previous in definitions.items():
                if data.get(field) is not None:
                    replacement = {item["id"]: item for item in data[field]}
                    for key in previous.keys() | replacement.keys():
                        if previous.get(key) != replacement.get(key):
                            since[field][key] = seq
                    definitions[field] = replacement
        elif kind == "lead-assignment":
            lead = data["thread"]
        elif kind == "dispatch-intent":
            intents[data["clientRequestId"]] = data
        elif kind == "dispatch-confirmed":
            tasks[data["taskId"]] = intents[data["clientRequestId"]]
        elif kind == "result" and tasks[data["taskId"]]["role"] == "worker":
            implementation = event
        elif kind == "check":
            checks.setdefault(data["checkId"], []).append(event)
        elif kind == "criterion-evidence":
            criteria.setdefault(data["criterionId"], []).append(event)
        elif kind == "review":
            reviews.append(event)
        elif kind == "finding-disposition":
            dispositions[data["reviewRef"], data["findingId"]] = event
        elif kind == "op-intent":
            operations[data["operation"]] = event
        elif kind == "op-result":
            results[data["intentRef"]] = event
        assignments[seq] = lead
    return (implementation, checks, criteria, reviews, dispositions, operations,
            results, assignments, lead, since)


def _trusted(event, assignments, lead):
    source = event["data"]["source"]
    return source in _TRUSTED and (source != "feature-lead" or
                                  lead is not None and assignments[event["seq"]] == lead)


def _select(events, since, head, assignments, lead, command=None):
    """Prefer current applicable observations; retain diagnostics when none cover."""
    fresh = [event for event in events if event["seq"] > since]
    applicable = [event for event in fresh
                  if event["data"]["commit"] == head and _trusted(event, assignments, lead)
                  and (command is None or event["data"]["command"] == command)]
    return (applicable or fresh)[-1] if fresh else None


def _result_gap(event, head):
    data = event["data"]
    if data["commit"] != head:
        return "stale"
    if data["outcome"] not in {"succeeded", "no-op"}:
        return "outcome"
    if "statusSummary" in data or data["outcome"] != "no-op":
        summary = data.get("statusSummary")
        if not (isinstance(summary, dict) and summary.get("childStarted") is True
                and summary.get("childStatus") == 0 and summary.get("refusal") is None):
            return "status"
    if not records.concrete(data["confirmation"]):
        return "confirmation"
    return None


def _operation_gap(operation, head, operations, results, uncertain):
    intent = operations.get(operation)
    if intent is None:
        return "missing", None
    pending = next((item for item in uncertain if item["kind"] == "operation"
                    and item["intent"]["data"]["operation"] == operation), None)
    if pending is not None:
        return "unresolved", pending["intent"]
    event = results.get(intent["seq"])
    if event is None:
        return "unresolved", intent
    reason = _result_gap(event, head)
    if reason:
        return reason, event
    return None, event


def _compare(loaded, head, pr_ready):
    """Compare an already RC-1-validated bundle; no disk or process access."""
    work = loaded["effectiveWork"]
    (implementation, checks, criteria, reviews, dispositions, operations,
     results, assignments, lead, since) = _index(loaded)
    gaps = []

    def gap(code, event=None, id=None, review_ref=None):
        gaps.append({"code": code, "id": id, "reviewRef": review_ref,
                     "eventRef": event["seq"] if event is not None else None})

    if implementation is None:
        gap("head-missing")
    elif implementation["data"]["resultCommit"] != head:
        gap("head-stale", implementation)

    for requirement in work["requiredChecks"]:
        key = requirement["id"]
        event = _select(checks.get(key, []), since["requiredChecks"][key], head,
                        assignments, lead, requirement["command"])
        if event is None:
            gap("check-missing", id=key)
            continue
        data = event["data"]
        reason = None
        if data["commit"] != head:
            reason = "stale"
        elif data["command"] != requirement["command"]:
            reason = "command"
        elif not _trusted(event, assignments, lead):
            reason = "source"
        elif data["outcome"] != "pass":
            reason = "outcome"
        elif not records.concrete(data["evidencePath"]):
            reason = "evidence"
        if reason:
            gap("check-" + reason, event, key)

    if not reviews:
        gap("review-missing")
    else:
        event = reviews[-1]
        data = event["data"]
        actor = data["reviewer"]
        if (data["base"], data["head"]) != (work["reviewBase"], head):
            gap("review-stale", event)
        elif ((lead is None and actor["kind"] != "task") or
              (lead is not None and (actor["kind"] != "lead" or assignments[event["seq"]] != lead))):
            gap("review-source", event)
        elif data["verdict"] == "changes-required":
            gap("review-verdict", event)

    # RC-1 validates concrete proof, severity, authorship and decision/review joins.
    # Historical findings survive replacement of a review or its author.
    for review in reviews:
        for finding in review["data"]["findings"]:
            key = (review["seq"], finding["id"])
            event = dispositions.get(key)
            if event is None:
                gap("finding-missing", id=key[1], review_ref=key[0])
            elif finding["severity"] in {"blocking", "important"} and event["data"]["disposition"] in {"disputed", "declined"}:
                gap("finding-unresolved", event, key[1], key[0])

    for requirement in work["acceptance"]:
        key = requirement["id"]
        event = _select(criteria.get(key, []), since["acceptance"][key], head, assignments, lead)
        if event is None:
            gap("criterion-missing", id=key)
            continue
        data = event["data"]
        reason = None
        if data["commit"] != head:
            reason = "stale"
        elif not _trusted(event, assignments, lead):
            reason = "source"
        elif not records.concrete(data["evidence"]):
            reason = "evidence"
        if reason:
            gap("criterion-" + reason, event, key)

    if pr_ready:
        uncertain = records.uncertain_items(loaded["events"])
        for operation, prefix in (("push", "push"), ("pull-request", "pr")):
            reason, event = _operation_gap(operation, head, operations, results, uncertain)
            if reason:
                gap(prefix + "-" + reason, event)
    return {"verified": not gaps, "head": head, "prReady": pr_ready, "gaps": gaps}


def check(local_dir, repo, work_id, head, *, pr_ready=False):
    """Load and compare current evidence; invalid input raises CommandError (2).

    Returns {verified, head, prReady, gaps}; see docs/cli/evidence.md for the
    stable gap order and PB-1's typed operation-result convention.
    """
    if not isinstance(head, str) or _SHA.fullmatch(head) is None or type(pr_ready) is not bool:
        raise cli.CommandError("invalid evidence check arguments")
    try:
        loaded = records.load_work(local_dir, repo, work_id)
    except (records.RecordError, schema.SchemaError):
        raise cli.CommandError("invalid or unavailable work records") from None
    return _compare(loaded, head, pr_ready)


def _command(args):
    data = check(args.local_dir, args.repo, args.work, args.head, pr_ready=args.pr_ready)
    return cli.Outcome(data, cli.ExitCode.SUCCESS if data["verified"] else cli.ExitCode.CHECK_FAILED)


def register(registry):
    parser = registry.group("evidence").add_parser("check")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--pr-ready", action="store_true")
    parser.set_defaults(handler=_command)
