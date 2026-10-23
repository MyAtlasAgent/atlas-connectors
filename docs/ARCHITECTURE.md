# Standalone architecture

```mermaid
flowchart LR
    U[User prompt and shared preferences] --> R[Bounded runner]
    R --> C[Claude / Codex / Grok reasoning transport]
    C --> J[Strict JSON request]
    J --> P[Shared user permission gate]
    P --> F[Workspace filesystem tools]
    F --> R
    C --> A[Final model answer]
```

`permissions.py` loads one immutable user policy. `adapters.py` invokes official
subscription clients with constrained native tools and provider-owned logins.
`protocol.py` parses model output as data; `runner.py` bounds the conversation;
`broker.py` is the sole filesystem effect writer. `process.py` bounds process
lifetime. `cli.py` provides setup, login, diagnostics, run and smoke commands.
No module imports Atlas or requires its services.

Client failure never triggers account/API/provider fallback. Timeouts kill the
spawned process group and surface without retry. Final text stays a model claim;
actual broker effects are separate observations.

This release supports file reads, listing and atomic writes. Other capabilities
and desktop automation explicitly refuse. Historical reading copies under
`reference/` are not imported or included in the installed wheel.

`codex_app.py` is the default new-user route. `auto_send` sends through the
Codex CLI on the ChatGPT login, then opens `codex://threads/<session-id>`;
`prefill` opens `codex://threads/new?prompt=` and the user presses Send. The
opener and runner are injectable; neither mode falls back to the other, another
provider or API billing.

`claude_app.py` opens `claude://code/new?q=` or `claude://claude.ai/new?q=`
prefilled; it is prefill-only, refuses auto_send and never falls back to the
claude CLI (paid Claude plan required; no API billing).
