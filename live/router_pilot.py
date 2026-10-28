"""EM-only synthetic router pilot: classify once, select if accepted, never dispatch."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import uuid

REPO = "synthetic/router-pilot"
EXAMPLES_LIMIT = 512 * 1024
BASELINE_FACTS = {"credentials-or-guardrails", "recovery-or-concurrency",
                  "shared-interface", "technical-documentation"}


def load_examples(path, cfg):
    """Validate the complete public dataset before creating work or calling Jev."""
    from t3compose import config, jsonio, schema

    def require(condition):
        if not condition:
            raise ValueError("invalid synthetic examples")

    def shape(value, keys):
        require(isinstance(value, dict) and set(value) == set(keys))

    value, digest = jsonio.read_json_with_digest(path, max_bytes=EXAMPLES_LIMIT)
    shape(value, ("version", "label", "examples"))
    require(type(value["version"]) is int and value["version"] == 1)
    require(value["label"] == "synthetic")
    entries = value["examples"]
    require(isinstance(entries, list) and 12 <= len(entries) <= 32)
    ids, covered = set(), set()
    for entry in entries:
        shape(entry, ("id", "label", "task", "expected"))
        identity = entry["id"]
        require(isinstance(identity, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", identity))
        require(identity not in ids and entry["label"] == "synthetic")
        ids.add(identity)
        task, expected = entry["task"], entry["expected"]
        schema.validate_routing_input({"task": task, "context": None})
        facts = task["riskFacts"]
        require(len(facts) == len(set(facts)) and all(f in cfg["riskFacts"] for f in facts))
        shape(expected, ("domain", "tier", "floors"))
        require(expected["domain"] in config.DOMAIN_CRITERIA_KEYS)
        require((expected["tier"] is None) if expected["domain"] == "MIXED_OR_UNCLEAR"
                else expected["tier"] in ("easy", "medium", "challenging"))
        floors = expected["floors"]
        require(isinstance(floors, list) and all(isinstance(f, str) and f in cfg["riskFacts"] for f in floors))
        require(len(floors) == len(set(floors)) and set(floors) == set(facts))
        covered.update(floors)
    require(BASELINE_FACTS <= covered)
    return entries, digest


def summarize(rows):
    """Compare selected final tiers only; gold never controls classification/selection."""
    counts = dict.fromkeys(("selected", "matchingRoutes", "mistakenCheapRoutes",
                           "unnecessaryExpensiveRoutes", "domainMismatches",
                           "unexpectedSelections", "floorMismatches"), 0)
    counts["unselected"] = {}
    tiers = ("easy", "medium", "challenging")
    for row in rows:
        observed = row["classification"]
        answers = observed["answers"] if observed else {}
        domain = answers.get("domain", {}).get("choice")
        gold, selected = row["expected"], row["selection"]
        row["domainMismatch"] = domain != gold["domain"] if domain is not None else None
        row["unexpectedSelection"] = selected is not None and gold["tier"] is None
        row["floorMismatch"] = set(selected["floorsApplied"]) != set(gold["floors"]) if selected else None
        row["tierComparison"] = None
        counts["domainMismatches"] += row["domainMismatch"] is True
        counts["unexpectedSelections"] += row["unexpectedSelection"]
        counts["floorMismatches"] += row["floorMismatch"] is True
        if selected is None:
            outcome = row["outcome"]
            counts["unselected"][outcome] = counts["unselected"].get(outcome, 0) + 1
            continue
        counts["selected"] += 1
        if row["domainMismatch"] or gold["tier"] is None:
            continue
        difference = tiers.index(selected["tier"]) - tiers.index(gold["tier"])
        category, key = (("cheap", "mistakenCheapRoutes") if difference < 0 else
                         ("expensive", "unnecessaryExpensiveRoutes") if difference > 0 else
                         ("same", "matchingRoutes"))
        row["tierComparison"] = category
        counts[key] += 1
    return counts


def run(args, local, entries, examples_digest, catalog_digest):
    from t3compose import cli, jev, records, routing

    run_id = "RP-" + uuid.uuid4().hex
    report = {"version": 1, "label": "synthetic", "repository": REPO, "runId": run_id,
              "examplesDigest": examples_digest, "catalogDigest": catalog_digest,
              "catalogReadAt": args.catalog_read_at, "complete": False, "failure": None,
              "results": [{"id": e["id"], "label": "synthetic", "expected": e["expected"],
                           "workId": run_id + "-" + e["id"], "recordRefs": {},
                           "classification": None, "selection": None, "routeExitCode": None,
                           "outcome": "not-run", "reason": "not attempted"} for e in entries]}
    writer, code = "manager@" + REPO, 0
    classification_fields = ("inputRef", "inputDigest", "policyDigest", "classificationRef",
                             "ruleVersion", "criteriaDigest", "riskFacts", "configuredModel",
                             "snapshotModel", "responseId", "answers", "outcome", "reason")
    selection_fields = ("inputRef", "inputDigest", "policyDigest", "classificationRef", "selectionRef",
                        "tier", "floorsApplied", "configDigest", "catalogReadAt", "catalogDigest",
                        "quotaStateDigest")
    stage, row = "metadata", None
    try:
        commit = cli.helper_info()["commit"]
        for entry, row in zip(entries, report["results"]):
            stage = "create-work"
            row.update(outcome="incomplete", reason="work not completed")
            work = {"workId": row["workId"], "repository": REPO, "linearIssue": None,
                    "targetBranch": "main", "reviewBase": commit,
                    "scope": "Synthetic router pilot; example " + entry["id"] + "; examples " + examples_digest,
                    "designRefs": ["docs/router-pilot.md"], "ownedPaths": ["pilot/router-examples.json"],
                    "dependencies": [], "acceptance": [{"id": "OBSERVE", "text": "Record synthetic routing evidence"}],
                    "requiredChecks": [{"id": "L8", "kind": "fast", "command": "live/router_pilot.py"}],
                    "writer": writer}
            records.create_work(local, REPO, row["workId"], work, writer=writer)
            stage = "classify"
            result = jev.classify(local, REPO, row["workId"], entry["task"])
            data = result.data
            row["classification"] = {k: data[k] for k in classification_fields}
            row["recordRefs"].update(input=data["inputRef"], classification=data["classificationRef"])
            row.update(outcome=data["outcome"], reason=data["reason"])
            if data["outcome"] != "accepted":
                continue  # No context retry and no manual judgment insertion.
            if result.code != cli.ExitCode.SUCCESS:
                raise ValueError("inconsistent classification outcome")
            stage = "select"
            selection = routing.command(SimpleNamespace(
                local_dir=local, repo=REPO, work=row["workId"], route_action="select",
                input_ref=data["inputRef"], policy_digest=data["policyDigest"], role="worker",
                round="first", review_scope="code", escalate=False,
                catalog=args.catalog, catalog_read_at=args.catalog_read_at))
            if isinstance(selection, cli.Outcome):
                row.update(outcome="route-refused", routeExitCode=int(selection.code))
                reason = selection.data["reason"]
                row["reason"] = ("catalog unavailable or expired" if "catalog" in reason else
                                 "routing policy changed" if reason.startswith("routing policy changed") else
                                 "selection unavailable or requires judgment")
            else:
                row["selection"] = {k: selection[k] for k in selection_fields}
                row["recordRefs"]["selection"] = selection["selectionRef"]
                row.update(outcome="selected", routeExitCode=0, reason="recorded Worker first-round selection")
        report["complete"] = True
    except Exception as error:
        code = 3 if isinstance(error, cli.CommandError) and error.code == cli.ExitCode.INVALID_CONFIG else 2
        report["failure"] = {"stage": stage, "reason": "pilot interrupted; preserve and reconcile records"}
        if row is not None:
            row.update(outcome="incomplete", reason="pilot interrupted; preserve and reconcile records")
            # A durable write can succeed before reporting an error. Never retry it.
            try:
                events = records.load_work(local, REPO, row["workId"])["events"]
                for event in events:
                    key = {"routing-input": "input", "routing-classification": "classification",
                           "routing-selection": "selection"}.get(event["kind"])
                    if key:
                        row["recordRefs"][key] = event["seq"]
            except Exception:
                pass  # Work identity still locates partial evidence if storage is unreadable.
        print("Synthetic pilot interrupted; preserve records before another run", file=sys.stderr)
    report["summary"] = summarize(report["results"])
    return report, code


def main():
    if not (sys.implementation.name == "cpython" and sys.version_info >= (3, 15) and
            sys.version_info.releaselevel == "final" and sys.flags.isolated and sys.flags.no_site):
        print("Stable CPython 3.15+ with -I -S required", file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from t3compose import cli, config, jsonio, paths, routing

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise cli.CommandError("invalid pilot arguments; use --help")

    try:
        parser = Parser(description=__doc__, allow_abbrev=False)
        for option in ("examples", "catalog", "catalog-read-at"):
            parser.add_argument("--" + option, required=True)
        parser.add_argument("--local-dir", type=Path)
        args = parser.parse_args()  # All needed arguments before config/state I/O.
        local = paths.local_dir(args.local_dir)
        cfg = config.load_config(local=local)
        state = config.load_quota_state(local=local, config=cfg)
        now = datetime.now(timezone.utc)
        try:
            for observations in state.data["quotaObservations"].values():
                routing.evaluate_quota(observations, now)
        except ValueError:
            raise cli.CommandError("invalid quota observation time", cli.ExitCode.INVALID_CONFIG) from None
        entries, examples_digest = load_examples(args.examples, cfg.data)
        _, catalog_digest = routing.load_catalog(args.catalog, args.catalog_read_at, now)
        report, code = run(args, local, entries, examples_digest, catalog_digest)
        print(jsonio.canonical_json(report).decode("utf-8"))
        return code
    except config.ConfigValidationError:
        print("Synthetic pilot refused: invalid routing config or quota state", file=sys.stderr)
        return 3
    except cli.CommandError as error:
        print("Synthetic pilot refused: check arguments, configuration and records", file=sys.stderr)
        return 3 if error.code == cli.ExitCode.INVALID_CONFIG else 2
    except Exception:
        print("Synthetic pilot refused: invalid setup, examples or catalog; preserve records", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
