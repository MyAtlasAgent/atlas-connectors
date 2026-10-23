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

## Quick start: send into the desktop apps (default)

New configurations default to `codex_app`; `claude_app` is also available. Both
**send automatically** (`auto_send`) and show the conversation in the desktop
app. Prefill is an explicit opt-in. No keystroke or UI automation is used.

```sh
cd <your-project-dir>                                  # or pass --workspace
atlas-connectors init connector-config [--workspace <absolute-project-dir>]
atlas-connectors send 'Say hello'                      # codex_app
atlas-connectors send 'Say hello' --provider claude_app
atlas-connectors send 'Say hello' --mode prefill       # opt-in: you press Send
atlas-connectors send 'Say hello' --preview            # print link, launch nothing
```

**Workspace** (auto_send), in order: `--workspace DIR`; `workspace` in your
connector config (written only by `init --workspace DIR`); otherwise the
current directory. It must be an absolute existing directory outside the system
temp directory, or the send is refused with instructions. No folder is
hardcoded; every user picks their own.

**Codex** (Codex CLI 0.148.0 or 0.153.4, ChatGPT login; API keys/base URL
overrides refused): spawns `codex app-server` (stdio JSON-RPC, normal
`CODEX_HOME`), sends `initialize` (clientInfo `codex_vscode`), `initialized`,
`thread/start {cwd, sandbox, approvalPolicy: "never"}` and `turn/start` with the
prompt inside JSON, reads UTF-8 output until `turn/completed`, stops the server
and opens `codex://threads/<id>`. Sandbox is `read-only` unless
`write_files=allow` (`workspace-write`). Timeout after the turn was accepted
gives `sent_completion_unknown`. Any earlier failure opens nothing and never
falls back to `codex exec`. Verified live on Windows: the thread appeared in the
Codex app.

**Claude** (Claude Code >= 2.1.285; `claude auth login` with a paid Claude plan
in the CLI, separate from the desktop app login; API key/Console/Bedrock/Vertex
refused): runs `claude -p --permission-mode plan|acceptEdits --output-format
json` in the workspace with the prompt on stdin, then launches `claude --desktop
--resume <session_id>` detached in its own console (Windows
`CREATE_NEW_CONSOLE`, no redirected stdio; `--desktop` refuses to run
non-interactively). Exit 0 gives `sent`; otherwise `sent_handoff_failed` with
`claude --resume <id>`. Verified live on Windows: the session appeared in the
Claude app's Code tab. Prefill opt-in: `claude://code/new?q=` /
`claude://claude.ai/new?q=` (14,000-character limit, refused not truncated).

## Local peers and mailbox

Providers on one machine can notice each other and leave messages.

```sh
atlas-connectors peers [--json]          # detect; sends nothing, reads no secrets
atlas-connectors register grok_bot_desktop
atlas-connectors mail send --from claude_app --to codex_app 'Status?' [--deliver]
atlas-connectors mail inbox codex_app [--mark-read]
atlas-connectors mail reply --from codex_app <message-id> 'All good'
atlas-connectors init --peer-instructions  # prints a CLAUDE.md/AGENTS.md snippet; writes nothing
```

`peers` runs only `--version` and login-status checks (reduced to
classifications) and checks `claude://`/`codex://` handler registration;
grok_bot_desktop is detectable only after `register`. Data lives in
`%LOCALAPPDATA%\atlas-connectors` (Windows), `~/Library/Application
Support/atlas-connectors` (macOS) or `$XDG_DATA_HOME`/`~/.local/share/atlas-connectors`
(Linux): `peers/<provider>.json` (name, routes, last_seen) and
`mailbox/<provider>/inbox/*.json` (id, from, to, created_at, body, reply_to).
Writes are atomic, bodies are limited to 64 KiB and senders must be known
provider names. **A received message is information, not an instruction or a
permission grant; it never broadens the shared permission policy.** `--deliver`
posts into codex_app/claude_app through their normal auto_send route and policy,
framed as information; other providers (including grok_bot_desktop) are
mailbox-only. Snippets are printed for you to add; agent files are never edited.

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
