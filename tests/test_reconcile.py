"""Offline history and real abrupt-crash recovery, using only temporary state."""
import copy
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from t3compose import process
from t3compose.jsonio import canonical_json
from tests.support import OfflineTestCase, BOOTSTRAP_PATH
from tests import test_records as fixtures
from tests.test_records import (REPO_ID,
                               MANAGER_WRITER, LEAD_WRITER, SHA_A, SHA_C)


class OfflineRecovery(OfflineTestCase):
    setUp = fixtures.OfflineRecordStore.setUp
    add = fixtures.OfflineRecordStore.add
    load = fixtures.OfflineRecordStore.load
    command = fixtures.OfflineRecordStore.command
    refuses_replay = fixtures.OfflineReviewHistory.refuses_replay

    def cli_event(self, kind, data, expected=0):
        file = self.temp / "synthetic-correction.json"
        file.write_bytes(canonical_json(data))
        result = self.command("event", "add", "--repo", REPO_ID, "--work", "WORK-1",
                              "--writer", MANAGER_WRITER, "--kind", kind, "--file", str(file))
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def snapshot(self, **kwargs):
        return self.r.snapshot(self.local, REPO_ID, "WORK-1", **kwargs)

    def report(self, repair=False, work="WORK-1"):
        return self.r.reconcile(self.local, REPO_ID, work, repair=repair)

    def preservation(self, writer=MANAGER_WRITER):
        return {"item": {"kind": "takeover", "ref": writer}, "outcome": "stopped-and-preserved",
                "t3Facts": {"writer": writer, "state": "stopped", "childrenReconciled": True,
                            "thread": "thread-lead-synth-1", "evidence": "synthetic native observation"},
                "gitFacts": {"commitsPreserved": True, "uncommittedPreserved": True,
                             "evidence": "synthetic archive", "worktreePath": str(self.temp / "synthetic-tree")}}

    def transfer(self, old, new):
        proof = self.add("reconciliation", self.preservation(old), writer=old)
        return self.add("writer-transfer", {"from": old, "to": new, "reconciliationRef": proof["seq"],
                                             "checkpoint": self.snapshot()}, writer=old)

    def test_prefix_digest_restart_handoff_and_tampering(self):
        self.assertTrue(hasattr(self.r, "snapshot"), "checkpoint API missing")
        initial = self.snapshot()
        expected = "sha256:" + hashlib.sha256(canonical_json({"work": self.work, "events": []})).hexdigest()
        self.assertEqual(initial, {"lastSeq": 0, "digest": expected})
        self.assertEqual(self.snapshot(), initial)
        before = self.add("restart", {"phase": "before", "checkpoint": initial})
        saved = self.snapshot()
        self.assertNotEqual(saved, initial)
        self.assertEqual(self.snapshot(through_seq=0), initial)
        handoff = self.add("handoff", {**self.payloads["handoff"], "checkpoint": saved})
        for data in ({"phase": "after", "checkpoint": saved, "beforeRef": before["seq"]},
                     {"phase": "after", "checkpoint": initial, "beforeRef": handoff["seq"]},
                     {"phase": "before", "checkpoint": initial}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.add("restart", data)
        after = self.add("restart", {"phase": "after", "checkpoint": initial, "beforeRef": before["seq"]})
        with self.assertRaises(ValueError):
            self.add("restart", after["data"])
        self.assertEqual(self.snapshot(through_seq=1), saved)
        self.refuses_replay(before, {"data": {**before["data"], "checkpoint": {**initial, "digest": "sha256:" + "0" * 64}}})
        self.refuses_replay(after, {"data": {**after["data"], "beforeRef": handoff["seq"]}})
        self.refuses_replay(handoff, {"data": {**handoff["data"], "pointers": {**handoff["data"]["pointers"], "repository": "synthetic/wrong"}}})
        original = (self.directory / "work.json").read_bytes()
        (self.directory / "work.json").write_bytes(canonical_json({**self.work, "scope": "tampered"}))
        with self.assertRaises(ValueError):
            self.snapshot(through_seq=0)
        (self.directory / "work.json").write_bytes(original)
        for number in (-1, 99, True, "1"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.snapshot(through_seq=number)

    def test_writer_transfer_requires_preserved_tenure_and_all_children(self):
        self.assertTrue(hasattr(self.r, "snapshot"), "checkpoint API missing")
        self.add("lead-assignment")
        for group, key, value in (("t3Facts", "state", "running"), ("t3Facts", "childrenReconciled", False),
                                  ("t3Facts", "writer", LEAD_WRITER), ("gitFacts", "commitsPreserved", False),
                                  ("gitFacts", "uncommittedPreserved", False), ("gitFacts", "evidence", "")):
            data = self.preservation()
            data[group][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.add("reconciliation", data)
        for outcome in ("unknown-liveness", "in-flight", "completed"):
            proof = self.add("reconciliation", {**self.preservation(), "outcome": outcome})
            with self.assertRaises(ValueError):
                self.add("writer-transfer", {"from": MANAGER_WRITER, "to": LEAD_WRITER,
                                             "reconciliationRef": proof["seq"], "checkpoint": self.snapshot()})
        # Even an outstanding read-only Reviewer must be reconciled.
        self.add("dispatch-intent", {**self.payloads["dispatch-intent"], "role": "reviewer"})
        for confirmed in (False, True):
            if confirmed:
                self.add("dispatch-confirmed")
            with self.assertRaises(ValueError):
                self.transfer(MANAGER_WRITER, LEAD_WRITER)
        self.add("result")
        transfer = self.transfer(MANAGER_WRITER, LEAD_WRITER)
        self.assertEqual(self.load()["effectiveWork"]["writer"], LEAD_WRITER)
        self.assertEqual(self.load()["work"], self.work)
        for kind in ("next-action", "dispatch-intent"):
            with self.assertRaises(ValueError):
                self.add(kind)
        with self.assertRaises(ValueError):
            self.add("lead-assignment", writer=LEAD_WRITER)
        self.add("next-action", writer=LEAD_WRITER)
        for target in ("lead:OTHER@" + REPO_ID, "manager@synthetic/wrong"):
            with self.assertRaises(ValueError):
                self.transfer(LEAD_WRITER, target)
        self.transfer(LEAD_WRITER, MANAGER_WRITER)
        self.add("lead-assignment", {**self.payloads["lead-assignment"], "thread": "synthetic-new-lead"})
        with self.assertRaises(ValueError):
            self.add("writer-transfer", {**transfer["data"], "checkpoint": self.snapshot()})
        self.transfer(MANAGER_WRITER, LEAD_WRITER)
        self.assertEqual(self.load()["effectiveWork"]["writer"], LEAD_WRITER)
        self.refuses_replay(transfer, {"writer": LEAD_WRITER})
        self.refuses_replay(transfer, {"data": {**transfer["data"], "from": LEAD_WRITER}})

    def test_reconcile_dispatch_native_refs_and_uncertain_operations(self):
        self.assertTrue(hasattr(self.r, "reconcile"), "reconciliation API missing")
        intent = self.add("dispatch-intent")
        report = self.report()["works"][0]
        self.assertEqual(report["uncertain"][0]["intent"], intent)
        self.assertIsNone(report["uncertain"][0]["confirmation"])
        confirmed = self.add("dispatch-confirmed")
        self.assertEqual(self.report()["works"][0]["uncertain"][0]["confirmation"], confirmed)
        self.add("reconciliation", {**self.payloads["reconciliation"], "item": {"kind": "dispatch", "ref": 1}})
        self.assertEqual(len(self.report()["works"][0]["uncertain"]), 1)
        self.add("result")
        self.assertEqual(self.report()["works"][0]["uncertain"], [])
        op = self.add("op-intent")
        data = {**self.payloads["op-result"], "intentRef": op["seq"]}
        for mutation in ({"outcome": "unknown"}, {"statusSummary": {"childStarted": True, "childStatus": None}}):
            self.add("op-result", {**data, **mutation})
            uncertain = self.report()["works"][0]["uncertain"]
            self.assertEqual([(i["kind"], i["ref"]) for i in uncertain], [("operation", op["seq"])])
            self.assertTrue(uncertain[0]["observations"])
        self.add("op-result", data)
        self.assertEqual(self.report()["works"][0]["uncertain"], [])
        for ref in (op["seq"], op["data"]["opId"]):
            observation = self.add("reconciliation", {**self.payloads["reconciliation"],
                "item": {"kind": "operation", "ref": ref}, "outcome": "unknown-liveness"})
            result = self.command("work", "reconcile", "--repo", REPO_ID, "--work", "WORK-1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["works"][0]["uncertain"]), 1)
            item = json.loads(result.stdout)["works"][0]["uncertain"][0]
            self.assertEqual((item["intent"], item["observations"][-1]), (op, observation))
            self.add("op-result", data)
            self.assertEqual(self.report()["works"][0]["uncertain"], [])

    def temporary(self, folder=None, pid=99999999, nonce="a"):
        file = (folder or self.directory / "events") / f'.t3c-tmp-{pid}-{nonce * 32}.tmp'
        file.write_bytes(b"synthetic interrupted write")
        file.chmod(0o600)
        return file

    def test_orphan_predicates_locks_metadata_and_inode_replacement(self):
        self.assertTrue(hasattr(self.r, "reconcile"), "repair API missing")
        temp = self.temporary()
        dead = ProcessLookupError(errno.ESRCH, "synthetic dead")
        original = temp.read_bytes()
        for error in (None, PermissionError(errno.EPERM, "synthetic"), OSError(errno.EIO, "synthetic"), OverflowError()):
            with self.subTest(error=type(error).__name__), patch.object(os, "kill", side_effect=error):
                self.assertEqual(self.report(True)["works"][0]["temporaries"][0]["state"], "retained")
                self.assertEqual(temp.read_bytes(), original)
        with patch.object(os, "kill", side_effect=dead):
            with temp.open("rb") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(self.report(True)["works"][0]["temporaries"][0]["state"], "retained")
            temp.chmod(0o644)
            self.report(True)
            self.assertTrue(temp.exists())
            temp.chmod(0o600)
            # Three links cannot be a helper-owned publication temporary.
            links = [self.temp / f"synthetic-link-{n}" for n in (1, 2)]
            for link in links:
                os.link(temp, link)
            self.report(True)
            self.assertTrue(temp.exists())
            for link in links:
                link.unlink()
            with patch.object(os, "getuid", return_value=os.getuid() + 1):
                self.report(True)
                self.assertTrue(temp.exists())
            real_lock = fcntl.flock
            def swap(fd, flags):
                result = real_lock(fd, flags)
                temp.unlink()
                temp.write_bytes(b"synthetic replacement")
                temp.chmod(0o600)
                return result
            with patch.object(fcntl, "flock", swap):
                self.report(True)
            self.assertEqual(temp.read_bytes(), b"synthetic replacement")
            temp.unlink()
            temp.symlink_to(self.directory / "work.json")
            self.report(True)
            self.assertTrue(temp.is_symlink())
            temp.unlink()
            temp.mkdir()
            self.report(True)
            self.assertTrue(temp.is_dir())
            temp.rmdir()
        live = self.temporary(pid=os.getpid())
        self.report(True)
        self.assertTrue(live.exists())

    def test_partial_creation_damage_isolation_root_temps_and_disappearance(self):
        self.assertTrue(hasattr(self.r, "reconcile"), "repair API missing")
        partial = self.directory.parent / "WORK-partial"
        partial.mkdir()
        orphan = self.temporary(partial)
        unrelated = partial / "keep.tmp"
        unrelated.write_text("unrelated")
        root_temp = self.temporary(self.directory)
        events = self.add("next-action")
        before = (self.directory / "events/1.json").read_bytes()
        with patch.object(os, "kill", side_effect=ProcessLookupError(errno.ESRCH, "dead")):
            result = self.report(True, work=None)
        self.assertEqual([x["workId"] for x in result["damaged"]], ["WORK-partial"])
        self.assertFalse(orphan.exists())
        self.assertFalse(root_temp.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(self.load()["events"], [events])
        self.assertEqual((self.directory / "events/1.json").read_bytes(), before)
        temporary = self.temporary(self.directory)
        real_stat = os.stat
        def disappear(path, *args, **kwargs):
            if path == temporary.name:
                temporary.unlink(missing_ok=True)
            return real_stat(path, *args, **kwargs)
        with patch.object(os, "stat", disappear):
            self.assertEqual(self.report()["damaged"], [])
        (partial / "events").symlink_to(self.directory / "events", target_is_directory=True)
        self.assertTrue(self.report(True, work=None)["damaged"])
        self.assertEqual((self.directory / "events/1.json").read_bytes(), before)

    def test_actual_recovery_commands_success_refusals_and_sanitization(self):
        for action in ("snapshot", "reconcile", "repair"):
            result = self.command("work", action, "--repo", REPO_ID, "--work", "WORK-1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)
            for extra in (("--through-seq", "-1"), ("--through-seq", "99"), ("--through-seq", "synthetic-secret")):
                refused = self.command("work", action, "--repo", REPO_ID, "--work", "WORK-1", *extra)
                self.assertEqual((refused.returncode, refused.stdout), (2, ""))
                self.assertNotIn("synthetic-secret", refused.stderr)
        saved = json.loads(self.command("work", "snapshot", "--repo", REPO_ID, "--work", "WORK-1").stdout)
        self.add("next-action")
        self.assertEqual(json.loads(self.command("work", "snapshot", "--repo", REPO_ID,
                         "--work", "WORK-1", "--through-seq", "0").stdout), saved)
        self.assertEqual(self.command("work", "snapshot", "--repo", REPO_ID).returncode, 2)
        for action in ("reconcile", "repair"):
            result = self.command("work", action, "--repo", "synthetic/absent")
            self.assertEqual((result.returncode, json.loads(result.stdout)), (0, {"works": [], "damaged": []}))
            result = self.command("work", action, "--repo", REPO_ID, "--work", "missing")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["damaged"][0]["workId"], "missing")

    def test_abrupt_prelink_and_postlink_crash_then_actual_repair_and_append(self):
        import shutil
        script = self.temp / "synthetic-crash.py"
        script.write_text('''import os, signal, sys
from pathlib import Path
from t3compose import cli, records
local, window, sandbox = sys.argv[1:]
Path(sandbox).write_text(str(Path.cwd().parent))
original = os.link
def crash(*args, **kwargs):
    if window == "after":
        original(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGKILL)
os.link = crash
cli.helper_info = lambda: {"commit": "3" * 40}
records.append_event(local, "github.com/example-org/example-repo", "WORK-1",
                     "next-action", {"action": "synthetic crash event"},
                     writer="manager@github.com/example-org/example-repo")
''')
        for window, linked in (("before", 0), ("after", 1)):
            local = self.temp / f"synthetic-crash-{window}"
            self.r.create_work(local, REPO_ID, "WORK-1", self.work, writer=MANAGER_WRITER)
            sandbox = self.temp / f"synthetic-child-{window}"
            result = process.run([sys.executable, "-I", "-S", "-B", str(BOOTSTRAP_PATH),
                                  str(script), str(local), window, str(sandbox)], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, -9, result.stderr)
            # The bootstrap's fresh temp context cannot unwind under SIGKILL.
            child_temp = Path(sandbox.read_text())
            self.assertEqual(child_temp.parent, Path("/tmp").resolve())
            shutil.rmtree(child_temp)
            self.local = local
            directory = local / "state/records" / REPO_ID / "work/WORK-1/events"
            temps = list(directory.glob(".t3c-tmp-*"))
            self.assertEqual(len(temps), 1)
            pid = int(temps[0].name.split("-")[2])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            old_bytes = {p.name: p.read_bytes() for p in directory.glob("*.json")}
            self.assertEqual(len(old_bytes), linked)
            for action, state in (("reconcile", "orphan"), ("repair", "removed")):
                result = self.command("work", action, "--repo", REPO_ID, "--work", "WORK-1")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["works"][0]["temporaries"][0]["state"], state)
            self.assertFalse(temps[0].exists())
            data = self.temp / "synthetic-restarted.json"
            data.write_bytes(canonical_json({"action": "synthetic restarted append"}))
            result = self.command("event", "add", "--repo", REPO_ID, "--work", "WORK-1", "--kind", "next-action",
                                  "--file", str(data), "--writer", MANAGER_WRITER)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["seq"], linked + 1)
            self.assertEqual(sorted(p.name for p in directory.iterdir()), [f"{i}.json" for i in range(1, linked + 2)])
            for name, content in old_bytes.items():
                self.assertEqual((directory / name).read_bytes(), content)
            self.assertEqual(len(self.load()["events"]), linked + 1)

    def test_stale_proof_after_dispatch_and_no_implicit_lead_transfer(self):
        with self.assertRaises(ValueError):
            self.transfer(MANAGER_WRITER, LEAD_WRITER)
        self.add("lead-assignment")
        self.assertEqual(self.load()["effectiveWork"]["writer"], MANAGER_WRITER)
        proof = self.add("reconciliation", self.preservation())
        for kind in ("dispatch-intent", "dispatch-confirmed", "result"):
            self.add(kind)
        with self.assertRaises(ValueError):
            self.add("writer-transfer", {"from": MANAGER_WRITER, "to": LEAD_WRITER,
                                         "reconciliationRef": proof["seq"], "checkpoint": self.snapshot()})
        self.transfer(MANAGER_WRITER, LEAD_WRITER)
        bad = self.preservation(LEAD_WRITER)
        bad["t3Facts"]["thread"] = "synthetic-wrong"
        proof = self.add("reconciliation", bad, writer=LEAD_WRITER)
        with self.assertRaises(ValueError):
            self.add("writer-transfer", {"from": LEAD_WRITER, "to": MANAGER_WRITER,
                                         "reconciliationRef": proof["seq"], "checkpoint": self.snapshot()}, writer=LEAD_WRITER)

    def test_dispatch_preservation_removes_uncertainty_and_keeps_native_facts(self):
        self.add("dispatch-intent")
        self.add("dispatch-confirmed")
        data = self.preservation()
        data["item"] = {"kind": "dispatch", "ref": 1}
        data["t3Facts"].update(clientRequestId=self.payloads["dispatch-intent"]["clientRequestId"],
                               taskId=self.payloads["dispatch-confirmed"]["taskId"])
        data["gitFacts"]["worktreePath"] = self.payloads["dispatch-confirmed"]["worktreePath"]
        self.add("reconciliation", {**data, "outcome": "unknown-liveness"})
        item = self.report()["works"][0]["uncertain"][0]
        self.assertEqual(item["confirmation"]["data"]["childThread"], self.payloads["dispatch-confirmed"]["childThread"])
        self.assertEqual(item["observations"][0]["data"]["outcome"], "unknown-liveness")
        proof = self.add("reconciliation", data)
        self.assertEqual(self.report()["works"][0]["uncertain"], [])
        self.refuses_replay(proof, {"data": {**data, "gitFacts": {"unknown": "lost artifacts"}}})
        checkout, _, head = fixtures.OfflineRecordStore.git_fixture(self)
        replacement = {**self.payloads["dispatch-intent"], "clientRequestId": "synthetic-replacement", "round": 2}
        for ref, outcome in ((1, "unknown-liveness"), (data["t3Facts"]["clientRequestId"], "in-flight")):
            observation = self.add("reconciliation", {**data, "item": {"kind": "dispatch", "ref": ref},
                "outcome": outcome, "t3Facts": {"unknown": "synthetic latest status unavailable"}})
            result = self.command("work", "reconcile", "--repo", REPO_ID, "--work", "WORK-1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["works"][0]["uncertain"]), 1)
            item = json.loads(result.stdout)["works"][0]["uncertain"][0]
            self.assertEqual(item["confirmation"]["data"], self.payloads["dispatch-confirmed"])
            self.assertIn(proof, item["observations"])
            self.assertEqual(item["observations"][-1], observation)
            with self.assertRaises(ValueError):
                self.add("dispatch-intent", replacement)
            self.cli_event("dispatch-intent", {**replacement, "dispatchingCheckout": str(checkout), "provisionedCommit": head}, 2)
            renewed = self.add("reconciliation", data)
            self.assertEqual(self.report()["works"][0]["uncertain"], [])
        self.add("dispatch-intent", replacement)
        self.refuses_replay(renewed, {"data": {**data, "outcome": "unknown-liveness"}})

    def test_later_reviewer_uncertainty_blocks_transfer(self):
        self.add("lead-assignment")
        intent = self.add("dispatch-intent", {**self.payloads["dispatch-intent"], "role": "reviewer"})
        self.add("dispatch-confirmed")
        data = self.preservation()
        data["item"] = {"kind": "dispatch", "ref": intent["seq"]}
        data["t3Facts"].update(clientRequestId=intent["data"]["clientRequestId"], taskId=self.payloads["dispatch-confirmed"]["taskId"])
        data["gitFacts"]["worktreePath"] = self.payloads["dispatch-confirmed"]["worktreePath"]
        self.add("reconciliation", data)
        proof = self.add("reconciliation", self.preservation())
        for ref in (intent["seq"], intent["data"]["clientRequestId"]):
            self.add("reconciliation", {**data, "item": {"kind": "dispatch", "ref": ref}, "outcome": "unknown-liveness"})
            transfer = {"from": MANAGER_WRITER, "to": LEAD_WRITER, "reconciliationRef": proof["seq"], "checkpoint": self.snapshot()}
            with self.assertRaises(ValueError):
                self.add("writer-transfer", transfer)
            self.cli_event("writer-transfer", transfer, 2)
            self.add("reconciliation", data)
        transfer["checkpoint"] = self.snapshot()
        event = self.add("writer-transfer", transfer)
        self.assertEqual(self.load()["effectiveWork"]["writer"], LEAD_WRITER)
        self.refuses_replay(event, {"data": {**transfer, "reconciliationRef": intent["seq"]}})
        replay = copy.deepcopy(self.load()["events"])
        replay[-2]["data"]["outcome"] = "unknown-liveness"
        replay[-1]["data"]["checkpoint"] = self.r.checkpoint(self.work, replay[:-1])
        with self.assertRaises(ValueError):
            self.r.history(self.work, replay)

    def test_repair_sync_failure_retains_events_and_reports_uncertainty(self):
        self.add("next-action")
        event = self.directory / "events/1.json"
        original = event.read_bytes()
        for error in (OSError(errno.EIO, "synthetic sync"), PermissionError(errno.EACCES, "synthetic sync"),
                      FileNotFoundError(errno.ENOENT, "synthetic sync")):
            temp = self.temporary()
            os.link(temp, self.temp / "synthetic-preserved")
            with patch.object(os, "kill", side_effect=ProcessLookupError(errno.ESRCH, "dead")), \
                    patch.object(os, "fsync", side_effect=error):
                report = self.report(True)
            self.assertEqual(report["works"][0]["temporaries"][0]["state"], "error")
            self.assertTrue(report["damaged"])
            self.assertFalse(temp.exists())
            self.assertEqual(event.read_bytes(), original)
            (self.temp / "synthetic-preserved").unlink()

    def test_actual_entrypoint_complete_checkpoint_lead_and_disposition_history(self):
        def command(action, **options):
            args = ["work", action, "--repo", REPO_ID, "--work", "WORK-1"]
            for key, value in options.items():
                args.extend(["--" + key.replace("_", "-"), str(value)])
            result = self.command(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        def add(kind, data, writer=MANAGER_WRITER):
            path = self.temp / "synthetic-command-payload.json"
            path.write_bytes(canonical_json(data))
            result = self.command("event", "add", "--repo", REPO_ID, "--work", "WORK-1", "--kind", kind,
                                  "--writer", writer, "--file", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        checkpoint = command("snapshot")
        before = add("restart", {"phase": "before", "checkpoint": checkpoint})
        add("handoff", {**self.payloads["handoff"], "checkpoint": command("snapshot")})
        add("restart", {"phase": "after", "checkpoint": checkpoint, "beforeRef": before["seq"]})
        assignment = add("lead-assignment", self.payloads["lead-assignment"])
        proof = add("reconciliation", self.preservation())
        add("writer-transfer", {"from": MANAGER_WRITER, "to": LEAD_WRITER,
                                "reconciliationRef": proof["seq"], "checkpoint": command("snapshot")})
        evidence = self.temp / "synthetic-lead-review.md"
        evidence.write_text("synthetic review")
        review = add("review", {**self.payloads["review"], "head": SHA_A,
                     "reviewer": {"kind": "lead", "thread": assignment["data"]["thread"]},
                     "evidence": {"path": str(evidence), "digest": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()}}, LEAD_WRITER)
        add("finding-disposition", {**self.payloads["finding-disposition"], "reviewRef": review["seq"],
            "disposition": "withdrawn", "by": "lead:" + assignment["data"]["thread"]}, LEAD_WRITER)
        self.assertEqual(command("snapshot", through_seq=0), checkpoint)
        shown = command("show")
        self.assertEqual(len(shown["events"]), 8)
        self.assertEqual(shown["effectiveWork"]["writer"], LEAD_WRITER)
        for action in ("reconcile", "repair"):
            self.assertEqual(command(action)["works"][0]["uncertain"], [])

    def test_later_unknown_writer_observation_invalidates_old_stopped_proof(self):
        self.add("lead-assignment")
        proof = self.add("reconciliation", self.preservation())
        self.add("reconciliation", {**self.preservation(), "outcome": "unknown-liveness",
                                    "t3Facts": {"unknown": "synthetic status unavailable"}})
        with self.assertRaises(ValueError):
            self.add("writer-transfer", {"from": MANAGER_WRITER, "to": LEAD_WRITER,
                                         "reconciliationRef": proof["seq"], "checkpoint": self.snapshot()})
        self.transfer(MANAGER_WRITER, LEAD_WRITER)
