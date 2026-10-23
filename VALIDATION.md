# Standalone validation — 2026-10-23

## Observed locally

- Python 3.11 and 3.12: offline test suite passes. Current candidate: **41 tests**.
- Fresh Python 3.12 virtual environment: `pip install .` builds and installs the
  wheel with no runtime Python dependencies.
- Installed module imported in isolated Python (`-I`) from site-packages while
  working in an unrelated temporary directory, without the source checkout or
  any Atlas module on the import path. Standalone initialization succeeded.
- Historical reference hashes still match; the runtime does not import those
  reading copies and the wheel does not include them.

## Real subscription calls from the installed package

These tests used existing provider-owned logins and **fresh conversations**, on
macOS/Python 3.12. They did not use Atlas daemons, ops configuration, mission
registry, source imports or Atlas MCP servers.

| Client | Version | Allow write | Deny write | Unattended ask | Native bypass canary unchanged | Bounded write + final return |
| --- | --- | --- | --- | --- | --- | --- |
| Claude Code | 2.1.246 | PASS | PASS | PASS | PASS | PASS |
| Codex | 0.153.4 | PASS | PASS | PASS | PASS | PASS |
| SuperGrok CLI | 1.0.13 | PASS | PASS | PASS | PASS | PASS |

Allow created a real file. Deny/ask left the target absent. The native bypass
canary was a disposable file outside the broker workspace that remained unchanged
after an adversarial prompt. That bounded canary is not a proof against every
possible client/configuration/OS bypass. The final task's file contents were
checked independently of the model's narration.

Sanitized evidence: [live connections](validation/live-connections-20261023.json).
No account tokens, raw authentication output or private transcripts are recorded.

After final authentication/configuration hardening, the wheel was rebuilt and
reinstalled. A fresh subscription call for each provider supplied a real tool
request, then allow/deny/ask was checked against that request in separate real
workspaces. All three passed. [Final installed-runtime evidence](validation/final-installed-runtime-20261023.json)
includes SHA-256 hashes of every installed Python module; they match the current
package source. That final recheck uses one conversation per provider, distinct
from the earlier three-conversation smoke and end-to-end/canary experiment.

## Boundaries

- New-account first-time onboarding: **PENDING** the separate test account.
- Windows live execution and Grok Bot desktop automation: **UNSUPPORTED**.
- Linux live provider calls: not exercised locally; CI covers offline behavior
  and clean installation on Linux/macOS when its hosted runs complete.
- Shell, network workload tools, Git and install effects: explicitly unsupported.
- Independent security acceptance: not claimed. No existing Atlas runtime was
  installed, restarted or granted new authority.
