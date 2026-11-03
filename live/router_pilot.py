"""EM-only synthetic router pilot: classify once with V3, derive CD-8 route, never dispatch."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import uuid

REPO = "synthetic/router-pilot"
EXAMPLES_LIMIT = 512 * 1024
BASELINE_FACTS = {"credentials-or-guardrail", "recovery-or-concurrency",
                  "shared-interface", "document-change"}


def load_examples(path, cfg):
    """Validate the complete public dataset before creating work or calling Jev."""
    from t3compose import jsonio, schema

    def require(condition):
        if not condition:
            raise ValueError("invalid synthetic examples")

    def shape(value, keys):
        require(isinstance(value, dict) and set(value) == set(keys))

    value, digest = jsonio.read_json_with_digest(path, max_bytes=EXAMPLES_LIMIT)
    shape(value, ("version", "label", "examples"))
    require(type(value["version"]) is int and value["version"] == 2)
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
        shape(expected, ("lane", "criticality", "scope", "floors"))
        require(expected["lane"] in ("BACKEND_LOGIC", "UI_FRONTEND", "MIXED", "DOCS"))
        require(expected["criticality"] in ("CORE", "NON_CRITICAL"))
        require(expected["scope"] in ("STANDARD", "COMPLEX"))
        floors = expected["floors"]
        require(isinstance(floors, list) and all(isinstance(f, str) and f in cfg["riskFacts"] for f in floors))
        require(len(floors) == len(set(floors)) and set(floors) == set(facts))
        covered.update(floors)
    require(BASELINE_FACTS <= covered)
    return entries, digest


def derive_route(task, classification_data, cfg_risk_facts):
    """Derive routing class locally from decision and CD-8 §3 risk floors."""
    from t3compose import schema

    record = {k: v for k, v in classification_data.items()
              if k not in ("classificationRef", "judgment")}
    schema.validate_event_data("routing-classification", record)
    reference = classification_data.get("classificationRef")
    if (record.get("configVersion") != 3 or type(reference) is not int or
            reference <= record["inputRef"] or record["outcome"] not in ("accepted", "defaulted")):
        raise ValueError("incomplete classification")
    judgment = classification_data.get("judgment")
    if judgment is not None:
        schema.validate_event_data("routing-judgment", {
            k: v for k, v in judgment.items() if k != "judgmentRef"})
        if (judgment.get("configVersion") != 3 or
                any(judgment[k] != record[k] for k in ("inputRef", "inputDigest", "policyDigest")) or
                type(judgment.get("judgmentRef")) is not int or judgment["judgmentRef"] <= reference):
            raise ValueError("incomplete judgment")
        eff_lane, eff_criticality, eff_scope = (judgment[k] for k in ("lane", "criticality", "scope"))
    else:
        answers = record["answers"]
        if (record["outcome"] != "accepted" or set(answers) != {"lane", "criticality", "scope"} or
                answers["lane"]["choice"] == "MIXED"):
            raise ValueError("classification owes judgment")
        eff_lane, eff_criticality, eff_scope = (answers[k]["choice"] for k in ("lane", "criticality", "scope"))

    applied_floors = list(task.get("riskFacts", []))
    for fact in applied_floors:
        floor_spec = cfg_risk_facts.get(fact, {})
        if floor_spec.get("scope") == "COMPLEX":
            eff_scope = "COMPLEX"
        if floor_spec.get("criticality") == "CORE":
            eff_criticality = "CORE"

    starting_stage = 1 if eff_scope == "STANDARD" else 2
    if eff_lane == "DOCS":
        derived_lane = "docs"
    else:
        derived_lane = "backend-logic" if eff_lane in ("BACKEND_LOGIC", "MIXED", "backend-logic") else "ui-frontend"

    return {
        "lane": derived_lane,
        "scope": eff_scope,
        "criticality": eff_criticality,
        "startingStage": starting_stage,
        "appliedFloors": applied_floors,
    }


def summarize(rows):
    """Compare derived final classes against gold; gold never controls decisions."""
    counts = {
        "derived": 0,
        "matchingRoutes": {"scope": 0, "criticality": 0},
        "mistakenCheapRoutes": {"scope": 0, "criticality": 0},
        "unnecessaryExpensiveRoutes": {"scope": 0, "criticality": 0},
        "laneMismatches": 0,
        "floorMismatches": 0,
        "defaultedAnswers": {
            "byQuestion": {
                "lane": {"count": 0, "exampleIds": []},
                "criticality": {"count": 0, "exampleIds": []},
                "scope": {"count": 0, "exampleIds": []},
            },
            "byBasis": {
                "default-low-confidence": 0,
                "default-insufficient-context": 0,
                "default-jev-failure": 0,
            },
            "counts": {"lane": 0, "criticality": 0, "scope": 0},
            "exampleIds": {"lane": [], "criticality": [], "scope": []},
        },
        "incomplete": 0,
    }

    for row in rows:
        gold = row["expected"]
        derived = row.get("derived")
        judgment = row.get("judgment")

        if derived is None:
            counts["incomplete"] += 1
            row["laneMismatch"] = None
            row["scopeComparison"] = None
            row["criticalityComparison"] = None
            row["floorMismatch"] = None
            continue

        counts["derived"] += 1

        # Lane comparison: normalize gold MIXED to backend-logic and gold DOCS to docs
        gold_lane_norm = ("docs" if gold["lane"] == "DOCS" else
                          "backend-logic" if gold["lane"] in ("BACKEND_LOGIC", "MIXED") else "ui-frontend")
        derived_lane_norm = derived["lane"]
        lane_mismatch = (derived_lane_norm != gold_lane_norm)
        row["laneMismatch"] = lane_mismatch
        if lane_mismatch:
            counts["laneMismatches"] += 1

        # Scope comparison
        if derived["scope"] == gold["scope"]:
            scope_comp = "match"
            counts["matchingRoutes"]["scope"] += 1
        elif gold["scope"] == "COMPLEX" and derived["scope"] == "STANDARD":
            scope_comp = "cheap"
            counts["mistakenCheapRoutes"]["scope"] += 1
        elif gold["scope"] == "STANDARD" and derived["scope"] == "COMPLEX":
            scope_comp = "expensive"
            counts["unnecessaryExpensiveRoutes"]["scope"] += 1
        else:
            scope_comp = "mismatch"
        row["scopeComparison"] = scope_comp

        # Criticality comparison
        if derived["criticality"] == gold["criticality"]:
            crit_comp = "match"
            counts["matchingRoutes"]["criticality"] += 1
        elif gold["criticality"] == "CORE" and derived["criticality"] == "NON_CRITICAL":
            crit_comp = "cheap"
            counts["mistakenCheapRoutes"]["criticality"] += 1
        elif gold["criticality"] == "NON_CRITICAL" and derived["criticality"] == "CORE":
            crit_comp = "expensive"
            counts["unnecessaryExpensiveRoutes"]["criticality"] += 1
        else:
            crit_comp = "mismatch"
        row["criticalityComparison"] = crit_comp

        # Floor mismatch
        floor_mismatch = (set(derived["appliedFloors"]) != set(gold["floors"]))
        row["floorMismatch"] = floor_mismatch
        if floor_mismatch:
            counts["floorMismatches"] += 1

        # Defaulted attribution
        defaulted = judgment.get("defaulted", []) if judgment else []
        basis = judgment.get("basis") if judgment else None
        if basis in counts["defaultedAnswers"]["byBasis"]:
            counts["defaultedAnswers"]["byBasis"][basis] += 1
        for q in ("lane", "criticality", "scope"):
            if q in defaulted:
                counts["defaultedAnswers"]["byQuestion"][q]["count"] += 1
                counts["defaultedAnswers"]["byQuestion"][q]["exampleIds"].append(row["id"])
                counts["defaultedAnswers"]["counts"][q] += 1
                counts["defaultedAnswers"]["exampleIds"][q].append(row["id"])

    return counts


def run(args, local, entries, examples_digest, catalog_digest, cfg):
    """Execute classification and local derivation for all examples."""
    from t3compose import cli, config, jev, records

    run_id = "RP-" + uuid.uuid4().hex
    report = {
        "version": 2, "label": "synthetic", "repository": REPO, "runId": run_id,
        "examplesDigest": examples_digest, "catalogDigest": catalog_digest,
        "catalogReadAt": args.catalog_read_at, "complete": False, "failure": None,
        "results": [{"id": e["id"], "label": "synthetic", "expected": e["expected"],
                     "workId": run_id + "-" + e["id"], "recordRefs": {},
                     "classification": None, "judgment": None, "derived": None,
                     "outcome": "not-run", "reason": "not attempted"} for e in entries]
    }
    writer, code = "manager@" + REPO, 0
    classification_fields = ("inputRef", "inputDigest", "policyDigest", "classificationRef",
                             "ruleVersion", "criteriaDigest", "riskFacts", "configuredModel",
                             "snapshotModel", "responseId", "answers", "outcome", "reason")
    judgment_fields = ("judgmentRef", "lane", "criticality", "scope", "defaulted", "basis", "reason")
    stage, row = "metadata", None
    try:
        commit = cli.helper_info()["commit"]
        for entry, row in zip(entries, report["results"]):
            stage = "policy"
            if config.load_matrix_config(local=local).policy_digest != cfg.policy_digest:
                raise ValueError("routing policy changed")
            stage = "create-work"
            row.update(outcome="incomplete", reason="work not completed")
            work = {
                "workId": row["workId"], "repository": REPO, "linearIssue": None,
                "targetBranch": "main", "reviewBase": commit,
                "scope": "Synthetic router pilot; example " + entry["id"] + "; examples " + examples_digest,
                "designRefs": ["docs/router-pilot.md"], "ownedPaths": ["pilot/router-examples.json"],
                "dependencies": [], "acceptance": [{"id": "OBSERVE", "text": "Record synthetic routing evidence"}],
                "requiredChecks": [{"id": "L8", "kind": "fast", "command": "live/router_pilot.py"}],
                "writer": writer
            }
            records.create_work(local, REPO, row["workId"], work, writer=writer)

            stage = "classify"
            result = jev.classify(local, REPO, row["workId"], entry["task"], decline=True)
            data = result.data
            row["classification"] = {k: data[k] for k in classification_fields}
            row["recordRefs"].update(input=data["inputRef"], classification=data["classificationRef"])
            if data.get("judgment"):
                row["judgment"] = {k: data["judgment"][k] for k in judgment_fields}
                row["recordRefs"]["judgment"] = data["judgment"]["judgmentRef"]

            stage = "derive"
            if result.code != cli.ExitCode.SUCCESS:
                raise ValueError("classification refused")
            if (data["policyDigest"] != cfg.policy_digest or
                    config.load_matrix_config(local=local).policy_digest != cfg.policy_digest):
                raise ValueError("routing policy changed")
            row["derived"] = derive_route(entry["task"], data, cfg.data["riskFacts"])
            row.update(outcome="derived", reason="locally derived routing outcome")
        report["complete"] = True
    except Exception as error:
        code = 3 if isinstance(error, cli.CommandError) and error.code == cli.ExitCode.INVALID_CONFIG else 2
        report["failure"] = {"stage": stage, "reason": "pilot interrupted; preserve and reconcile records"}
        if row is not None and row.get("derived") is None:
            row.update(outcome="incomplete", reason="pilot interrupted; preserve and reconcile records")
            try:
                events = records.load_work(local, REPO, row["workId"])["events"]
                for event in events:
                    key = {"routing-input": "input", "routing-classification": "classification",
                           "routing-judgment": "judgment"}.get(event["kind"])
                    if key:
                        row["recordRefs"][key] = event["seq"]
            except Exception:
                pass
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
        args = parser.parse_args()
        local = paths.local_dir(args.local_dir)
        cfg = config.load_matrix_config(local=local)
        now = datetime.now(timezone.utc)
        _, catalog_digest = routing.load_catalog(args.catalog, args.catalog_read_at, now)
        entries, examples_digest = load_examples(args.examples, cfg.data)
        report, code = run(args, local, entries, examples_digest, catalog_digest, cfg)
        print(jsonio.canonical_json(report).decode("utf-8"))
        return code
    except config.ConfigValidationError:
        print("Synthetic pilot refused: invalid routing config", file=sys.stderr)
        return 3
    except cli.CommandError as error:
        print("Synthetic pilot refused: check arguments, configuration and records", file=sys.stderr)
        return 3 if error.code == cli.ExitCode.INVALID_CONFIG else 2
    except Exception:
        print("Synthetic pilot refused: invalid setup, examples or catalog; preserve records", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
