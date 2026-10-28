# GitHub App scope check

`publish check` reads the configured App key, mints a token for one repository,
checks its actual scope, and revokes it. It does not publish or run a child
command with that token. GA-2 and GA-3 own command execution and Git preconditions.

## Invocation and prerequisites

Use the stable CPython 3.15 interpreter selected by uv's global pin:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <root>/bin/t3c publish check
"$PY" -I -S <root>/bin/t3c --local-dir <state-directory> publish check
"$PY" -I -S <root>/bin/t3c --text --local-dir <state-directory> publish check
```

Replace placeholders with paths; quote paths containing spaces. Global options
precede `publish`. There are no command-specific options. The
[shared CLI contract](../cli.md) defines isolation, help and output rendering.
The default config is `<root>/.local/github-app.json`; an override selects
`<state-directory>/github-app.json`. No config is read during import/register.

Machine prerequisites are HS-1, macOS `/usr/bin/security`, `/usr/bin/openssl`
(tested with LibreSSL 3.3.6), the App installation, HTTPS access to GitHub and
the configured Keychain item. No Python dependency is added. If macOS asks
Shawn for Keychain access, choose **Allow**, not **Always Allow**.
After merge and review, the Engineering manager runs the first command above
once for L4 and compares it with the interim check. Workers run no live check;
offline results do not establish L4 or real installation readiness.

## Configuration

This is a synthetic example, not an installed credential configuration:

```json
{
  "version": 1,
  "app": {
    "slug": "fixture-worker",
    "appId": 12345,
    "installationAccount": "fixture-org",
    "keychain": {"service": "fixture-service", "account": "fixture-account"}
  },
  "repository": {
    "owner": "fixture-org",
    "name": "fixture-repo",
    "permissions": {"contents": "write", "pull_requests": "write"}
  }
}
```

All shown fields are required; unknown fields and duplicate JSON keys are
refused. `version` is integer 1 and `appId` a positive integer, never a boolean.
Owner and installation account are ASCII GitHub login forms: 1–39 alphanumeric
or hyphen characters, starting and ending alphanumeric. They must agree ignoring
case. Slug is 1–100 lowercase ASCII alphanumeric or hyphen characters, with
alphanumeric ends. Repository name is 1–100 ASCII alphanumeric, dot, underscore
or hyphen characters, excluding `.` and `..`. Keychain service/account are
1–255 printable ASCII characters; neither starts with whitespace or `-`.
Permissions must be a nonempty object containing only `contents` and/or
`pull_requests`, each with `read` or `write`. Do not configure `metadata`.

The loader checks the opened file: regular file, owned by the current user,
not group/world writable, at most 65,536 bytes, valid UTF-8 JSON with finite
numbers. Mode 0600 is suitable. Invalid config exits 3 before Keychain or HTTP
access; diagnostics never echo paths, unknown keys or supplied values.

## Credential and HTTP lifecycle

Only `process.run` launches programs, using the two exact executable paths
above. Keychain lookup uses `find-generic-password -s SERVICE -a ACCOUNT -w`,
captures both streams and has a 120-second timeout. Its output may be raw or
hex-encoded PEM, including reflowed spaces or escaped newlines. The key must be
an unencrypted `RSA PRIVATE KEY` or `PRIVATE KEY` PEM that OpenSSL can sign with.

The key reaches OpenSSL solely through an anonymous pipe referenced by
`/dev/fd/N`, never a temporary file, argv value or environment variable. A feeder
thread avoids deadlock with large keys; pipe descriptors close on constructor,
start, process launch and timeout failures, and a started feeder is joined.
OpenSSL uses SHA-256 RSA signing, with a 30-second timeout. Stdin holds JWT
signing input; stdout and stderr are captured. The JWT uses RS256, App ID as
string `iss`, `iat = int(now) - 60`, and `exp = int(now) + 540`.

HTTP uses only `https://api.github.com`, verified TLS, refused redirects,
API version `2022-11-28`, and a 30-second timeout per request. Success bodies
are bounded at 8 MiB; HTTP error bodies at 64 KiB. Empty success bodies mean
no content. Invalid JSON, duplicate keys and nonfinite success values fail.
An HTTP failure reports method/path, status and GitHub's string `message`,
never headers or raw network exceptions. Token-request HTTP 422 also directs
Shawn to add the repository to the installation or grant requested permissions,
even when GitHub supplies no message.

Installation discovery requests 100 entries per page, at most 20 pages, and
requires a short final page and exactly one matching account login. Account
selection and repository full-name comparison preserve the source's exact case
matching. The installation ID must be positive; returned App ID/slug, if present,
must match config. The token POST requests exactly `[repository.name]` and the
configured permissions. One `GET /installation/repositories` must establish
`total_count == 1` and exactly `[owner/name]`. Granted permissions must equal
config plus `metadata: read`; broader, narrower or malformed scope fails.

A nonempty string token is recorded before other token fields are validated.
Even a malformed token string or missing `expires_at` triggers a revocation
attempt with `DELETE /installation/token`, authenticated with that token.
A missing, empty or non-string token supplies no credential for revocation.
Token validation requires 16–4096 visible ASCII characters without spaces;
expiry is a nonempty string reported as received, not parsed as a timestamp.
Revocation runs after success, scope refusal, listing failure, KeyboardInterrupt
or an unexpected exception. If DELETE fails, the check fails and reports
`expires_at` (or `unknown`); do not treat the credential as revoked. Abrupt
process termination cannot guarantee cleanup. No token is returned to the caller.

Known key forms, PEM body/lines, JWT/signature and token are redacted from
diagnostics and recursively from structured strings and object keys before
truncation or output escaping. Raw exceptions, arguments and tracebacks are
never printed. Credential containers suppress their dataclass representations.

## Results and exit codes

Normal stdout is one JSON object; global `--text` renders the same facts.
This synthetic success exits 0 with empty stderr:

```json
{
  "ok": true,
  "app_slug": "fixture-worker",
  "installation_account": "fixture-org",
  "installation_id": 67890,
  "installation_repository_selection": "selected",
  "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"},
  "expires_at": "2030-01-02T03:04:05Z",
  "total_count": 1,
  "repositories": ["fixture-org/fixture-repo"],
  "reasons": [],
  "revocation": {"status": "ok", "expires_at": "2030-01-02T03:04:05Z"}
}
```

| Exit | Stdout | Stderr |
|---|---|---|
| 0 | Success object above. | Empty. |
| 1 | `ok: false`, available facts, nonempty `reasons`, revocation evidence. | Empty. |
| 2, unexpected failure inside the token lifecycle | Same failure object, retaining revocation evidence. | Class-only diagnostic for each unexpected failure, after cleanup. |
| 2, argument or setup failure | Empty. | Sanitized CLI diagnostic. |
| 3, invalid config | Empty. | `App config: <static reason>`. |

Failures retain facts only once obtained. `app_slug`, `installation_account`,
`ok`, `reasons` and `revocation` are always present in lifecycle results.
Revocation status is `not_needed`, `ok` or `failed`; its expiry is always a string.
For example, missing expiry followed by a failed DELETE exits 1 with:

```json
{"app_slug":"fixture-worker","installation_account":"fixture-org","ok":false,"reasons":["could not revoke token; expires_at=unknown: GitHub API DELETE /installation/token failed: HTTP 500: temporary failure","GitHub token response has no expires_at"],"revocation":{"status":"failed","expires_at":"unknown"}}
```

A scope refusal retains the reported permissions and repository list, sets
`ok: false`, adds the scope reason(s), and still revokes. An unexpected failure
in either the check or revocation takes exit 2 precedence over operational
failures. Both reasons survive when both fail; the revocation reason comes first.
For a `RuntimeError`, stderr is exactly this line, never the exception's message:

```text
t3c publish check: command failed (RuntimeError); check local inputs and helper installation
```

## Reuse and offline validation

`load_config(path)` / `parse_config(data)` return `Config`, including `full_name`
and `bot_login` (`slug + "[bot]"`), for GA-2/PB-1. Invalid config raises
`ConfigError`. Enclose both `mint_token(config, runtime, secrets, lease)` and
its consumer in `with TokenLease(api) as lease`; then inspect `status`, `reason`,
`expires_at` and `unexpected`. The lease owns the token and cleanup. Consumers
must redact facts through `Secrets.public` before output. `Runtime(http, clock)`
injects transport/time; process calls remain injectable through the module.

From the repository root, with the [offline guard](../testing.md) active:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S -B -m unittest discover -s tests -t . -p test_github_app.py -v
"$PY" -I -S -B -m unittest discover -s tests -t . -v
```

Tests use temporary synthetic `--local-dir`, in-process `runpy` entry execution,
injected HTTP, fake Keychain output, and explicitly allowed OpenSSL for fresh
synthetic key generation, signing, verification and timeout cleanup.
