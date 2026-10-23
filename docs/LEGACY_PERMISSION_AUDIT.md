# Provider permission parity — 2026-10-23

Historical audit of the extraction preview and installed Atlas routes. Superseded
for standalone users by [the live broker guide](PERMISSIONS.md). This file is
retained as evidence; its implementation-status statements are historical.

## User preference

The user requires equal permissions across providers. For the same mission role,
scope and selected user preference, changing provider must preserve the effective
permission envelope. Provider names must not select a more privileged default.

An adapter must either enforce that envelope or report an unsupported capability
and refuse the affected action. It must not silently broaden access or quietly
restrict a build-capable provider to review-only while another provider can edit.
Mission grants and explicit reviewer restrictions still apply equally to all.

**Configuration standard implemented:** all routes now inherit one user-selected
policy through `atlas_connectors/permissions.py`. Provider-specific policy fields
and recognized native policy flags in route configuration are rejected. The
original installed routes and reading copies remain historical evidence;
equivalent live enforcement is still unqualified.

## Choose permissions once

Edit `examples/permissions.json`. The initial shared default is `ask`, with no
capability overrides. For example, a user can choose:

```json
{
  "version": 1,
  "default": "ask",
  "capabilities": {
    "read_files": "allow",
    "write_files": "allow",
    "network": "deny"
  }
}
```

Those choices apply equally to Claude, Codex, SuperGrok CLI and Grok Bot desktop.
Other supported capabilities are `run_commands`, `git_commit`, `git_push`,
`connected_tools`, `read_credentials`, `merge` and `install`. Each accepts only
`allow`, `ask` or `deny`; omitted capabilities inherit the shared default.
`allow` expresses user preference, not an Atlas grant or provider-enforcement
receipt. Actual execution still needs applicable scope and authority.

Load the configuration from the repository root:

```python
from atlas_connectors.permissions import load_configuration

config = load_configuration("examples/operators.example.json")
assert config.permissions_for("claude") is config.permissions_for("codex")
print(config.permissions_for("codex").preference("write_files"))
```

The same immutable policy and fingerprint apply to every configured route.
Changing the user file takes effect on the next configuration load; loaded
snapshots do not mutate running work. No provider is launched by this module.
The policy reference must resolve inside the configuration directory. Native
CLI flags are translated only by a future live adapter, rather than supplied
as per-provider overrides in this configuration.

## What was inspected

- Current local operator command configuration, selecting only command, prompt
  mode, timeout, forbidden-flag and environment-variable-name fields; no secrets.
- The pinned dispatcher/Courier/Grok reference code included in this repository.
- The installed Grok build wrapper's permission clauses, without executing it.
- Current official provider documentation linked below.

No provider was launched. User/project/managed provider settings, plugin/MCP
permissions, authenticated account state and runtime overrides were not read.
Consequently this is a static configuration/source check, not a proof of the
complete effective policy on any live session.

## Differences observed

| Surface | Files and execution | Publishing and authority | Unknowns |
| --- | --- | --- | --- |
| Claude dispatcher | Configured `acceptEdits`, additional ops directory, broad Python/Git/GitHub allow rules | Command allow rules can cover publishing commands; packet text withholds main/merge authority but is not an enforcement boundary | Full merged settings, sandbox, MCP, network and live effective policy |
| Claude Courier | Separate `BoundedPermission` implementation; explicit mode/tool/directory declaration and environment allowlist | Checks final argv and refuses its prohibited modes/flags | Not interchangeable with the legacy dispatcher; current CLI qualification |
| Codex dispatcher | Configured `-s read-only`, with final output written by the CLI | Candidate edits/commits are incompatible with the selected read-only shell sandbox; no separate model grant implied | Approval/network/MCP configuration and live effective policy |
| Grok review CLI | Edit/Write deny rules for the checkout; Python and test commands allowed; post-run checkout checks | Push and multiple mutations denied by command rules | No explicit sandbox profile in the inspected invocation; ambient sandbox settings not inspected |
| Grok build CLI | Separate allow rules for worktree edits, tests and commits | Model push and GitHub CLI denied; separate owner-operated publication step | No explicit sandbox profile in the inspected invocation; wrapper itself disclaims structural egress prevention |
| Grok Bot desktop | Manual-delivery route; no owned automatic adapter extracted | A packet cannot establish app-enforced permission equality | All app permission, sandbox, network and provider identity enforcement |

The earlier package hard-coded Codex read-only in its example. That flag is now
removed: all example routes reference the same user policy. These argv templates
are **offline transport examples, not launch-ready commands**. Removing a native
flag does not enforce the shared policy; forwarding these templates to a real
CLI without a qualified translator could inherit that client's defaults. The
historical Grok wrapper also retains its old rules. No live launcher is shipped.

## Documentation corrections and enforcement limits

Current Claude documentation says `acceptEdits` auto-accepts edits/common file
operations within allowed working directories. It describes `dontAsk` as denying
calls that would otherwise prompt. The historical Courier reading copy contains
a comment describing `dontAsk` as answering everything yes; that explanation is
not current provider behavior. Courier's conservative refusal remains its own
historical policy and is not changed by this extraction. [Claude permissions](https://code.claude.com/docs/en/permissions)

Codex documents `--sandbox/-s` as the policy for generated shell commands. The
read-only setting observed in the configured route therefore differs from an
editing route; it does not by itself specify every connected tool's permission.
[Codex command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

Grok's documentation likewise defines headless `dontAsk` as denying unmatched
calls. Tool allow/deny rules and OS sandboxing are distinct controls. In
particular, the documented read-only/strict child-network restrictions are Linux
only, and built-in profiles do not permanently protect credential paths. Matching
mode names across vendors or platforms cannot establish equal access.
[Grok headless permissions](https://docs.x.ai/build/enterprise),
[Grok sandbox](https://docs.x.ai/build/features/sandbox)

Static inference: allowing a Python interpreter under shell rules can enable
filesystem or network operations inside that process unless a stronger boundary
prevents them. A post-run dirty-tree check detects some changes; it cannot undo a
write, secret read or network effect. No such bypass was executed in this check.

## Required common preference contract

The shared user preference now selects capability-level allow/ask/deny choices.
Its future enforcement contract must additionally state filesystem read/write
roots, command/test scope, Git commit/push scope, network destinations, connected
tools, credential exposure and expiry/revocation. Each adapter needs
to translate that same policy into its native enforcement and record the actual
enforced result. Missing enforcement must stay explicit.

Qualification must use the same positive and negative cases per provider:
permitted worktree edit, refused outside-root write, read-only review edit
refusal, allowed test command, denied external destination and credential read,
feature-branch publication only when granted, and refusal of merge/install or
expired authority. Include shell/interpreter paths and connected tools, not just
the vendors' direct Edit/Write tools. Grok Bot desktop stays manual/unqualified
until its enforcement can be demonstrated.

The existing live routes were inspected only. Shared configuration parity is now
tested in this repository; native enforcement and live rollout remain separate.
The source preview must not claim equal live permissions until those controls
and conformance observations exist.
