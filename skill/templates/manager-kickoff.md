# Manager kickoff

This thread restarts with no memory of earlier work. This message holds pointers only, names no task and repeats no state. The work state must be rebuilt from the records.

## Pointers

- Skill name: {{SKILL}}
- Repository: {{REPOSITORY}}
- Records directory (the helper's `--local-dir`): {{RECORDS_PATH}}
- Routing policy: {{ROUTING_POLICY_PATH}}
- Takeover dump (the old manager's handover document; it can be the temporary prose manager's): {{TAKEOVER_DUMP_PATH}}
- Checkpoint sequence: {{CHECKPOINT_SEQ}}
- Checkpoint digest: {{CHECKPOINT_DIGEST}}

## First actions

1. Load the skill named above before any other action.
2. List the repository's works with `t3c work list` and read the latest handoff and restart events with `t3c work show`: they, not this message, say what is open.
3. Recompute the saved prefix with `t3c work snapshot` using its `--through-seq` option, and compare the digest with the one above and with the one in the record; on a mismatch stop, report, and append nothing.
4. Follow `docs/restart.md` for a restart and `docs/pilot.md` for the handoff and the pilot, in the helper's repository.
5. Your first action on the work is to reconcile the takeover dump named above against Git, GitHub, Linear and the open threads, as in the Takeover section of `docs/pilot.md`. Until the writer transfer, the prose manager holds and dispatches nothing. Resume nothing and dispatch nothing until that is done, the records, T3 and Git agree, and the hold's end condition named in the handoff record is met.
