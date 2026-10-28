# Run a command with an App token

`publish run` gives one child a scoped installation token and revokes it after
the child terminates. It implements GA-2 part 2, consuming the shared launch
interface delivered by GA-2a. GA-3's Git, netrc and command-form preflight
refusals remain a separate Task.

## Invocation and prerequisites

From the repository directory where the child must run:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <root>/bin/t3c --local-dir <state-directory> publish run --status-file <new-file> -- CMD [ARGS...]
```

Quote substituted paths. Use stable CPython 3.15 through uv's global pin.
The shared [CLI contract](../cli.md) supplies global options and isolation.
`--status-file FILE` is required. Its parent must already exist, and FILE must
be unused, including no existing symlink. Each invocation has one exclusive
writer; callers must not share, remove or replace its status path while it runs.
No status directory is created. An existing path is rejected without modifying
it, before config loading or minting, with a diagnostic that it belongs to an
earlier invocation. Its contents are never fresh evidence for this invocation.

An explicit `--` and a nonempty command are required. `argparse.REMAINDER`
preserves every child argument, including `--text`, `--help`, and option values.
Global options precede `publish`. Global `--text` does not wrap child output.
The child's working directory is the runner's starting directory; the helper's
installation root does not select it. The helper never uses a shell launcher.

App config comes only from `args.local_dir / "github-app.json"`, using GA-1's
strict loader. Without an override, the shared CLI selects `<root>/.local`.
See [GitHub App prerequisites](github_app.md): macOS Keychain, security,
OpenSSL, an installed App and HTTPS access to GitHub. Git is needed for Git
commands; the credential overrides require Git 2.31 or later. There is no new
Python dependency. At a Keychain prompt choose **Allow**, not **Always Allow**.

## Streams, exit codes and status evidence

Child stdout and stderr pass through unchanged. The handler returns
`PassThrough` once a child has started, with no JSON envelope or extra stdout.
Helper diagnostics use safe stderr, never exception messages, supplied paths,
commands, environment values, keys, JWTs or tokens.

| Outcome | Exit | Status file |
|---|---|---|
| Child exits normally | Child's status, including 0, 1, 2, 3, 75 or another 0–255 value | Final started record below. |
| Child dies from signal N | 128 + N | Final started record with normalized status. |
| Missing separator/command | 2 | `{"childStarted":false,"refusal":"invalid-input"}` |
| Invalid App config | 3 | No-start record, refusal `invalid-config`. |
| Mint failure | 1 | No-start record, refusal `mint-failed`. |
| Interruption before launch | 1 | No-start record, refusal `interrupted`. |
| Setup, intent-write or process-start failure | 2 | No-start record, refusal `setup-failed`, `status-failed` or `start-failed`. |
| Revocation failure after child exits | Child's status unchanged | Final started record, revocation `failed`; safe expiry warning on stderr. |

The runner uses [atomic_replace](../records.md#atomic-json-apis-and-temporary-ownership)
for canonical, atomic mode-0600 writes. Immediately before launch it records:

```json
{"childStarted":true,"childStatus":null}
```

This is uncertain intent, **not proof that a child ran**. Only a successful
return from the atomic writer permits launch. After termination and cleanup:

```json
{"childStarted":true,"childStatus":0,"revocation":"ok"}
```

`childStatus` is the actual normalized child status, and `revocation` is `ok`
or `failed`. A process-start failure replaces intent with a no-start record
when writing remains possible, and still revokes any minted token.

An atomic-write exception can occur after rename, leaving the complete new
record visible but durability uncertain. On a failed final write the runner
inspects the record and warns; it keeps the child status and never writes a
false no-start record over an actual started outcome. The prior intent may
remain, or the complete final record may already be visible. A failed intent
write prevents launch even if intent became visible. Its no-start replacement
is attempted; if that also fails, intent can remain without any child having run.
Diagnostics distinguish an observed complete record from an unconfirmed write.

Reconstruction requires a fresh, exclusively owned path. With that condition,
no file after exit means no child started. `true/null` means unknown and needs
reconciliation (for example, a read of the remote ref) before any retry. Neither
exit code nor child output proves startup. Do not treat an existing file from
an earlier invocation, a missing file removed externally, or an incomplete
write as fresh acceptance evidence.

The parser cannot write a status file before a handler is dispatched. Missing
`--status-file`, unknown options, help, module-loading failures and interpreter
refusal produce no new status file. Invalid/unusable status paths or persistence
errors may also prevent any record. Handled invalid command syntax, config and
operational failures attempt the no-start form once a usable fresh path exists.
Existing-file rejection preserves the old file and explicitly warns about it.

## Environment, signals and cleanup

Before any future GA-3 inspection, the runner copies and sanitizes the inherited
environment, removing `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`,
`GITHUB_ENTERPRISE_TOKEN`, `GH_HOST`, `GH_DEBUG`, `GIT_ALLOW_PROTOCOL`,
`GIT_SSL_NO_VERIFY`, every `GIT_TRACE*`, `GIT_CURL_VERBOSE`,
`GIT_REDACT_COOKIES`, `GIT_CONFIG`, and every `GIT_CONFIG_*` (including
`GIT_CONFIG_PARAMETERS`, global/system-file overrides and `GIT_CONFIG_NOSYSTEM`).
Unrelated variables remain unchanged; the parent's environment is not mutated.

Only after that future preflight seam does execution add controlled
`GIT_CONFIG_COUNT/KEY_n/VALUE_n`: an empty `credential.helper` clears all
matching helpers, including URL-specific ones; one HTTPS github.com helper
reads `GH_TOKEN` when Git executes it; `protocol.ssh.allow=never` refuses SSH.
The fresh token is injected only into `GH_TOKEN`. The helper/config/argv contain
no credential literal. The key and JWT stay inside GA-1's minting lifecycle.
The Git helper answers only `get`; it supplies no token for another host.
GA-3 will add the complementary refusal checks for unsafe underlying Git config
and command forms; this part alone does not claim those checks.

Non-raising temporary handlers queue SIGINT, SIGTERM and SIGHUP. Signals before
launch refuse startup; a signal during launch is forwarded once the real handle
is available. During execution, queued signals are forwarded within the bounded
wait interval (normally 0.1 seconds). Wait failures cannot cause early revocation;
the runner continues waiting/reaping and forwarding. It never kills a child just
to finish cleanup and does not supervise grandchildren or process groups.
Handlers remain installed through revocation and status recording, then previous
handlers are restored. A signal after the child exits cannot change its result.
SIGKILL, process crashes and machine failure cannot guarantee cleanup.

One GA-1 `TokenLease` surrounds both minting and child use, so malformed mint
responses still receive GA-1's cleanup. Revocation follows child termination,
including failure and interruption. A failed or unexpectedly interrupted cleanup
warns with redacted `expires_at` (or `unknown`) and retains a known child result.

## Reuse and validation

`run_command(command, status_file, *, local_dir) -> RunResult` is the reusable
in-process entry for PB-1. Call it in the main thread with a nonempty argv list
(without the CLI separator), from the intended repository directory. It returns
actual `child_started`, `child_status`, `refusal`, `revocation` and `code` facts;
`record()` produces exactly the applicable public status object above. Unlike
disk intent, `child_started=True` in this result means a handle was obtained.
PB-1 consumes these facts directly, without parsing streams or rereading status
to infer startup. `code` is the helper refusal code or normalized child status.

`sanitized_environment(base)` supplies GA-3's token-free inspection environment.
`child_environment(base, token)` requires that sanitized base and adds only the
controlled execution variables. GA-3's future checks belong at the marked seam
before minting, with the same captured cwd. HC-1 can consume the shared
`process.start`; the token runner owns its own handle, signals, wait and cleanup.
Neither shared `process` nor `jsonio` is reimplemented here.

Offline entry tests inject synthetic minting/revocation, use explicitly allowed
printenv/sleep and guarded Python children, and isolate Git's config and helpers.
Run focused discovery with `-p test_token_run.py`, then full
[SUITE](../testing.md#suite-and-labels). Offline passes are not live evidence.

After merge, only the Engineering manager runs this read-only live check once,
from the t3-compose repository root, choosing an unused status filename:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S bin/t3c publish run --status-file .local/ga-2-live-status.json -- git ls-remote https://github.com/proxy-hatch/t3-compose.git refs/heads/main
```

Confirm passed-through output, child exit status, the status record and revocation.
No live pass is claimed by this implementation's offline tests.
