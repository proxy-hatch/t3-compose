# GitHub App scope check

`publish check` mints a token for one repository, verifies scope, then revokes it.
GA-2/GA-3 own child execution and Git preconditions; this command does neither.

## Invocation and prerequisites

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <root>/bin/t3c publish check
"$PY" -I -S <root>/bin/t3c --local-dir <state-directory> publish check
"$PY" -I -S <root>/bin/t3c --text --local-dir <state-directory> publish check
```

Use stable CPython 3.15 through uv's global pin. Replace and quote placeholders;
all global options precede `publish`. There are no command-specific options.
The [shared CLI contract](../cli.md) defines help, isolation and rendering.
Config is `github-app.json` under the selected local directory (default
`<root>/.local`); import/register read nothing. Prerequisites are HS-1, macOS
`/usr/bin/security`, `/usr/bin/openssl` (tested with LibreSSL 3.3.6), the App
installation, its Keychain item and HTTPS access to GitHub. No Python dependency
is added. At a Keychain prompt choose **Allow**, not **Always Allow**. After
merge the Engineering manager runs the first command once for L4 and compares
with the interim check; offline tests do not count as a live pass.

## Configuration

Synthetic example; all fields are required and unknown/duplicate keys fail:

```json
{"version":1,"app":{"slug":"fixture-worker","appId":12345,"installationAccount":"fixture-org","keychain":{"service":"fixture-service","account":"fixture-account"}},"repository":{"owner":"fixture-org","name":"fixture-repo","permissions":{"contents":"write","pull_requests":"write"}}}
```

`version` is integer 1 and `appId` a positive integer, never bool. Owner/account
are 1–39 ASCII alphanumeric/hyphen characters, with alphanumeric ends, and agree
ignoring case. Slug is 1–100 lowercase ASCII alphanumeric/hyphen characters with alphanumeric ends. Repository name is 1–100 ASCII alphanumeric/dot/underscore/
hyphen characters, excluding `.` and `..`. Keychain names are 1–255 printable
ASCII characters, starting with neither whitespace nor `-`. Permissions are a
nonempty object naming only `contents` and/or `pull_requests`, each `read` or
`write`; do not configure `metadata`. The opened file must be regular, owned by
the current user, not group/world writable, at most 65,536 bytes, and valid UTF-8
JSON with finite numbers. Mode 0600 is suitable. Invalid config exits 3 before
Keychain/HTTP; diagnostics never echo supplied paths, keys or values.

## Credentials, scope and cleanup

Only `process.run` launches the two exact executable paths above. Keychain uses
`find-generic-password -s SERVICE -a ACCOUNT -w`, captures both streams and has
a 120-second timeout. Raw/hex PEM and reflowed spaces/escaped newlines are
accepted for unencrypted `RSA PRIVATE KEY`/`PRIVATE KEY`. OpenSSL receives the
key only through an anonymous pipe at `/dev/fd/N`, never argv/env/file; stdin
holds JWT input. A feeder avoids large-key deadlock. Constructor/start/launch/
timeout failures close descriptors; started feeders are joined. SHA-256 RSA
signing times out after 30 seconds. RS256 JWT claims are string App ID `iss`,
`iat=int(now)-60`, `exp=int(now)+540`; both process streams are captured.

HTTP uses fixed `https://api.github.com`, verified TLS, refused redirects,
API version `2022-11-28`, 30-second deadlines, 8 MiB success/64 KiB error caps.
Empty 2xx means no content. Invalid JSON, duplicates and nonfinite values fail.
For complete bounded mint JSON, every top-level nonempty string token is first
registered for redaction and cleanup; duplicates/nonfinite fields then refuse
approval. Distinct duplicate token values each get a DELETE attempt. No regex
extracts credentials from truncated/unparseable JSON. HTTP errors report only
method/path/status and GitHub's string message. Token HTTP 422 directs Shawn to
add the repository to the installation or grant requested permissions.

Installation pages contain at most 100 entries; 20 full pages fail. Exactly one
account login must match, case-sensitive as in the reviewed source. IDs are
positive integers; returned App ID/slug, if present, must match. POST requests
one repository name and configured permissions. One repository-list GET must
show count 1 and exactly `owner/name`. Granted permissions must equal config
plus `metadata:read`; broader, narrower or malformed scope fails.

A token requires 16–4096 visible ASCII characters without spaces. Malformed
nonempty string tokens still get cleanup. Missing/empty/non-string tokens supply
no credential. Expiry is an opaque nonempty UTF-8 string, not a parsed timestamp;
missing, ambiguous or unencodable expiry is reported as `unknown`. DELETE uses
each observed token after success, refusal, listing failure or interruption.
Failed DELETE fails the check and retains status/expiry. Abrupt termination or
unread/unparseable replies cannot guarantee cleanup. No credential is returned.
Known key forms/body lines, JWT/signature and tokens are redacted before output escaping/truncation. Unencodable text uses backslash escapes; structured output
stops nesting at depth 8 with `[omitted]`. This preserves cleanup evidence even
for malformed response facts. Raw exceptions/tracebacks are never printed;
credential containers suppress repr.

## Results, exits and reuse

Stdout is one JSON object (`--text` renders the same facts). Success fields are
`ok:true`, `app_slug`, `installation_account`, `installation_id`,
`installation_repository_selection`, `permissions`, `expires_at`, `total_count`, `repositories`, `reasons:[]`, and `revocation:{status:"ok",expires_at:"..."}`.
Failure sets `ok:false`, nonempty `reasons`, and retains available facts.
Lifecycle results always include app slug/account and revocation, whose status
is `not_needed`, `ok` or `failed`, with string expiry (possibly `unknown`). Scope
refusals retain reported permissions/repositories. Revocation reasons come first.

| Exit | Stdout | Stderr |
|---|---|---|
| 0 | Success facts. | Empty. |
| 1 | Failure facts and cleanup evidence. | Empty. |
| 2, unexpected lifecycle failure | Failure facts and cleanup evidence. | `t3c publish check: command failed (<class>); check local inputs and helper installation` after cleanup. |
| 2, arguments/setup | Empty. | Sanitized CLI diagnostic. |
| 3, config | Empty. | `App config: <static reason>`. |

Unexpected check/cleanup failures take exit-2 precedence; both reasons survive.
`load_config`/`parse_config` return `Config` (`full_name`, `bot_login`) for later
Tasks. Enclose mint and consumption in `with TokenLease(api) as lease`, then
inspect `status`, `reason`, `expires_at`, `unexpected`. Mint registers cleanup
ownership during response decoding. Use `Secrets.public` before rendering.
`Runtime(http,clock)` and module-based process calls remain injectable. Guarded entry tests use temporary `--local-dir`, fake HTTP/Keychain and fresh throwaway
OpenSSL keys; see the [offline harness](../testing.md). Run focused discovery
with `-p test_github_app.py` or full `SUITE` using the shared isolated invocation.
