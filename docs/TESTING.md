# Standalone testing

## Offline

`python -m unittest discover -s tests -v` needs no login and makes no provider
calls. It uses real temporary files/processes to verify policy parity, actual
allow/deny/ask effects, path/symlink refusals, safe hardlink replacement, replay,
invalid messages and process timeout cleanup.

## Clean installation

Create a new Python 3.11/3.12 virtual environment and `python -m pip install .`.
Change to an unrelated empty directory and run the installed `atlas-connectors
init` there. Do not set PYTHONPATH. Confirm `python -I -c "import atlas_connectors;
print(atlas_connectors.__file__)"` resolves in that virtual environment. No Atlas
source, ops directory or daemon is required. CI repeats this isolation check.

## Fresh conversations using an existing login

`atlas-connectors smoke PROVIDER --config PATH` uses three fresh conversations
and a disposable workspace. For the same concrete write request, allow must
create the file, deny must not, and unattended ask must not. Provider names are
claude, codex and supergrok_cli. This uses subscription quota. No automatic
retry, API fallback or alternate account is allowed after a failure.

Record exact client version, real effects and failures; never publish tokens,
auth output, private prompts or raw client transcripts. A successful greeting
alone is insufficient, and a fresh conversation is not a new-account test.

## First-time onboarding

1. Install this package and supported official clients on a clean workstation.
2. Run `atlas-connectors init new-account --isolated-logins`.
3. Run `atlas-connectors login PROVIDER --config new-account/connectors.json`.
4. Complete the official interactive sign-in yourself.
5. Run doctor and smoke with the same config.
6. Exercise a real file task and interactive ask approval.

Login profiles contain authentication material; keep them outside shared source
and never upload them. The user is arranging this separate new-account test.
