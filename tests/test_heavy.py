"""Offline integration: real entry, guarded descendants, Git worktrees and flock."""
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from unittest.mock import patch

from tests.support import BOOTSTRAP_PATH, OfflineTestCase, ROOT
from t3compose import jsonio, process


class OfflineHeavy(OfflineTestCase):
    def setUp(self):
        self.repo = self.temp / "repo"
        self.work = self.temp / "check"
        self.local = self.temp / "local"
        self.handles = []
        self.addCleanup(self.cleanup_processes)
        self.git("init", "-q", str(self.repo), cwd=self.temp)
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-q", "--allow-empty", "-m", "Synthetic")
        self.git("worktree", "add", "-q", "--detach", str(self.work))
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.adapter = self.temp / "adapter.py"
        self.adapter.write_text(textwrap.dedent(f'''\
            import fcntl, os, runpy, sys
            from pathlib import Path
            from t3compose import jsonio, process
            root = Path({str(self.temp)!r})
            (root / ("boot-" + str(os.getpid()))).write_text(os.environ["HOME"])
            def helper(*args):
                return [sys.executable, "-I", "-S", "-B", {str(BOOTSTRAP_PATH)!r},
                        __file__, *args]
            process.helper_command = helper
            original_flock = fcntl.flock
            def observe_wait(fd, operation):
                if operation == fcntl.LOCK_EX and "--label" in sys.argv:
                    label = sys.argv[sys.argv.index("--label") + 1]
                    (root / (label + ".waiting")).touch()
                return original_flock(fd, operation)
            fcntl.flock = observe_wait
            if (root / "inject-publish").exists():
                original = jsonio.atomic_replace
                def fail_after_launch(path, value, **kwargs):
                    if value.get("checkPid") is not None:
                        (root / "publish-failed").touch()
                        raise OSError("synthetic-private-error")
                    return original(path, value, **kwargs)
                jsonio.atomic_replace = fail_after_launch
            sys.argv[0] = {str(ROOT / 'bin/t3c')!r}
            runpy.run_path(sys.argv[0], run_name="__main__")
            '''))
        self.fixture = self.temp / "fixture.py"
        self.fixture.write_text(textwrap.dedent('''\
            import json, os, signal, subprocess, sys, time
            from pathlib import Path
            from t3compose import process
            from tests.support import BOOTSTRAP_PATH
            root, work, mode, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
            (root / ("boot-" + str(os.getpid()))).write_text(os.environ["HOME"])
            os.chdir(work)
            def mark(suffix, value):
                temporary = root / (name + suffix + ".tmp")
                temporary.write_text(str(value))
                temporary.replace(root / (name + suffix))
            def received(sig, frame):
                mark(".signal", sig)
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, received if mode == "delay" else signal.SIG_DFL)
            print("synthetic stdout", flush=True)
            print("synthetic stderr", file=sys.stderr, flush=True)
            lock_inode = (root / "local/state/locks/heavy-check.lock").stat().st_ino
            lock_fds = []
            for fd in range(3, 256):
                try:
                    if os.fstat(fd).st_ino == lock_inode:
                        lock_fds.append(fd)
                except OSError:
                    pass
            if mode != "descendant" and not (root / "inject-publish").exists():
                deadline = time.monotonic() + 10
                metadata = root / "local/state/locks/heavy-check-status.json"
                while json.loads(metadata.read_text())["checkPid"] != os.getpid():
                    if time.monotonic() > deadline:
                        sys.exit(9)
                    time.sleep(.01)
            mark(".ready", json.dumps({"pid": os.getpid(), "time": time.monotonic(), "lockFds": lock_fds}))
            if mode in ("hold", "delay", "descendant"):
                deadline = time.monotonic() + 20
                while not (root / (name + ".release")).exists():
                    if time.monotonic() > deadline:
                        sys.exit(9)
                    time.sleep(.01)
            elif mode == "leak":
                child = process.start([sys.executable, "-I", "-S", "-B", str(BOOTSTRAP_PATH),
                                       __file__, str(root), str(work), "descendant", name + "-desc"],
                                      pass_fds=tuple(lock_fds), stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
                deadline = time.monotonic() + 10
                while not (root / (name + "-desc.ready")).exists():
                    if time.monotonic() > deadline:
                        child.kill()
                        child.wait()
                        sys.exit(9)
                    time.sleep(.01)
            elif mode == "dirty":
                Path("changed").write_text("change")
            elif mode == "head":
                process.run(["git", "-c", "user.name=Synthetic", "-c",
                             "user.email=synthetic@example.invalid", "commit", "--allow-empty",
                             "-m", "Change"], check=True, capture_output=True)
            elif mode == "git-gone":
                Path(".git").rename(root / "saved-git")
            elif mode == "signal":
                os.kill(os.getpid(), signal.SIGTERM)
            mark(".end", time.monotonic())
            sys.exit(int(mode) if mode.isdigit() else 0)
            '''))

    def git(self, *args, cwd=None):
        return process.run(["git", "-C", str(cwd or self.repo), *args],
                           check=True, capture_output=True, text=True).stdout

    def python(self, script, *args):
        return [sys.executable, "-I", "-S", "-B", str(BOOTSTRAP_PATH), str(script), *args]

    def argv(self, name="one", mode="0", wait=False, **changes):
        values = {"repo-path": str(self.work), "commit": self.sha,
                  "label": name, "log": str(self.temp / (name + ".log"))}
        values.update(changes)
        argv = ["--local-dir", str(self.local), "heavy", "run"]
        for key, value in values.items():
            argv.extend(["--" + key, value])
        if wait:
            argv.append("--wait")
        return [*argv, "--", *self.python(self.fixture, str(self.temp), str(self.work), mode, name)]

    def launch(self, argv):
        child = process.start(self.python(self.adapter, *argv), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        self.handles.append(child)
        return child

    def finish(self, child, code=0):
        out, err = child.communicate(timeout=15)
        self.assertEqual(child.returncode, code, err)
        if code == 2:
            self.assertEqual(out, "")
            self.assertNotIn("synthetic-private", err)
            return None
        self.assertEqual(err, "")
        self.assertEqual(len(out.splitlines()), 1)
        return json.loads(out)

    def invoke(self, argv, code=0):
        return self.finish(self.launch(argv), code)

    def until(self, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail("fixture readiness/termination deadline exceeded")

    def ready(self, name):
        path = self.temp / (name + ".ready")
        self.until(path.exists)
        return json.loads(path.read_text())

    def release(self, name):
        (self.temp / (name + ".release")).touch()

    def status(self):
        return self.invoke(["--local-dir", str(self.local), "heavy", "status"])

    def lock_paths(self):
        directory = self.local / "state/locks"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "heavy-check.lock", directory / "heavy-check-status.json"

    def cleanup_processes(self):
        # Children killed mid-bootstrap cannot unwind their sandbox: track and
        # remove only the exact synthetic contexts recorded by our adapters.
        for path in self.temp.glob("*.ready"):
            self.release(path.name.removesuffix(".ready"))
        for child in self.handles:
            if child.poll() is None:
                child.terminate()
            try:
                child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=3)
        for path in self.temp.glob("boot-*"):
            pid = int(path.name.split("-")[1])
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            def gone():
                try:
                    os.kill(pid, 0)
                    return False
                except ProcessLookupError:
                    return True
            self.until(gone)
            home = Path(path.read_text())
            if home.name == "home" and home.parent.parent == self.temp.parent:
                shutil.rmtree(home.parent, ignore_errors=True)

    def test_summary_streams_exit_mapping_and_freshness(self):
        for mode, code, status, head, tree in (("0", 0, 0, True, True),
                ("2", 1, 2, True, True), ("75", 1, 75, True, True),
                ("signal", 1, -signal.SIGTERM, True, True),
                ("dirty", 1, 0, True, False), ("head", 1, 0, False, True)):
            with self.subTest(mode=mode):
                result = self.invoke(self.argv(name=mode, mode=mode), code)
                self.assertEqual(set(result), {"label", "commit", "log", "childStatus",
                                 "headUnchanged", "treeUnchanged", "startedAt", "endedAt"})
                self.assertEqual((result["childStatus"], result["headUnchanged"],
                                  result["treeUnchanged"]), (status, head, tree))
                self.assertEqual((result["label"], result["commit"]), (mode, self.sha))
                self.assertEqual(result["log"], str(self.temp / (mode + ".log")))
                for field in ("startedAt", "endedAt"):
                    self.assertTrue(result[field].endswith("Z"))
                self.assertLessEqual(datetime.fromisoformat(result["startedAt"]),
                                     datetime.fromisoformat(result["endedAt"]))
                log = Path(result["log"])
                self.assertEqual(log.read_text(), "synthetic stdout\nsynthetic stderr\n")
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)
                if mode == "dirty":
                    (self.work / "changed").unlink()
        observed = self.status()
        self.assertEqual((observed["locked"], observed["metadata"]), (False, "stale"))
        self.assertEqual(observed["holder"]["summary"], result)

    def test_waiters_serialize_and_nonwaiting_busy_has_holder(self):
        first = self.launch(self.argv(mode="hold"))
        first_info = self.ready("one")
        holder = self.status()["holder"]
        self.assertEqual((holder["label"], holder["commit"], holder["checkPid"]),
                         ("one", self.sha, first_info["pid"]))
        busy = self.invoke(self.argv("busy"), 75)
        self.assertTrue(busy["locked"])
        self.assertEqual(busy["holder"], holder)
        self.assertFalse((self.temp / "busy.ready").exists())
        self.assertFalse((self.temp / "busy.log").exists())
        second = self.launch(self.argv("two", "hold", wait=True))
        self.until((self.temp / "two.waiting").exists)
        # The adapter observes the real blocking flock call; it never replaces
        # acquisition. The second runner has reached the occupied lock.
        self.assertTrue(self.status()["locked"])
        self.assertFalse((self.temp / "two.ready").exists())
        self.release("one")
        self.finish(first)
        second_info = self.ready("two")
        self.assertGreaterEqual(second_info["time"], float((self.temp / "one.end").read_text()))
        self.release("two")
        self.finish(second)

    def test_wrapper_sigkill_retains_lock_and_does_not_retain_output_pipe(self):
        child = self.launch(self.argv(mode="hold"))
        self.ready("one")
        child.kill()
        child.communicate(timeout=3)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.invoke(self.argv("busy"), 75)
        self.release("one")
        self.invoke(self.argv("after", wait=True))

    def test_signals_wait_for_delayed_foreground_exit(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(sig=sig):
                name = "signal-" + str(sig)
                child = self.launch(self.argv(name, "delay"))
                self.ready(name)
                child.send_signal(sig)
                received = self.temp / (name + ".signal")
                self.until(received.exists)
                self.assertEqual(int(received.read_text()), sig)
                self.assertIsNone(child.poll())
                self.invoke(self.argv(name + "-busy"), 75)
                self.release(name)
                self.finish(child)
                self.assertFalse(self.status()["locked"])

    def test_broken_wrapper_stdout_does_not_release_live_check(self):
        child = self.launch(self.argv(mode="hold"))
        self.ready("one")
        child.stdout.close()
        self.invoke(self.argv("busy"), 75)
        self.release("one")
        child.wait(timeout=10)
        self.invoke(self.argv("after", wait=True))

    def test_grandchild_inherited_fd_is_unlocked_after_foreground_exit(self):
        child = self.launch(self.argv(mode="leak"))
        descendant = self.ready("one-desc")
        self.assertTrue(descendant["lockFds"], "descendant must actually retain the lock description")
        self.finish(child)
        os.kill(descendant["pid"], 0)
        self.invoke(self.argv("after"))
        os.kill(descendant["pid"], 0)
        self.release("one-desc")

    def test_runner_loss_preserves_check_lock_and_orphan_observation(self):
        child = self.launch(self.argv(mode="hold"))
        self.ready("one")
        holder = self.status()["holder"]
        os.kill(holder["runnerPid"], signal.SIGKILL)
        self.finish(child, 2)
        self.invoke(self.argv("busy"), 75)
        self.release("one")
        self.invoke(self.argv("after", wait=True))

    def test_kernel_state_overrides_missing_corrupt_and_dead_holder_metadata(self):
        lock, metadata = self.lock_paths()
        with lock.open("wb") as stream:
            lock.chmod(0o600)
            inode = lock.stat().st_ino
            fcntl.flock(stream, fcntl.LOCK_EX)
            # Obtain a PID that has actually exited; no invented 'probably dead' PID.
            departed = self.launch(["version"])
            self.finish(departed)
            holder = {"label": "departed", "commit": self.sha, "runnerPid": departed.pid,
                      "checkPid": departed.pid, "startedAt": "2026-01-01T00:00:00Z", "summary": None}
            for value, classification in ((None, "missing"), ("corrupt", "malformed"),
                                           (holder, "present")):
                if value is None:
                    metadata.unlink(missing_ok=True)
                elif isinstance(value, str):
                    metadata.write_text(value)
                    metadata.chmod(0o600)
                else:
                    jsonio.atomic_replace(metadata, value)
                for _ in range(3):
                    observation = self.status()
                    self.assertEqual((observation["locked"], observation["metadata"]),
                                     (True, classification))
                    self.invoke(self.argv("busy"), 75)
                    self.assertEqual(lock.stat().st_ino, inode)
                if isinstance(value, dict):
                    self.assertEqual(observation["ownerObservation"], "recorded-owners-dead")
            fcntl.flock(stream, fcntl.LOCK_UN)
        self.assertEqual(self.status()["metadata"], "stale")
        self.invoke(self.argv())
        self.assertEqual(lock.stat().st_ino, inode)
        duplicate = json.dumps(holder).replace('"label": "departed"',
                                               '"label": "departed", "label": "departed"')
        for content, want in ((None, "missing"), ("{}", "malformed"), (duplicate, "malformed")):
            if content is None:
                metadata.unlink()
            else:
                metadata.write_text(content)
                metadata.chmod(0o600)
            observed = self.status()
            self.assertEqual((observed["locked"], observed["metadata"]), (False, want))

    def test_preflight_refuses_repository_and_input_errors_without_launch(self):
        bare = self.temp / "bare"
        self.git("init", "--bare", str(bare), cwd=self.temp)
        subdirectory = self.work / "subdirectory"
        subdirectory.mkdir()
        for changes in ({"repo-path": str(self.repo)}, {"repo-path": str(bare)},
                {"repo-path": str(subdirectory)},
                {"repo-path": str(self.temp)}, {"repo-path": str(self.temp / "missing")},
                {"commit": "0" * 40}, {"commit": "HEAD"}, {"label": " "},
                {"label": "bad\nlabel"}, {"log": str(self.work / "log")},
                {"log": str(self.repo / ".git/log")}, {"log": ""}):
            with self.subTest(changes=changes):
                self.invoke(self.argv(**changes), 2)
                self.assertFalse((self.temp / "one.ready").exists())
        for kind in ("untracked", "staged", "unstaged"):
            path = self.work / "dirty"
            path.write_text("dirty")
            if kind != "untracked":
                self.git("add", "dirty", cwd=self.work)
            if kind == "unstaged":
                self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                         "commit", "-qm", "Fixture", cwd=self.work)
                self.sha = self.git("rev-parse", "HEAD", cwd=self.work).strip()
                path.write_text("changed")
            self.invoke(self.argv(), 2)
            self.git("reset", "--hard", "HEAD", cwd=self.work)
            path.unlink(missing_ok=True)
        args = self.argv()
        separator = args.index("--")
        missing_options = []
        for option in ("--repo-path", "--commit", "--label", "--log"):
            index = args.index(option)
            missing_options.append(args[:index] + args[index + 2:])
        for malformed in (*missing_options, args[:separator], args[:separator + 1],
                          args[:separator + 1] + [""],
                          args[:separator] + ["synthetic-private-command"],
                          ["--local-dir", str(self.local), "heavy", "run"]):
            self.invoke(malformed, 2)
        self.assertFalse((self.temp / "one.ready").exists())

    def test_wait_revalidates_head_and_tree(self):
        lock, _ = self.lock_paths()
        for change in ("tree", "head"):
            with lock.open("ab") as stream:
                lock.chmod(0o600)
                fcntl.flock(stream, fcntl.LOCK_EX)
                child = self.launch(self.argv(change, wait=True))
                self.until((self.temp / (change + ".waiting")).exists)
                if change == "tree":
                    (self.work / "changed").write_text("dirty")
                else:
                    self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                             "commit", "--allow-empty", "-qm", "Changed", cwd=self.work)
                fcntl.flock(stream, fcntl.LOCK_UN)
                self.finish(child, 2)
                self.assertFalse((self.temp / (change + ".ready")).exists())
                (self.work / "changed").unlink(missing_ok=True)

    def test_existing_log_and_state_aliases_cannot_damage_files(self):
        target = self.work / "protected"
        target.write_text("preserve")
        self.git("add", "protected", cwd=self.work)
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "Protected", cwd=self.work)
        self.sha = self.git("rev-parse", "HEAD", cwd=self.work).strip()
        log = self.temp / "one.log"
        for link in (lambda: log.symlink_to(target), lambda: os.link(target, log),
                     lambda: log.write_text("existing")):
            link()
            self.invoke(self.argv(), 2)
            self.assertEqual(target.read_text(), "preserve")
            log.unlink()
        lock, metadata = self.lock_paths()
        for path in (lock, metadata):
            for link in (lambda: path.symlink_to(target), lambda: os.link(target, path)):
                link()
                self.invoke(self.argv(), 2)
                self.invoke(["--local-dir", str(self.local), "heavy", "status"], 2)
                self.assertEqual(target.read_text(), "preserve")
                path.unlink()
        self.local = self.work / "state"
        self.invoke(self.argv(), 2)
        self.assertFalse(self.local.exists())

    def test_pid_permission_error_and_reuse_are_not_proven_termination(self):
        from t3compose import heavy
        for error in (PermissionError(), None):
            with patch.object(os, "kill", side_effect=error):
                self.assertNotEqual(heavy._pid_state(123), "dead")

    def test_metadata_failure_waits_for_live_check_before_unlock(self):
        (self.temp / "inject-publish").touch()
        child = self.launch(self.argv(mode="hold"))
        self.ready("one")
        self.until((self.temp / "publish-failed").exists)
        self.assertIsNone(child.poll())
        self.invoke(self.argv("busy"), 75)
        self.release("one")
        self.finish(child, 2)
        (self.temp / "inject-publish").unlink()
        self.invoke(self.argv("after"))

    def test_forwarded_signal_status_and_failed_postcheck(self):
        child = self.launch(self.argv(mode="hold"))
        self.ready("one")
        child.terminate()
        summary = self.finish(child, 1)
        self.assertEqual(summary["childStatus"], -signal.SIGTERM)
        summary = self.invoke(self.argv("gone", "git-gone"), 1)
        self.assertEqual((summary["childStatus"], summary["headUnchanged"],
                          summary["treeUnchanged"]), (0, False, False))
        (self.temp / "saved-git").rename(self.work / ".git")

    def test_state_directory_symlinks_and_unsafe_modes_are_refused(self):
        self.local.mkdir()
        target = self.temp / "alias-target"
        target.mkdir()
        (self.local / "state").symlink_to(target)
        self.invoke(self.argv(), 2)
        self.assertEqual(list(target.iterdir()), [])
        (self.local / "state").unlink()
        lock, metadata = self.lock_paths()
        for path in (lock, metadata):
            path.write_text("preserve")
            path.chmod(0o644)
            self.invoke(self.argv(), 2)
            self.assertEqual(path.read_text(), "preserve")
            path.unlink()

    def test_signalled_waiter_refuses_launch_after_kernel_wait(self):
        first = self.launch(self.argv(mode="hold"))
        self.ready("one")
        waiter = self.launch(self.argv("waiter", wait=True))
        self.until((self.temp / "waiter.waiting").exists)
        waiter.terminate()
        self.invoke(self.argv("busy"), 75)
        self.release("one")
        self.finish(first)
        self.finish(waiter, 2)
        self.assertFalse((self.temp / "waiter.ready").exists())
