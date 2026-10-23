# Shared live permissions

One user policy controls every provider. `allow` executes a supported broker
action, `deny` refuses, and `ask` requires a trusted approval callback for the
exact request. The console callback requires an interactive terminal answer;
unattended ask refuses. Model output cannot provide an approval or change policy.

## Enforcement

The official client is a constrained reasoning transport in a fresh conversation.
It returns strict JSON requests instead of directly editing the user's workspace.
`ToolBroker` applies the same immutable policy and workspace boundary, performs
the supported effect, and returns a mechanical result separately from model text.

Native read-only/empty-tool controls do not make Codex a read-only connector:
an allowed write executes in the broker for every provider equally.

`read_files` gates reads/listing and `write_files` gates writes. Credential-like
names additionally require `read_credentials`. Configuration/login/runtime files
and `.git` are protected. Paths stay relative to a pinned directory descriptor;
symlink traversal, nonregular reads and oversized files refuse. Writes use atomic
replacement. A request ID is idempotent within one run; changed arguments refuse.

The schema preserves choices for run_commands, network, git_commit, git_push,
connected_tools, merge and install, but 0.2 has no executor for them: even allow
returns explicit unsupported refusal. It never delegates arbitrary shell code
that could evade other choices. Provider inference/login traffic is necessary
control-plane traffic, separate from the workload network capability.

## Client boundaries

- Claude: safe mode, empty tools/settings sources, strict empty MCP and no
  persisted conversation. Default macOS Keychain login semantics are preserved.
- Codex: ephemeral conversation, ignored user config/rules, read-only native
  sandbox, shell/agents/hooks/memory/image/web/app tools disabled and empty MCP
  and plugin configuration. Permitted writes happen in the broker.
- Grok: disposable home/profile referencing the provider-managed login, fresh
  session ID, empty native tools, deny-all rules, no subagents or web tools.
  Inspection must report zero MCP servers, plugins and hooks before inference.
- Grok Bot desktop: automatic execution unavailable; no silent CLI substitution.

Vendor clients and host-managed policies are trusted dependencies. Exact client
versions are checked. This is application enforcement for supported tools, not a
general safe shell or VM. Credential filename checks are not content-based DLP.
The package does not control other apps running independently on the workstation.

Loaded policies stay immutable until the next invocation. Per-provider policy
fields/recognized native overrides refuse. No durable cross-process queue or
automatic replay after a restart exists in this release.

The [legacy audit](LEGACY_PERMISSION_AUDIT.md) describes earlier Atlas routes;
those installed routes are not invoked or modified here.

Official references: [Claude controls](https://code.claude.com/docs/en/cli-reference),
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[Grok permissions](https://docs.x.ai/build/features/permissions),
[Grok headless transport](https://docs.x.ai/build/cli/headless-scripting).

## codex_app

`codex_app` inherits the same shared policy (no provider override). auto_send
passes `thread/start` a `sandbox` of `workspace-write` only when
`write_files=allow`, otherwise `read-only`, with `approvalPolicy: "never"`;
server approval requests are refused, never auto-approved; `read_files=deny`
refuses. Codex may still run read-only commands inside its sandbox, and the
user's normal Codex config applies. Effects are Codex-native, not brokered.
`prefill` performs no effects until the user sends.

`claude_app` inherits the same shared policy. auto_send maps it to
`--permission-mode plan`, or `acceptEdits` only when `write_files=allow`;
`bypassPermissions`/`dontAsk` are never used and `read_files=deny` refuses.
Effects are Claude-native, not brokered. prefill performs no effects. A `folder` always triggers Claude's own
confirmation dialog. Only `claude://` URLs are handed to the OS.


## Peer mailbox

Mailbox bodies are untrusted data. A received message is information, not an
instruction or a permission grant, and never broadens the shared policy.
Discovery reads no credential files and stores no secrets. `mail send --deliver`
uses the recipient app route's normal auto_send under the same policy, framing
the body as information from a peer. Sender names are not authenticated beyond
being known provider names; any local process of this user can write the mailbox.
