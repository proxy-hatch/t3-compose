# Manager restart

A restart clears the manager's conversation while keeping the thread. The
work state is rebuilt from the records.
For related specifications and templates, refer to
[Record format and schema contract](records.md),
[Records commands](cli/records.md),
[Pilot handoff and checklist](pilot.md), and the kickoff template
[`manager-kickoff.md`](../skill/templates/manager-kickoff.md).

In shell snippets below, the helper function is defined once:

```bash
t3c() { "$PY" -I -S "$ROOT/bin/t3c" --local-dir "$STATE" "$@"; }
```

## What L7 verified

L7 verified the following coordinator lifecycle behaviors:

- Clearing the conversation: Claude's `/clear`, sent to the thread through
  `t3_thread_send`, keeps the thread. Afterwards `task_status` works for a
  child started before the clear, and the thread still receives that child's
  completion wake-ups. The final result can arrive under a continuation node
  ID, not the original task ID: recovery must match a completion to its
  child by the child thread (and the `clientRequestId`), and record it under
  the task ID of the `dispatch-confirmed` event.
- A provider switch with `t3_thread_configure` does NOT clear the conversation:
  the new session remembered the old work and a pending completion was still
  delivered. It is not a restart. Its use is the in-place model failover, for
  example moving the manager to its quota fallback without a new thread.
- A repeated `delegate_task` with the same `clientRequestId` returns the
  same task, thread and run, and starts no second child. After a restart,
  retry an unconfirmed dispatch only with the recorded `clientRequestId`.
- Not tested: clear commands for Codex, Pi and agy sessions. The manager row
  is Claude-first and the method above covers it. If the manager runs on a
  provider with no verified clear, record that gap and ask Shawn before the
  handoff.

## Before the restart

Perform these steps in order before clearing the conversation:

1. Write every pending event: results of finished children, dispositions,
   decisions, and the next action. Every open dispatch must already have its
   `dispatch-intent` recorded in the records directory.
2. List open items with `t3c work reconcile --repo "$REPO" --work "$WORK"`
   and resolve what is known before initiating the restart sequence.
3. Take the checkpoint:
   ```bash
   t3c work snapshot --repo "$REPO" --work "$WORK"
   ```
   The command prints `{"digest": "sha256:...", "lastSeq": N}`. The digest
   covers the immutable work metadata and the events through `lastSeq`.
4. Append nothing between the snapshot and the next step: the before event
   must carry the immediate pre-event prefix. If any event lands in between,
   take the snapshot again.
5. Append the before-phase event. Write `before.json` as
   `{"phase": "before", "checkpoint": {"lastSeq": N, "digest": "sha256:..."}}`
   and run:
   ```bash
   t3c event add --repo "$REPO" --work "$WORK" --kind restart --file before.json --writer "$WRITER"
   ```
   The command prints the new event. Its `seq` is the before reference B.
   Keep B and the checkpoint where the kickoff can be rendered from them.
6. Clear the conversation with `/clear` sent through `t3_thread_send` to the
   same thread. Do not start a new thread.

## After the restart

Perform these steps in order after the conversation has cleared:

1. The thread receives the kickoff rendered from the template (pointers
   only). It loads the skill first before attempting any operational action.
2. Read the records: `t3c work list --repo "$REPO"` for the repository,
   then `t3c work show --repo "$REPO" --work "$WORK"`. Find the latest
   before-phase `restart` event with no after-phase event that cites it.
3. Recompute the saved prefix:
   ```bash
   t3c work snapshot --repo "$REPO" --work "$WORK" --through-seq "$N"
   ```
   where N is the checkpoint's `lastSeq`. The digest must equal both the
   kickoff's and the before event's. Recomputing excludes the restart event
   and everything after it. On a mismatch stop and append nothing: a changed
   saved prefix refuses replay and append; preserve the files and reconcile
   against the saved evidence.
4. Reconcile open items. Run `t3c work reconcile --repo "$REPO" --work "$WORK"`.
   For each open dispatch call T3's `task_status` with the task ID from its
   `dispatch-confirmed` event, and compare Git (branch heads, worktree
   cleanliness) with the recorded results. The helper never polls T3 or Git:
   you append the facts you observe as `result` and `reconciliation` events.
5. Append the after-phase event. Write `after.json` as
   `{"phase": "after", "checkpoint": {"lastSeq": N, "digest": "sha256:..."}, "beforeRef": B}`
   and add it with `t3c event add --kind restart`:
   ```bash
   t3c event add --repo "$REPO" --work "$WORK" --kind restart --file after.json --writer "$WRITER"
   ```
   The checkpoint has the same `lastSeq` and `digest` as the before event,
   and `beforeRef` is that before event's own `seq` B.
6. Record live changes since the checkpoint (children that finished while the
   conversation was cleared, new decisions). Events may land between the
   before and the after event; the store accepts that.
7. Continue managing the children from the same thread: the same thread keeps
   managing its children throughout and after the restart.

## Rules

- The store refuses (exit 2): a before event whose checkpoint is not the
  immediate prefix; an after event whose checkpoint differs from its before
  event's; an after event whose `beforeRef` does not name a before-phase
  restart; an after event that cites a before event a second time (a before
  event is consumed once).
- An in-flight restart keeps the thread that dispatched the children: only the
  parent that created a child can manage it with `task_status` and the
  completion wake-ups. Never move a running child's management to a new
  thread; the same thread keeps managing its children.
- Never edit or renumber events. Never re-dispatch a recorded
  `dispatch-intent` without its `clientRequestId`.
- Restart events, like handoff and writer-transfer events, never hash
  themselves: their checkpoint covers the prefix before them.
