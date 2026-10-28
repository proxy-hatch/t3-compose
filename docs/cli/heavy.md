# Heavy checks

HC-1 implements the [CLI contract](../cli.md) and
[heavy-check plan](../superpowers/plans/2026-10-27-t3-compose-implementation.md#hc-1-heavy-check-lock).
It coordinates cooperating callers using one local filesystem lock. Every
coordinator must select the same local directory for machine-wide exclusion.

## Commands

```text
t3c [--local-dir PATH] heavy run --repo-path WORKTREE --commit SHA --label TEXT --log FILE [--wait] -- CMD [ARGS...]
t3c [--local-dir PATH] heavy status
```

`t3c` abbreviates the isolated interpreter invocation from the CLI contract.
Global `--local-dir` precedes `heavy`. All state derives from `args.local_dir`.
There is no configuration dependency or exit 3 in this module.
Imports and command registration perform no I/O. Every launch uses
`process.start`; Git reads use `process.run`, with optional Git locks disabled.

`run` requires an existing linked worktree root, a full lowercase 40-character
commit SHA equal to HEAD, and a clean staged, unstaged and untracked tree.
Main checkouts, bare repositories, subdirectories and nonrepositories are
refused. The caller must first finish the worktree's writer. The label must
contain 1–200 printable characters and cannot consist only of whitespace.
The `--` separator and a nonempty command are required; NUL arguments fail.
Relative paths resolve before relaunch. The command runs in the worktree.

The log must be a **new file**, with an existing parent directory. Existing
files (including symlinks and hard links) are refused, never truncated. Both
stdout and stderr go to this mode-0600 file; stdin is `/dev/null`. Command
arguments, environment values and backend exception text are never copied
into diagnostics or metadata. Labels and log paths are deliberate output
fields, so callers must choose public-safe labels before publishing evidence.

Local state and the log must lie outside the checked worktree and its Git
storage. State and log creation must not dirty the checked tree, even when
such paths would otherwise be ignored. The same validation runs again after
lock acquisition, including after a blocking wait. Invalid preflight starts
no check. A queued refusal creates no log. No command is run through a shell.

## Results

Default stdout contains one JSON value. Global `--text` uses the shared text
renderer. `run` returns these exits:

| Exit | Data |
|---|---|
| 0 | Completion summary; child exited 0 and both freshness flags are true. |
| 1 | Completion summary; child failed, was signalled, or HEAD/tree changed. |
| 75 | Status observation, including holder details when readable. No check launched. |
| 2 | No stdout; sanitized invalid-input/helper-failure diagnostic on stderr. |

Child exits such as 2 or 75 map to 1, never to helper refusal or busy.
Completion summary fields are exactly:

| Field | Meaning |
|---|---|
| `label` | Supplied label. |
| `commit` | Accepted full HEAD SHA. |
| `log` | Absolute log filename. |
| `childStatus` | Integer return code; negative signal number for signal termination. |
| `headUnchanged` | Postcheck HEAD still equals accepted SHA. |
| `treeUnchanged` | Postcheck staged/unstaged/untracked tree remains clean. |
| `startedAt`, `endedAt` | UTC ISO timestamps with `Z`, before launch and after postcheck. |

If a postcheck Git read fails, the affected freshness flag is false: the
check cannot certify freshness. These are check-event facts, not a complete
record event. This command has no work/writer arguments and appends no events.
A coordinator supplies event identity, source and evidence under the records
contract. Snapshot comparisons cannot detect edits made and reverted between
observations; the caller must exclude concurrent writers.

`status` exits 0 with an observation, even when busy, or 2 for an unsafe path
or I/O failure. Its fields are exactly `locked` (boolean), `metadata` (one of
`missing`, `malformed`, `present`, `stale`), `holder` (object or null), and
`ownerObservation` (`unknown`, `recorded-owner-present`, `recorded-owners-dead`,
or `not-held`). A valid holder object has `label`, `commit`, `runnerPid`,
`checkPid` (null before launch), `startedAt`, and `summary` (null until complete,
then the completion object above). Unknown/extra metadata is not echoed.
A valid free-lock holder is `stale`, including normal completed metadata.
Missing/corrupt metadata retains its classification when free. Held metadata
with no living recorded PID is reported `recorded-owners-dead`: an orphan or
leaked descriptor may still hold the lock. Permission errors and existing or
reused PIDs cannot prove termination; their observation remains conservative.
Status is an instantaneous observation, not a reservation or proof of ownership.

## Files and lifetime

State lives under `LOCAL/state/locks/`. `heavy-check.lock` is created once,
mode 0600, with a stable inode. It is never unlinked, renamed, replaced or
truncated. `heavy-check-status.json` is separately atomically replaced using
`jsonio.atomic_replace`, mode 0600. Status probes never rewrite metadata.
Directory traversal uses no-follow descriptors; state directory descriptors
remain pinned for file operations. Existing lock/status objects must be
regular, single-link, owned by the current user, mode 0600. Symlinks, hard-link
aliases and unsafe objects are refused. A changed lock pathname is refused.
These checks prevent accidental aliases; they do not defend against a hostile
process with the same user privileges deliberately replacing state directories.

Without `--wait`, flock uses nonblocking acquisition and returns busy data.
With `--wait`, the dedicated runner blocks in kernel flock; there is no
polling scheduler, service or user-level acquisition loop. Metadata and PID
observations never grant entry or force-break a held lock.

The wrapper starts a runner in a new session. Its private protocol uses a
hidden `--_reply-fd` argument on the same `heavy run` command and one JSON
reply carrying data/code over an inherited pipe. It is not a supported user
interface. Only the runner inherits that pipe; checks and grandchildren do
not. Runner stdio is `/dev/null`, independent of wrapper output pipes.
The wrapper waits for the runner before rendering its one summary. SIGKILL
of the wrapper leaves the runner/check active. Broken wrapper output cannot
release a live check's lock.

SIGINT, SIGTERM and SIGHUP are caught by wrapper and runner and forwarded to
their direct child. Signals during launch are retained until that child is
available. A signalled waiter still waits for kernel acquisition, then refuses
launch. A check that delays/ignores signals keeps the lock until it exits;
cleanup deliberately has no forced timeout. Process descendants are not a
process-tree cancellation contract.

The foreground check inherits the locked open file description across exec.
Runner SIGKILL therefore leaves exclusion intact while that descriptor lives.
After waiting for foreground termination, the surviving runner explicitly
unlocks the shared description, even if a grandchild retains a duplicate.
Closing alone is insufficient. Errors during metadata/postcheck/response
handling never unlock before foreground termination. If the runner is killed,
there is no surviving runner to perform that explicit unlock: descendants can
retain exclusion, reported conservatively by status, until they close/exit.
There is no automatic unsafe recovery. A command that deliberately closes or
unlocks inherited descriptors defeats runner-loss retention; this is a
cooperative check wrapper, not a sandbox. No proof of live success is implied.

## Offline evidence and EM live recipe

The offline suite uses real temporary Git linked worktrees, real flock and
bootstrap-guarded Python subprocesses. It proves serialization, wrapper loss,
signals, output/exit mapping, stale observations and descendant-FD release.
Offline evidence does not count as live evidence. Run t3-compose `SUITE`
directly; **never put SUITE through heavy run or the machine's shared lock**.

Only the Engineering manager runs this short post-merge live recipe. Use a
scratch repository, explicit temporary state, and a log outside its worktree:

```sh
PY="$(uv python find 3.15)"
T3C_ENTRY="/absolute/path/to/helper/bin/t3c"
HC_SCRATCH="$(mktemp -d)"
git init -q "$HC_SCRATCH/repo"
git -C "$HC_SCRATCH/repo" -c user.name=Synthetic -c user.email=synthetic@example.invalid commit -q --allow-empty -m 'Synthetic check'
git -C "$HC_SCRATCH/repo" worktree add -q --detach "$HC_SCRATCH/check"
HC_SHA="$(git -C "$HC_SCRATCH/check" rev-parse HEAD)"
"$PY" -I -S -B "$T3C_ENTRY" --local-dir "$HC_SCRATCH/state" heavy run --repo-path "$HC_SCRATCH/check" --commit "$HC_SHA" --label synthetic-live --log "$HC_SCRATCH/check.log" -- "$PY" -I -S -B -c 'print("synthetic live check")'
"$PY" -I -S -B "$T3C_ENTRY" --local-dir "$HC_SCRATCH/state" heavy status
```

After both calls finish, inspect the summary/log, remove the scratch linked
worktree with `git worktree remove`, and remove only `HC_SCRATCH`. Cleanup
never touches the shared lock or any owner's repository. Record live evidence
separately; Workers do not run this recipe or create a live script.
