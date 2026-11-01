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
     --observed-at T [--reset-at T] --evidence TEXT`. K is `usage-limit`,
     `quota-exhausted`, `credits` or `rate-limit`. For a pool, I is the
     account.
  2. Clear the refused dispatch: append a `reconciliation` record for it with
     the outcome `stopped-and-preserved`. A refused child has no recordable
     result, and the store needs the dispatch cleared before the next Worker
     intent. Do the same for a refused Reviewer.
  3. Take the next target of the same cell, with the same brief and a new
     `clientRequestId`. Start at the round's previous start commit, or at the
     latest completed result's commit. Append the new `dispatch-intent`
     before the call, then dispatch.
  4. The refused child's commits, if it made any, stay on its branch. The new
     brief may name that branch for read-only reference. A `delegate_task`
     cannot reopen another child's worktree, and work that was not committed
     is lost to a new worktree.
- **Transient error:** a rate limit with a retry time of 5 minutes or less, an
  "at capacity" answer, or a 5xx error. Retry the same target once after the
  stated wait (1 minute when none is stated). If it fails again, treat the
  target as unavailable for this dispatch only. Record no quota refusal: this
  is not quota evidence. Do record the skipped target and the reason in
  {{LEAD_LOG}}.
- **Environment block, capability or role block:** a missing tool, a permission
  or a hook; a provider, model or option that the saved catalog does not offer;
  or a driver kind blocked for the role. The target is unavailable for this
  dispatch. Record no quota refusal and rotate no account. Record the skipped
  target and the reason in {{LEAD_LOG}}, and take the next target of the cell.
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
3. Record the escalation before you select or dispatch at the next stage. Run
   `t3c route escalate` (see Routing records) with the role `worker`, the
   reason `worker-hard-stop`, `--wip-commit` set to the block's WIP commit, and
   as evidence a text file of at most 8,192 bytes that holds the checked block.
   The command dispatches nothing.
4. Render the continuation brief with `t3c brief render --template
   continuation --original-brief ORIGINAL --escalation FILE --values VALUES
   --out BRIEF`. ORIGINAL is the first Worker brief of the round, the one that
   is not itself a continuation: when the failed Worker was a continuation,
   use that same original again, never the continuation brief it received
   (the checker refuses a continuation inside a continuation). FILE is the
   latest block. In VALUES, `START` is the latest WIP commit, `REVIEW_BASE` is
   unchanged and `STAGE` is the next stage. Run `t3c brief check BRIEF`.
5. Append the `dispatch-intent` for BRIEF, then dispatch it with
   `delegate_task` on the next stage's primary, with a new `clientRequestId`.
   Quota failover applies to that dispatch as above. The round starts at the
   WIP commit: a hard-stopped Worker completes its turn, so the commit is a
   recordable result.
6. Never pass the failed Worker's transcript.

These are not escalations: an environment block, a quota refusal and a
question about the spec. Report an environment block or a spec question to
whoever can resolve it.

Other triggers move work to the next stage, and each is recorded:
- a review finding still open after 2 Corrections rounds: run the next
  Corrections round at the next stage;
- a review deadlock, where the Worker disputes a finding and the reviewer
  raises it again: a reviewer at the next stage decides;
- a reviewer that fails twice for a reason other than quota: the next stage
  takes the review;
- the lead stalled: a second issue of the Task needs stage 3, or a merge or
  post-merge verification fails twice. Tell the manager, who replaces the lead
  thread at the next stage. The new lead resumes from the Task log and the
  records, so keep both current;
- a failure at stage 3: the item goes to the manager, then to the owner.

## Routing records
These rules apply to a Task whose routing is kept in the store.
1. A new issue or round starts a new epoch at the Task's starting stage. Start
   it in exactly one of two ways:
   (a) `t3c route input`, then `t3c route judge` with that `inputRef`, its
       `policyDigest`, the lane, criticality and scope, and a reason such as
       "carries the Task's class into round N"; or
   (b) `t3c route classify` alone. It records its own input, so use the
       `inputRef` and `policyDigest` it returns.
   Never run `t3c route classify` after `t3c route input` for the same task: it
   exits 2 and appends no second input.
2. Every later `t3c route select` and `t3c route escalate` of the epoch passes
   that epoch's `inputRef` and `policyDigest`. A fresh input starts a new epoch,
   and the escalations of an earlier epoch authorize nothing in it.
3. Select each target with `t3c route select --role ROLE --stage N --input-ref
   REF --policy-digest DIGEST --catalog FILE --catalog-read-at UTC`. ROLE is
   `worker`, `reviewer` or `feature-lead`. Add `--round corrections` for a
   Worker's Corrections round and `--round re-review` for a Reviewer's
   re-review. The answer names the provider instance, model and options to
   dispatch. After `t3c quota record`, select again: the live refusal makes the
   command skip that target and return the next one of the cell. Exit 4 means
   that a current input, a decision, a catalog or a stage prerequisite is
   missing. Exit 5 means that no target of the cell can serve now: wait as
   described under Refusal memory, and never escalate for it.
4. Select at the Task's starting stage. A higher stage needs `t3c route
   escalate` records made after the epoch's input, for the same role and lane,
   chained one stage at a time: `--from-stage N` records the move to N + 1, and
   N must be the stage reached now. A record has a role and a reason: `worker`
   with `worker-hard-stop` or `finding-unresolved`, `reviewer` with
   `review-deadlock` or `reviewer-failure`, `feature-lead` with `lead-stalled`.
   Its evidence is a text file of at most 8,192 bytes. Without the chain,
   `t3c route select` exits 4 and names the stage reached.
5. For a Reviewer, `t3c route select --role reviewer --pass pre` selects only
   the cell's advisory pre-pass. When none is configured or none can serve, it
   exits 0 with `skipped: true` and appends nothing: go on to the review of
   record, `--pass record`, which is the default. A pre-pass is never the
   review of record.
6. For a helper, run `t3c route select --role verifier` or `--role text` with
   the input, digest, catalog and time only. A helper takes no `--stage`,
   `--round` or `--pass`, and needs no decision on the input.
7. Capture the capabilities catalog once per thread with
   `orchestrator_capabilities`, and save the exact output with its true UTC
   capture time, ending in `Z`. Never label a file with another capture's time.
   Pass the file and that time as `--catalog` and `--catalog-read-at`: a
   catalog is valid for 24 hours, and `t3c route select` refuses an older one.
   Reuse the file for the whole Task. Capture again only after a dispatch error
   that suggests a provider change: a missing provider or model, a disabled
   instance or a changed option.
