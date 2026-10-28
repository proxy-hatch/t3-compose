# Quota commands

Use the [shared CLI contract](../cli.md) for invocation, global options, output,
errors and registration. These commands use only `args.local_dir`, selected by
`--local-dir` before `quota`. Every call reloads and validates `agent-routing.json`
and `agent-routing-state.json` through the [configuration API](../configuration.md).
Missing state is invalid configuration: no command initializes, repairs or silently
replaces it. The Engineering manager owns this single state file; calls must be
serialized by that owner. There is no new lock, automatic retry or account rotation.

```text
t3c quota show
t3c quota record --instance I --status exhausted|available --observed-at T \
  [--reset-at T] --evidence TEXT
t3c quota activate --instance I --reason TEXT
```

`show` is read-only. It returns the complete state, both exact file-byte digests
and evaluated quota for every configured pool member. No observations means
`unknown`, not inferred exhaustion or availability.

`record` appends exactly `{observedAt, status, resetAt, evidence}` to the named
account's list, creating that list if absent. `--status`, `--observed-at` and
nonempty `--evidence` are required; omitted `--reset-at` stores null. It preserves
all prior observations on every account, in their existing order, with evidence
unchanged. It changes neither `activeProviderInstanceId` nor `note`. Quota is per
account, across all its pool models. Tool/correctness failures and missing models
are not quota evidence. The command records an explicit status; it does not infer
one from evidence text, catalog rollout gaps or failed work.

`activate` requires a configured pool member and a nonempty `--reason`. It changes
`activeProviderInstanceId` and replaces `note` with that reason verbatim, preserving
all observations. Activation is explicit and separate from evidence recording;
it does not declare availability, clear exhaustion or check the catalog. Activating
an exhausted account leaves its quota exhausted. Activating the current account
is allowed and records the new reason.

Both timestamps must be valid UTC `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` (1–6 fractional
digits). Observations cannot be in the future; reset cannot precede observation.
An old observation may be appended without sorting or discarding earlier entries.
Evaluation uses [routing.evaluate_quota](routing.md): latest observation time wins,
last list entry breaks ties; an exhausted reset strictly before now restores
`unknown`, and later `available` evidence clears exhaustion. Equality with now
still counts as exhausted. Never invent a reset time. Every command validates the
times in existing state too, including observations that no longer decide quota.

Success (exit 0) returns exactly:

- `state`: `{activeProviderInstanceId, quotaObservations, note}`, full validated
  state, after the update for mutating commands.
- `quota`: mapping of every configured pool member to `unknown`, `exhausted` or
  `available`, evaluated at the command's UTC start time.
- `configDigest`: `sha256:<hex>` of the config bytes read for this call.
- `stateDigest`: `sha256:<hex>` of the state bytes read by `show`, or the canonical
  UTF-8 bytes published by `record`/`activate`.
- `previousStateDigest`: mutating commands only; digest of the state bytes loaded
  before the update. A no-op activation can have equal before/after digests.

Mutations validate the entire candidate before calling `jsonio.atomic_replace`.
The candidate must fit the configuration loader's 16 KiB bound; growth beyond it
is refused without discarding history. Writes use the [shared atomic JSON
contract](../records.md#atomic-json-apis-and-temporary-ownership): same-directory
0600 temporary, fsync, atomic replacement, directory fsync and cleanup. An I/O
failure after publication can leave the complete new state. Inspect with `show`
and compare digests/history before retrying; repeated `record` calls append again.

Exit 2 means bad arguments/update (unknown member, status, timestamp, empty
evidence/reason, oversized candidate) or publication/helper failure. Exit 3 means
invalid, unreadable, absent, malformed or oversized current config/state, including
unknown state fields/members and invalid stored times. Both have empty stdout and
a sanitized stderr message. Validation refusals preserve state bytes. These
commands emit no exits 4/5 and append no work-record event. They do not cache
config/state; a valid edit takes effect on the next call.
