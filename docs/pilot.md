# Pilot handoff and checklist

This document sets the formal handoff, writer transfer, takeover procedure, and execution checklist for the pilot.
For background and specifications, refer to the design sections [Pilot and validation](superpowers/specs/2026-10-27-t3-compose-design.md#pilot-and-validation), [Taking over existing Claude work](superpowers/specs/2026-10-27-t3-compose-design.md#taking-over-existing-claude-work), and [Manager threads and bases](superpowers/specs/2026-10-27-t3-compose-design.md#manager-threads-and-bases), as well as [Manager restart](restart.md), [Record format and schema contract](records.md), and [Records commands](cli/records.md).

Values in angle brackets (such as `<repository>`), `sha256:...` digests and the example sequence numbers are placeholders: replace them with the values the commands print.

In shell blocks below, the helper function is defined once:

```bash
t3c() { "$PY" -I -S "$ROOT/bin/t3c" --local-dir "$STATE" "$@"; }
```

## Order of gates

The handoff follows these ordered gates:

1. Every Task through PH-1 has merged and the owner's checkout includes them;
2. The live checks L1 to L7 have passed and L5 has published at least one pull request through the helper;
3. Shawn gives the go-ahead (plan decision 4);
4. The build manager writes the handoff record with its prefix checkpoint, reconciles its own writer, writes the writer-transfer with its own prefix checkpoint, renders the kickoff into its private briefs directory and sends it to the rapid-ship-bound thread (or Shawn does);
5. The pilot manager follows this document; after the writer transfer the build manager writes no rapid-ship record.

The hold remains in force until the pilot starts: nobody resumes, reviews, checks or publishes any rapid-ship task, and nobody chooses the pilot task. Writing the handoff record does none of these things. Plan decision 16's temporary prose manager does not satisfy or remove this formal handoff gate: it reconciles old work and needs Shawn's approval to dispatch. Until the writer transfer, the prose manager holds and dispatches nothing.

## The handoff record

The handoff record is created as a work under the pilot repository's identity. The build manager writes it as the work's lead-role writer `lead:<work>@<repository>` (the only writer pair the store allows toward a manager is the manager writer `manager@<repository>` and the work's lead-role writer). `work.json` is the work record described in [Record format and schema contract](records.md).

The build manager runs:

```bash
t3c work new --repo "$REPO" --work "$WORK" --file work.json --writer "lead:$WORK@$REPO"
t3c work snapshot --repo "$REPO" --work "$WORK"
t3c event add --repo "$REPO" --work "$WORK" --kind handoff --file handoff.json --writer "lead:$WORK@$REPO"
```

A newly created work gives `lastSeq` 0 for the metadata-only prefix snapshot.
The `handoff` payload contains exactly these fields; `takeoverDumpPath` names the old manager's handover document (the takeover dump):

```json
{
  "pointers": {
    "skill": "<skill>",
    "repository": "<repository>",
    "recordsPath": "<records-dir>",
    "routingPolicyPath": "policy.json",
    "takeoverDumpPath": "<takeover-dump>"
  },
  "holdEndCondition": "Shawn approves pilot launch after handoff verification",
  "pilotCriteria": [
    "A real T3 writer on the intended branch/base, followed by a separate Reviewer in this first pilot without a Feature lead; lead enablement follows L10/FL-2.",
    "The repository's required checks and evidence for every acceptance criterion.",
    "A recorded outcome for every review finding, with re-review after edits.",
    "Preserved unrelated work and no manager-authored project edits.",
    "Reconstruction of the same work state after a deliberate manager context restart."
  ],
  "nextAction": "reconcile the takeover dump against Git, GitHub, Linear and the open threads",
  "checkpoint": {
    "lastSeq": 0,
    "digest": "sha256:..."
  }
}
```

## The writer transfer

The writer transfer transfers coordination authority. It requires a reconciliation of the old writer first: a `reconciliation` event recording that the old writer has stopped cleanly and preserved all artifacts. This reconciliation of the old writer is distinct from the later reconciliation of the old Claude manager's work described in the Takeover section.

The build manager appends the old writer reconciliation:

```json
{
  "item": {
    "kind": "takeover",
    "ref": "lead:<work>@<repository>"
  },
  "outcome": "stopped-and-preserved",
  "t3Facts": {
    "writer": "lead:<work>@<repository>",
    "state": "stopped",
    "childrenReconciled": true,
    "evidence": "dispatches concluded"
  },
  "gitFacts": {
    "commitsPreserved": true,
    "uncommittedPreserved": true,
    "evidence": "clean checkout",
    "worktreePath": "worktrees/lead"
  }
}
```

If the work has a `lead-assignment` and the old writer is its lead-role writer, `t3Facts` also needs a `thread` entry equal to that lead's thread. The handoff record's work has no lead assignment, so the example omits it.

Append the event:

```bash
t3c event add --repo "$REPO" --work "$WORK" --kind reconciliation --file recon.json --writer "lead:$WORK@$REPO"
```

Next, take a new snapshot for the transfer's own pre-transfer checkpoint:

```bash
t3c work snapshot --repo "$REPO" --work "$WORK"
```

Then append the `writer-transfer` event with `from` (current writer), `to` (the other writer ID on the same repository: `manager@<repository>` or `lead:<work>@<repository>`, differing from `from`), `reconciliationRef` (the sequence of the reconciliation event above), and `checkpoint`:

```json
{
  "from": "lead:<work>@<repository>",
  "to": "manager@<repository>",
  "reconciliationRef": 2,
  "checkpoint": {
    "lastSeq": 2,
    "digest": "sha256:..."
  }
}
```

```bash
t3c event add --repo "$REPO" --work "$WORK" --kind writer-transfer --file transfer.json --writer "lead:$WORK@$REPO"
```

After this event, `effectiveWork.writer` (shown by `t3c work show --repo "$REPO" --work "$WORK"`) reflects the new writer. Any subsequent append by the old writer is refused.

Before taking any action, the recipient verifies:
1. Recompute the prefix for both the handoff checkpoint and the transfer checkpoint using:
   ```bash
   t3c work snapshot --repo "$REPO" --work "$WORK" --through-seq "$N"
   ```
2. Compare both recomputed digests with the saved checkpoint digests.
3. Check with `t3c work show --repo "$REPO" --work "$WORK"` that `effectiveWork.writer` matches its own writer identity.

## The kickoff

Render the kickoff brief from the kickoff template using:

```bash
t3c brief render --template skill/templates/manager-kickoff.md --values values.json --out kickoff.md
```

`values.json` contains strings for the seven placeholders: `SKILL`, `REPOSITORY`, `RECORDS_PATH`, `ROUTING_POLICY_PATH`, `TAKEOVER_DUMP_PATH`, `CHECKPOINT_SEQ`, and `CHECKPOINT_DIGEST`. `TAKEOVER_DUMP_PATH` is the path of the old manager's handover document.
The command prints `{"digest": "sha256:..."}`. At first start, the checkpoint values come from the writer-transfer checkpoint (the latest saved prefix containing the handoff); for any subsequent restart, they come from that restart's checkpoint. The template contains pointers only.

## Takeover

The takeover dump is the old manager's handover document. It can be the handover document of the temporary prose manager (plan decision 16). Until the writer transfer, the prose manager holds and dispatches nothing. The dump's path is private: it goes only into the kickoff's takeover-dump pointer and the handoff record's `takeoverDumpPath`, never into a tracked file.

The new manager's first action on the work is to reconcile the dump against Git, GitHub, Linear and the open threads, before anything is dispatched:

- Git: branches, commits, worktrees and dirty files, against what the dump says.
- GitHub: pull requests and review heads.
- Linear: the state of each issue the dump names.
- The open threads: children that may still be running, active writers and unanswered decisions.

Before taking over any work, reconcile the recorded state with live Git and T3 state: the old manager's workflow journals, worktrees, branches, commits, dirty files, pull requests, review heads, and unanswered decisions.
Being quota-blocked or quiet does not prove that its children have stopped. Establish that old dispatch has ceased and resolve active writers before T3 takes ownership. Preserve every worktree and every uncommitted change. A failed or null workflow result is incomplete evidence, not proof that nothing was written. An existing dirty tree cannot be transferred by starting a new isolated child at the manager's HEAD. For an existing worktree, verify its base, branch, dirty state and absence of a live writer before assigning a recovery brief. A recovery child may use shared isolation from the manager with an explicitly owned existing worktree path, working only there; it must not edit the manager's checkout. No automatic reset, stash, cleanup or replay.

Record each finding with:

```bash
t3c event add --repo "$REPO" --work "$WORK" --kind reconciliation --file recon-item.json --writer "$WRITER"
```

Valid item kinds include `worktree`, `dispatch`, `operation`, and `temp-file`.

## Pilot checklist

The pilot follows these steps in order:

- **S1** Hold. The hold is in force. No rapid-ship task is resumed, reviewed, checked or published and no pilot task is chosen until the handoff gates pass and Shawn approves the first dispatch. Until the writer transfer, the prose manager holds and dispatches nothing.
- **S2** Verify the handoff. Recompute and compare both saved prefixes; confirm the writer is the pilot manager's. Stop on any mismatch.
- **S3** First restart. The thread's start is the first restart: load the skill, rebuild state from the records, as in `restart.md`. It happens before any pilot Task is dispatched.
- **S4** Reconcile the takeover. Reconcile the takeover dump against Git, GitHub, Linear and the open threads, as detailed in the Takeover section; dispatch nothing before this reconciliation is done.
- **S5** Test the shared-isolation exception. Before any recovery brief relies on it, test that a child with shared isolation can work in an explicitly owned existing worktree outside the manager's checkout and cannot edit the manager's checkout. The design marks this exception as not yet verified.
- **S6** Confirm publication. Confirm that rapid-ship's publication commands can publish a given commit. If they push only a branch, confirm that the branch's head equals the reviewed head before and after the push.
- **S7** Choose one pilot Task. One bounded unfinished task whose requirements are approved and whose files have no active writer; choose the lane, criticality and scope from the Task's risk facts (the floors apply).
- **S8** First dispatch. A real T3 writer on the intended branch and base, with the `dispatch-intent` recorded before the dispatch. This first pilot has no Feature lead.
- **S9** Separate Reviewer. A Reviewer separate from the writer reviews the result at the exact head.
- **S10** Checks and evidence. The repository's required checks and evidence for every acceptance criterion are recorded at the final head.
- **S11** Findings and re-review. Every review finding has a recorded outcome, and a re-review follows each edit.
- **S12** Preserved work. Unrelated work is preserved and the manager authors no project edit.
- **S13** In-flight restart. Restart the manager once while a dispatched pilot Task is in flight, from the same thread that dispatched it, as in `restart.md`.
- **S14** Same work state. After that restart the records plus live T3 and Git state give the same work state as before it.
- **S15** One bounded agy task, through the same quality standard.
- **S16** One design-refinement task, through the same quality standard.
- **S17** Evidence, not claims. Confirm receipt and validation of the required evidence; do not count Worker claims or the number of agents as success.
- **S18** Router pilots, as in the Router pilots section.
- **S19** Failure and refusal paths are tested as well as success. An injected quota response is labeled simulated; live quota failover stays unverified until an actual exhaustion is observed; no simulated test counts as a passing live integration.

Order requirement: S1 before S4 before S8, and S3 before S8.

### Criteria map

| Criterion | Design demonstration | Checklist steps |
|---|---|---|
| C1 | A real T3 writer on the intended branch/base, followed by a separate Reviewer in this first pilot without a Feature lead; lead enablement follows L10/FL-2. | S8, S9 |
| C2 | The repository's required checks and evidence for every acceptance criterion. | S10 |
| C3 | A recorded outcome for every review finding, with re-review after edits. | S11 |
| C4 | Preserved unrelated work and no manager-authored project edits. | S12 |
| C5 | Reconstruction of the same work state after a deliberate manager context restart. | S3, S13, S14 |

### Other pilot requirements in the design

| Requirement | Checklist steps |
|---|---|
| The first restart before any pilot Task is dispatched | S3 |
| The in-flight restart | S13 |
| The bounded agy task and the design-refinement task | S15, S16 |
| Confirming evidence rather than claims | S17 |
| The router pilots | S18 |
| Failure and refusal paths | S19 |

## Evidence

The pilot gathers concrete, verifiable evidence rather than claims:

- Commit hashes and branch heads verified before and after each dispatch.
- Recorded event logs in the records directory covering dispatch intent, confirmation, and completions.
- Verified test execution logs, check outcomes, and acceptance evidence for each criterion.
- Recorded findings, resolutions, and re-review events for all identified review comments.
- Preserved worktree inventory confirming untouched external worktrees and no manager modifications.
- Demonstrated reconstruction of identical state across deliberate manager restarts.

## Router pilots

Router pilots follow these rules:

- Use synthetic inputs until the formal helper-backed pilot starts, and label synthetic evaluations explicitly; then also use real task inputs recorded with each decision.
- The temporary prose exception grants no earlier helper-router access to rapid-ship work.
- Inspect the router's decisions on examples covering engineering and non-engineering work, mixed scope, and easy-but-sensitive tasks.
- Record mistaken cheap routes separately from unnecessary expensive routes.
- Shawn's qualitative feedback tunes the configured threshold.
- No accuracy study is required before the pilot.
