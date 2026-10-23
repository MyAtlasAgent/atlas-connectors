# Atlas Connectors

Standalone **Claude Code, Codex and SuperGrok CLI** connections, using your own
subscription logins and one shared user permission policy. **No Atlas installation
is required.** MIT licensed. Version 0.2 is an early filesystem-tool release.

## Requirements

Python 3.11/3.12 on macOS or Linux, plus each official provider client you want
to use, installed separately on PATH and authenticated with your own supported
subscription. There is no API-key fallback and no runtime Python dependency.

| Connection | Executable | Client version currently accepted |
| --- | --- | --- |
| Claude Code | `claude` | `2.1.246` |
| Codex | `codex` | `0.153.4` |
| SuperGrok CLI | `grok` | `1.0.13` |

Other client versions are refused pending qualification because native controls
change. `doctor` checks version; `smoke` tests actual connectivity/permissions.
See [validation evidence](VALIDATION.md) for observed results and limitations.

Official installers: [Claude Code](https://code.claude.com/docs/en/setup),
[Codex CLI](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
[Grok Build](https://docs.x.ai/build).

## Quick start: Codex desktop app (default route)

New configurations default to `codex_app`, which uses the Codex desktop app on
your ChatGPT subscription (Claude needs a paid Claude plan; API billing is refused).

```sh
atlas-connectors init connector-config
atlas-connectors send 'Say hello'                 # auto_send (default)
atlas-connectors send 'Say hello' --mode prefill  # you press Send in the app
atlas-connectors send 'Say hello' --preview       # print link, launch nothing
```

`auto_send` runs `codex exec --skip-git-repo-check -s <sandbox> -o <tmp>/return.md`
on your ChatGPT login (sandbox derived from the shared policy; `ask` maps to
read-only, never broader), reads the session id, then opens
`codex://threads/<session-id>`. No keystrokes or UI automation are used. Whether a
CLI-sent session appears in the app's thread list is verified only by that deep
link opening it. If no session id is found the result is `sent_session_unknown`
and nothing is opened. CLI failure opens nothing and never falls back.
`prefill` opens `codex://threads/new?prompt=...` (optional `&path=`); the app never
auto-submits, so the result is `prefilled_awaiting_user_send`. Set
`"mode": "prefill"` on the `codex_app` route to make that the default.

### Claude desktop app (prefill only)

```sh
atlas-connectors send 'Say hello' --provider claude_app                 # claude://code/new?q=
atlas-connectors send 'Say hello' --provider claude_app --surface chat  # claude://claude.ai/new?q=
```

Claude Code and the Claude app require a paid Claude plan; there is no API
billing fallback. Claude Desktop (1.1.2321+) links never auto-submit, so
`claude_app` is prefill-only (`prefilled_awaiting_user_send`); `--mode auto_send`
is refused rather than falling back to the claude CLI. Prompts over 14,000
characters are refused instead of silently truncated. `--workspace` sets the
code surface's absolute `folder`, which Claude confirms with an in-app dialog.

## Install and connect

```sh
git clone https://github.com/MyAtlasAgent/atlas-connectors.git
cd atlas-connectors
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install .
atlas-connectors init connector-config
```

Once installed, commands work outside this checkout. No Atlas source, database,
dispatcher, account registry or background service is needed.

Sign in through the official client, skipping providers you do not use:

```sh
atlas-connectors login claude
atlas-connectors login codex
atlas-connectors login supergrok_cli
atlas-connectors doctor codex
atlas-connectors smoke codex
```

Login is interactive; only the vendor client handles credentials. Existing
logins can be reused. To test a new account in separate profiles, initialize
with `atlas-connectors init test-config --isolated-logins` and pass
`--config test-config/connectors.json` to subsequent commands. Never commit
these login directories. Initialization refuses to overwrite existing files.

## Choose permissions once

Edit `connector-config/permissions.json`. Initially every action asks. For
automatic file reads/writes in an entrusted workspace, for example:

```json
{
  "version": 1,
  "default": "deny",
  "capabilities": {
    "read_files": "allow",
    "write_files": "allow"
  }
}
```

The same policy applies to every provider. `allow` executes a supported action,
`deny` refuses it, and `ask` requires a per-request terminal approval. In
unattended/non-TTY runs, ask refuses. Model output cannot provide approval.
Provider-specific permission overrides are rejected.

## Run a task

```sh
mkdir -p demo-workspace
atlas-connectors run codex 'Write hello.txt containing Hello' --workspace demo-workspace
atlas-connectors run claude 'Read hello.txt and explain it' --workspace demo-workspace
atlas-connectors run supergrok_cli 'List the files' --workspace demo-workspace
```

Use `--config PATH` for another shared configuration and `--max-turns N` to
bound a run (default 8, maximum 32). Optional per-provider settings are `model`,
`executable`, `auth_directory` and `timeout`; permissions stay shared. Client
failure/timeout/quota never triggers automatic retry or provider/API fallback.

The clients run as constrained reasoning transports in fresh conversations.
They return structured requests; this package's broker checks the user policy
before performing supported effects.

| Action in 0.2 | Enforcement |
| --- | --- |
| Read/list files | `read_files`, inside workspace |
| Write files | `write_files`, bounded atomic replacement inside workspace |
| Credential-like workspace paths | Additional `read_credentials` choice |
| Shell, network fetch, Git, merge/install, external MCP | Explicitly unsupported even when allowed; no unrestricted fallback |
| Grok Bot desktop | Separate manual route; automatic execution refused |

Parent directories must exist. Files are limited to 64 KiB. Git metadata,
connector configuration, login directories and runtime files are protected.
Directory-descriptor checks refuse symlink traversal; replacement avoids
truncating external hardlink targets. Credential filename checks are conservative,
not content-based DLP. See [permission boundaries](docs/PERMISSIONS.md).

Output separates mechanical tool results from the final model claim. `returned`
means an answer arrived, not every action succeeded: inspect `tools` statuses.
The network permission controls workload actions; provider login/inference
necessarily uses network access when you invoke a live connection.

## Test without Atlas

```sh
python -m unittest discover -s tests -v
atlas-connectors smoke claude
atlas-connectors smoke codex
atlas-connectors smoke supergrok_cli
```

Offline tests need no login. Each smoke command consumes subscription quota in
three fresh conversations and checks actual allow/deny/unattended-ask file
effects in a disposable workspace. A greeting alone is not a permission test.
See [clean installation and new-account tests](docs/TESTING.md).

## Trust and provenance

Vendor binaries, authentication services and host administrator policy are
trusted dependencies. This is not an OS-wide sandbox for malicious binaries or
other apps. It cannot govern independent actions in a desktop app.

Historical Atlas reading copies under `reference/` are non-executable and are
not imported or included in the Python wheel. Their hashes remain in
`SOURCE_MANIFEST.json`. See [architecture](docs/ARCHITECTURE.md),
[desktop distinction](docs/GROK_BOT_DESKTOP.md) and
[remaining limitations](docs/RELEASE_GAPS.md).
