# Atlas Connectors

An engineering preview of the transport code Atlas uses to hand scoped work to
Claude Code, Codex and Grok, and collect their returns. MIT licensed.

**Status: source extraction preview; not yet a standalone live connector SDK.**
The implementation is currently distributed across Atlas. This
repository makes that boundary inspectable without sharing the full system.

## Start here

```sh
git clone https://github.com/MyAtlasAgent/atlas-connectors.git
cd atlas-connectors
python3 -B examples/offline_walkthrough.py
```

1. Read [the architecture and route map](docs/ARCHITECTURE.md).
2. Run `python3 -B examples/offline_walkthrough.py` (Python 3.11+, standard library).
3. Inspect the actual source under `reference/` and its provenance in
   [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json).
4. Read [the release gaps](docs/RELEASE_GAPS.md) before integrating it.

The walkthrough runs selected existing command-building and return-validation
functions against synthetic fixtures. It starts no provider, reads no provider
login, and uses a temporary directory for its output. It is a demonstration of
the extracted code, not an end-to-end Atlas execution or provider qualification.

## Routes

| Route | Included material | Current boundary |
| --- | --- | --- |
| Claude Code | Command builder, durable-session helpers, Courier endpoint, permission and telemetry code | Courier and the legacy dispatcher are distinct existing paths; not newly unified here |
| Codex | Shared command builder and illustrative read-only command configuration | Uses stdout/output-file return transport; no independent Codex SDK extracted |
| SuperGrok CLI | Existing launcher and return validator | Launcher depends on Atlas registration, permission, custody and return-ingestion services |
| Grok Bot desktop | Separate [manual handoff contract](docs/GROK_BOT_DESKTOP.md) and synthetic packet | An Atlas-owned automatic desktop connector was not found in the inspected sources |

Operators return through Atlas; this is not peer-to-peer bot messaging. Transport
delivery, operator receipt, returned findings, independent acceptance and a
verified external effect are different observations.

## Source and scope

Repository source is pinned to Atlas commit
`138fed3bd3bf0738f6c5eee8b35814facd0ef429`. The Grok return validator is a
separately identified installed-file snapshot with unverified source lineage.
This package does not claim to represent the current installed system or the
moving Milestone A integration root.

Reference files have `.txt` suffixes intentionally: they are source for reading,
not drop-in launch scripts. Local paths and email addresses are replaced with
placeholders; the manifest records source and packaged hashes. Configuration and
packets in `examples/` are newly authored synthetic examples, not account exports.

No provider binaries, credentials, browser sessions, operational databases,
transcripts, runtime logs, real account registry or Git history are included.
Each user supplies their own supported provider client and login if a future
release supports live execution. MIT covers the Atlas material in this package,
not third-party software, subscriptions or service access.

## Standalone library roadmap

This preview is ready for source inspection. A standalone live release still
needs the portability and contract work listed in `docs/RELEASE_GAPS.md`.
The offline walkthrough is the supported runnable example in this version.
