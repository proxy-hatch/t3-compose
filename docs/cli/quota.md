# Quota commands

Use the [shared CLI contract](../cli.md) for invocation, global options, output,
errors and registration. These commands use only `args.local_dir`, selected by
`--local-dir` before `quota`. Every call reloads the version-3 config with
`load_matrix_config` and the state with `load_quota_state`; there is no cache.
Missing state is invalid configuration; commands never initialize or repair it.

```text
t3c quota show
t3c quota record --instance I --model M --kind K --observed-at T [--reset-at T] --evidence TEXT
t3c quota activate --instance I --reason TEXT
```

`record` remembers a quota refusal, keyed by direct provider instance and model,
or by pool account and model. K is `usage-limit`, `quota-exhausted`, `credits`
(including OpenRouter 402), or `rate-limit` (retry over five minutes or missing).
Transient errors are not quota refusals. Evidence must contain non-whitespace
text and be at most 512 characters; it is stored verbatim. Omitted reset is null.
Both timestamps use UTC `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` with 1–6 fractional digits.
Observation cannot be after the command's start time; reset cannot precede it.

The instance/model pair must be routable: a selection in the manager, any matrix
cell (primary, quota fallback or pre-pass), or any helper cell must name that
instance/model directly, or name a pool containing that account with that model.
Selection options do not affect this memory key. If the refusing account is the
active account, the pointer moves to the next account of its pool in configured
order, wrapping around; otherwise the pointer stays. `note` stays unchanged.

Refusals expire at observation plus `quota.refusalRetryMinutes` (30 minutes in
the example). They are live strictly before expiry; equality is expired. Reported
reset times are hints and never affect expiry. Every command checks every stored
refusal time, including expired entries. `record` drops expired entries and keeps
all live entries in stored order before appending the new entry. Duplicate keys
are valid; the later expiry wins for skipping. Pruning limits growth toward the
16 KiB bound, but never discards live evidence to fit: an oversized candidate is
refused. The selection side that skips live keys arrives with MX-3a1.

`show` is read-only, takes no lock and returns the full validated state plus live
refusals with `expiresAt`. Old `quotaObservations` remain readable and are returned
verbatim. Writes preserve them, and these commands no longer evaluate their times
or use them to gate anything.

`activate` requires a configured pool member and nonempty `--reason`. It sets the
active pointer and replaces `note` with the reason verbatim. It preserves both
`refusals` (including expired entries) and `quotaObservations` exactly. Activating
the current account is allowed; activation does not clear refusals.

Success (exit 0) returns exactly:

- All commands: `state` (full validated state, after any update), `liveRefusals`
  (in stored order, each entry with `expiresAt`), `configDigest` (`sha256:<hex>`
  of config bytes read), and `stateDigest` (digest of bytes read for `show`, or
  canonical UTF-8 bytes published for writes).
- Writes also return `previousStateDigest`, the digest of bytes read under the
  lock. A no-op activation can have equal before/after digests.
- `record` also returns `activeMove`: `{"from": I, "to": J}` if the pointer
  changed, otherwise null. No evaluated `quota` mapping or `--status` remains.

Every write opens `agent-routing-state.lock` in the local directory with
`O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC`, mode 0600, and takes blocking exclusive
`fcntl.flock`. Config loads before the lock; state reads, validation, candidate
construction, replacement and digest computation occur under it. Closing the
lock descriptor releases it after publication or refusal; the lock file is never
deleted. Readers need no lock because atomic replacement exposes a complete file.

Mutations validate the whole candidate and its 16 KiB loader bound before calling
`jsonio.atomic_replace`. Writes use the [shared atomic JSON
contract](../records.md#atomic-json-apis-and-temporary-ownership): same-directory
0600 temporary, fsync, atomic replacement, directory fsync and cleanup. An I/O
failure after publication can leave the complete new state. Inspect with `show`
and compare digests/history before retrying; repeated `record` calls append again.
The diagnostic is `quota publication failed; inspect state before retrying`.

Exit 2 means invalid arguments/update (including an unroutable pair, unknown
activation member, kind, timestamp, evidence/reason or oversized candidate), or
lock/publication I/O failure. Exit 3 means absent, unreadable, malformed, oversized
or invalid config/state, a version-2 config, or an invalid stored refusal time.
Both have empty stdout and sanitized stderr that never echoes values or paths.
Validation refusals preserve state bytes. These commands emit no exits 4/5 and
append no work-record event.
