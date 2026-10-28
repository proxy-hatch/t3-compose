# Run a command with an App token

`publish run` gives one child a scoped installation token and revokes it after
the child terminates. It implements GA-2's lifecycle and GA-3's Git, netrc and
command-form refusals before minting, using GA-2a's shared launch interface.

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
OpenSSL, an installed App and HTTPS access to GitHub. Every invocation needs Git
for preflight; its credential overrides require Git 2.31 or later. There is no new
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
| Preflight refusal before App setup/minting | 1 | No-start record with one of the closed preflight kinds below. |
| Invalid App config | 3 | No-start record, refusal `invalid-config`. |
| Mint failure | 1 | No-start record, refusal `mint-failed`. |
| Interruption before launch | 1 | No-start record, refusal `interrupted`. |
| Setup, intent-write or process-start failure | 2 | No-start record, refusal `setup-failed`, `status-failed` or `start-failed`. |
| Unexpected pre-start helper or cleanup failure | 2 | No-start record with the public stage kind, when persistence is possible. |
| Revocation failure after child exits | Child's status unchanged | Final started record, revocation `failed`; safe expiry warning on stderr. |

Unexpected pre-start failures emit `t3c publish run: command failed (<class>);
check local inputs and helper installation`, never exception values. Expected
config, operational mint, launch and filesystem failures keep the mapped codes
above. Unexpected no-start revocation or record failures take exit-2 precedence;
required cleanup and status persistence are attempted first. Once a child has
exited, its status still wins through every cleanup/recording failure.

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

Before preflight inspection, the runner copies and sanitizes the inherited
environment, removing `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`,
`GITHUB_ENTERPRISE_TOKEN`, `GH_HOST`, `GH_DEBUG`, `GIT_ALLOW_PROTOCOL`,
`GIT_SSL_NO_VERIFY`, every `GIT_TRACE*`, `GIT_CURL_VERBOSE`,
`GIT_REDACT_COOKIES`, `GIT_CONFIG`, and every `GIT_CONFIG_*` (including
`GIT_CONFIG_PARAMETERS`, global/system-file overrides and `GIT_CONFIG_NOSYSTEM`).
Unrelated variables remain unchanged; the parent's environment is not mutated.

Only after preflight succeeds does execution add controlled
`GIT_CONFIG_COUNT/KEY_n/VALUE_n`: an empty `credential.helper` clears all
matching helpers, including URL-specific ones; one HTTPS github.com helper
reads `GH_TOKEN` when Git executes it; `protocol.ssh.allow=never` refuses SSH.
The fresh token is injected only into `GH_TOKEN`. The helper/config/argv contain
no credential literal. The key and JWT stay inside GA-1's minting lifecycle.
The Git helper answers only `get`; it supplies no token for another host.
The complementary checks below refuse unsafe underlying Git config and command
forms before App config loading, runtime creation, lease creation or minting.

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

`sanitized_environment(base)` supplies the token-free inspection environment.
`child_environment(base, token)` requires that sanitized base and adds only the
controlled execution variables. Inspection and execution use the same captured
cwd and underlying configuration selection. HC-1 can consume the shared
`process.start`; the token runner owns its own handle, signals, wait and cleanup.
Neither shared `process` nor `jsonio` is reimplemented here.

Offline entry tests inject synthetic minting/revocation, use explicitly allowed
printenv/sleep and guarded Python children, and isolate Git's config and helpers.
Run focused discovery with `-p test_token_preconditions.py` and
`-p test_token_run.py`, then full
[SUITE](../testing.md#suite-and-labels). Offline passes are not live evidence.

After merge, only the Engineering manager runs this read-only live check once,
from the t3-compose repository root, choosing an unused status filename:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S bin/t3c publish run --status-file .local/ga-2-live-status.json -- git ls-remote https://github.com/proxy-hatch/t3-compose.git refs/heads/main
```

Confirm passed-through output, child exit status, the status record and revocation.
No live pass is claimed by this implementation's offline tests.

## Preflight API and refusal kinds

`token_preconditions` is an ordinary imported module, with no registry entry.
`check(command, *, env, cwd)` takes a nonempty argv list, the exact sanitized,
token-free base and the captured starting directory. It mutates neither input,
returns `None` on success, and raises `Refusal(kind)` for an expected refusal.
The exception accepts only the following closed vocabulary. The CLI prints
`t3c publish run: <kind>` on stderr, no stdout, and exits 1. The atomic mode-0600
record is exactly `{"childStarted":false,"refusal":"<kind>"}`.

| Kind | Refused condition |
|---|---|
| `shell-command` | A recognized shell is CMD, by supplied or resolved basename. |
| `git-directory` | Git global `-C`, separate or joined. |
| `git-config-option` | Git global `-c`, separate or joined, or `--config-env`. |
| `git-option` | Another Git global option outside the bounded allowlist below. |
| `git-config` | Git cannot be resolved/read, times out, exits nonzero, supplies malformed NUL data, or cannot parse an SSL boolean. |
| `git-config-overrides` | Git does not apply the token-free `GIT_CONFIG_COUNT` capability probe. |
| `git-extraheader` | `http.extraheader` or `http.<url>.extraheader`, with any value or no value. |
| `git-url-rewrite` | `url.<base>.insteadOf` or `url.<base>.pushInsteadOf`, with any value or no value. |
| `git-trace-env` | `trace2.envVars`, with any value or no value. |
| `git-ssl-verify` | Any `http.sslVerify` or `http.<url>.sslVerify` value Git interprets as false. |
| `home-unset` | HOME is absent or empty, so netrc cannot be checked. |
| `netrc-unreadable` | Reading the selected HOME's `.netrc` fails, except absence. |
| `netrc-github` | Netrc contains a case-insensitive `github.com` token. |
| `netrc-default` | Netrc contains a case-insensitive `default` token. |

Only the first detected refusal is reported. Setting values, full keys with
subsections, paths, command text and Git stderr are never public diagnostics.
Unexpected exceptions retain CD-2's exit 2, class-only diagnostic and
`setup-failed` no-start record; interruptions retain `interrupted` and exit 1.
Status-write failures and signal restoration retain the lifecycle rules above.

Every Git inspection calls `process.run` through the module, with list argv,
no shell, the captured cwd, DEVNULL stdin/stderr, byte stdout and a five-second
timeout per invocation. For a recognized explicit Git command, inspection uses
that same executable spelling; otherwise it uses `git` from the base PATH.
Git reads its own effective system, global, local and worktree config, including
normal and conditional includes. The helper parses Git's NUL records at only
the first newline, retaining embedded value newlines and empty/dotted subsections.
It recognizes section and variable names case-insensitively.

For SSL verification, Git's `--type=bool --get-regexp` supplies canonical
booleans for every matching occurrence. False spellings include `false`, `no`,
`off`, an empty value and numeric zero variants such as `00`, `+0`, `-0`, `0x0`
and `0k`. Unquoted surrounding whitespace and case follow Git's parser;
invalid values fail closed. True-like values and valueless true booleans pass.
An earlier false occurrence still refuses even if a later value is true.

After inspection, a separate token-free probe adds two harmless command-scope
config entries to a copy of the base. Random markers must appear at the tail
of Git's listing. This verifies the Git 2.31+ override capability rather than
trusting a version string, without injecting a credential helper or token.
Other inspection calls receive the exact base. Execution adds only GA-2's
controlled helper overrides and token. Inherited `GIT_CONFIG`/`GIT_CONFIG_*`
decoys therefore cannot hide the configuration execution will use.

Netrc checking preserves the interim tool's conservative whitespace-token scan:
outer double quotes and case are ignored, including trailing whitespace.
Matches in comments also refuse. An absent file or unrelated machine passes.
The helper does not parse or expose credentials.

Command detection resolves PATH relative to the captured cwd and follows
symlinks. Supplied and resolved basenames are conservatively case-folded, even
on case-sensitive filesystems: `SH` is a shell and `GIT` is Git. Git is recognized
by those basenames or actual file identity with the resolved PATH Git, including
hard links; lexical path equality is not required. Shell names are `sh`, `bash`,
`rbash`, `zsh`, `dash`,
`ash`, `ksh`, `ksh93`, `mksh`, `pdksh`, `csh`, `tcsh`, `fish` and `yash`.
Git scanning stops at the first subcommand: `git commit -c` is not global `-c`.
The allowed global flags are `-p`/`--paginate`, `-P`/`--no-pager`,
`--no-replace-objects`, `--no-optional-locks`, `--literal-pathspecs`,
`--glob-pathspecs`, `--noglob-pathspecs`, `--icase-pathspecs`, `--no-lazy-fetch`,
`-v`/`--version` and `-h`/`--help`. All other global forms conservatively refuse,
including `--git-dir`, `--work-tree`, `--namespace`, `--bare`, `--exec-path`
and `--`. These checks are not a general-purpose sandbox: arbitrary wrappers,
Git aliases/subcommands, hooks, replaced executables or config changes after
inspection can change execution. Use a trusted command and stable config.

Offline refusal tests use real entry dispatch under temporary HOME/state,
synthetic Git repositories/configuration and blocked mint/start seams. Each
refusal asserts exit 1, exact mode-0600 no-start status, no App setup or mint,
safe diagnostics and restored signal handlers. The offline guard's synthetic
global override masks automatic HOME/XDG global discovery: those source files
are explicitly included in the entry fixtures, not claimed as normal global
discovery coverage. A test-only process fixture selects a synthetic system file
for a separate entry refusal; the production base still contains no overrides.

After merge, the Engineering manager runs one refused form live, once, from
the repository root with an unused status path:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S bin/t3c publish run --status-file .local/ga-3-live-status.json -- git -C . ls-remote https://github.com/proxy-hatch/t3-compose.git
```

It must exit 1 with `git-directory`, write mode 0600 and exactly
`{"childStarted":false,"refusal":"git-directory"}`, and mint nothing. The
command-form check precedes any App setup or Git invocation. This live check
belongs to the Engineering manager; offline evidence does not claim its pass.
