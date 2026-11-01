# Lead protocol: quota failover, escalation and routing records

Follow this protocol for every child you dispatch. Quota failover is
horizontal: it changes the target inside one routing cell. Escalation is
vertical: it moves work to the next stage. A failover never changes the stage.

## Dispatch
1. Always dispatch the cell's primary. Never estimate or check quota
   percentages beforehand.
2. Before a dispatch, skip every target that has a live refusal. `t3c quota
   show` lists the live refusals.
3. In a Task that keeps records, append the `dispatch-intent` record before the
   call. After the call append `dispatch-confirmed`, and when the child
   completes, its `result`.
4. After each dispatch, compare the response's provider instance, model and
   options with your brief, and check that a new worktree on a new branch
   exists. Cancel a child that landed wrong within seconds, and verify that the
   owner's checkout is clean.

## When a child fails
Classify the failure by the child's own response. Use the tool's error label
only when the child gave no response. Then act on the cause.
- **Quota refusal:** a usage limit, an exhausted quota, a credits error
  (HTTP 402), or a rate limit whose retry time is over 5 minutes or is missing.
  1. Record it: `t3c quota record --instance I --model M --kind K
     --observed-at T --evidence TEXT`. K is `usage-limit`, `quota-exhausted`,
     `credits` or `rate-limit`. For a pool, I is the account.
  2. Clear the refused dispatch: append a `reconciliation` record for it with
     the outcome `stopped-and-preserved`. A refused child has no recordable
     result, and the store needs the dispatch cleared before the next Worker
     intent. Do the same for a refused Reviewer.
  3. Dispatch the next target of the same cell, with the same brief and a new
     `clientRequestId`. Start at the round's previous start commit, or at the
     latest completed result's commit. Then append the new `dispatch-intent`.
  4. The refused child's commits, if it made any, stay on its branch. The new
     brief may name that branch for read-only reference. A `delegate_task`
     cannot reopen another child's worktree, and work that was not committed
     is lost to a new worktree.
- **Transient error:** a rate limit with a retry time of 5 minutes or less, an
  "at capacity" answer, or a 5xx error. Retry the same target once after the
  stated wait (1 minute when none is stated). If it fails again, treat the
  target as unavailable for this dispatch only. Record nothing: this is not
  quota evidence.
- **Environment block:** a missing tool, a permission or a hook. The target is
  unavailable for this dispatch. Record no quota evidence and rotate no account.
- **Failure inside the work:** not a failover. Handle it as a review finding or
  as a hard stop (see Escalation).

## Refusal memory
- A recorded refusal keys on the provider instance and model; in a pool, on the
  account and model.
- Skip that key until 30 minutes after the refusal was observed, then try it
  again.
- A reported reset time is recorded but never used for skipping: it can be
  wrong.
- In a pool, start at the active account and go in pool order, wrapping around
  once. Skip an account that does not offer the model or has a live refusal for
  it. Recording a refusal by the active account moves the active pointer to the
  next account. Walking accounts is not a fallback.
- When no target in the cell can serve, wait until the earliest refusal
  expires, then try again. A dry cell never escalates to another stage. At
  stage 3 the item waits, and you tell the manager.

## Escalation
A Worker that fails its second repair commits its work in progress and returns
an `<ESCALATION_REQUEST>` block. Then:
1. Save the returned text to a file and run
   `t3c brief check-escalation --result FILE`. If it refuses the block, do not
   guess the missing facts. Read the Worker's worktree (read only) for the
   branch, the WIP commit and the modified files, take the attempts and the
   root cause from the Worker's text, correct the block, and check it again.
2. In the Worker's worktree (read only), confirm that the WIP commit exists, is
   the branch's head and has the subject `wip(escalation): ...`, and that the
   tree is clean.
3. Render the continuation brief with `t3c brief render --template
   continuation --original-brief ORIGINAL --escalation FILE --values VALUES
   --out BRIEF`. ORIGINAL is the brief the failed Worker received. In VALUES,
   `START` is the WIP commit, `REVIEW_BASE` is unchanged and `STAGE` is the next
   stage. Run `t3c brief check BRIEF`.
4. Dispatch BRIEF with `delegate_task` on the next stage's primary, with a new
   `clientRequestId`. Quota failover applies to that dispatch as above. The
   round starts at the WIP commit: a hard-stopped Worker completes its turn, so
   the commit is a recordable result.
5. Never pass the failed Worker's transcript.
6. Record the escalation. Run `t3c route escalate` when the helper has that
   command. Until then append an entry to {{LEAD_LOG}} with the role, the
   stages, the reason `worker-hard-stop`, a short evidence text and the WIP
   commit.

These are not escalations: an environment block, a quota refusal and a
question about the spec. Report an environment block or a spec question to
whoever can resolve it.

Other triggers move work to the next stage, and you record each of them:
- a review finding still open after 2 Corrections rounds: run the next
  Corrections round at the next stage;
- a review deadlock, where the Worker disputes a finding and the reviewer
  raises it again: a reviewer at the next stage decides;
- a reviewer that fails twice for a reason other than quota: the next stage
  takes the review;
- a failure at stage 3: the item goes to the manager, then to the owner.

## Routing records
These rules apply to a Task whose routing is kept in the store.
1. A new issue or round starts with a fresh `t3c route input`, and so restarts
   at the Task's starting stage. Record a decision on it before any selection:
   `t3c route classify`, or `t3c route judge` with a reason such as "carries
   the Task's class into round N". Move above the starting stage only after you
   record the escalation, one stage at a time.
2. Capture the capabilities catalog once per thread with
   `orchestrator_capabilities`, and save the exact output with its true UTC
   capture time. Never label a file with another capture's time. Once
   `docs/cli/routing.md` says that selection enforces the escalation chain,
   reuse that file for the whole Task: a catalog may be up to 24 hours old.
   Capture again only after a dispatch error that suggests a provider change: a
   missing provider or model, a disabled instance or a changed option.
3. Until then, choose each target from the matrix's cell, and record the cell,
   the target and its position in {{LEAD_LOG}}. Once it says so, select with
   `t3c route select` against the saved file.
