"""EM-only L2: one LIVE synthetic Decisions API call, using the real adapter."""
import argparse
from pathlib import Path
import sys
import uuid


def main():
    if not (sys.implementation.name == "cpython" and sys.version_info >= (3, 15) and
            sys.version_info.releaselevel == "final" and sys.flags.isolated and sys.flags.no_site):
        print("Stable CPython 3.15+ with -I -S required", file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from t3compose import cli, jev, jsonio, paths, records

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise cli.CommandError("invalid live check arguments; use --help")

    try:
        parser = Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--local-dir", type=Path, default=paths.local_dir())
        args = parser.parse_args()
        local = paths.local_dir(args.local_dir)
        repo, work_id = "synthetic/jev-contract", "L2-" + uuid.uuid4().hex
        writer = "manager@" + repo
        task = {"intent": "Synthetic: implement a pure parser for comma-separated integers",
                "acceptance": ["Parse valid integers", "Reject malformed integer tokens"],
                "interfaces": ["parse(text) returns a list of integers"],
                "scope": ["synthetic_parser.py"], "riskFacts": [], "dependencies": []}
        value = {"workId": work_id, "repository": repo, "linearIssue": None,
                 "targetBranch": "main", "reviewBase": cli.helper_info()["commit"],
                 "scope": "Synthetic Jev contract check", "designRefs": ["docs/cli/jev.md"],
                 "ownedPaths": ["synthetic_parser.py"], "dependencies": [],
                 "acceptance": [{"id": "CONTRACT", "text": "Both typed answers validate"}],
                 "requiredChecks": [{"id": "L2", "kind": "fast",
                                     "command": "live/jev_contract.py"}], "writer": writer}
        records.create_work(local, repo, work_id, value, writer=writer)
        print("LIVE synthetic Jev contract check; one request, no automatic retry")
        result = jev.classify(local, repo, work_id, task)
        print(jsonio.canonical_json({"label": "LIVE synthetic", "workId": work_id,
                                    "result": result.data}).decode("utf-8"))
        return int(result.code)
    except cli.CommandError as error:
        print("LIVE synthetic check refused; inspect configuration, inputs and records", file=sys.stderr)
        return int(error.code)
    except Exception:
        print("LIVE synthetic check failed; preserve records before retry", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
