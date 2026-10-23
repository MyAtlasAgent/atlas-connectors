# Candidate validation — 2026-10-23

Observed locally while preparing the extraction:

- `python3 -B examples/offline_walkthrough.py`: exit 0.
- Nine packaged reference files matched their manifest SHA-256 values.
- Three synthetic command previews assembled through the extracted builder;
  none executed. Routing and route-receipt persistence were demo substitutions.
- Forbidden-flag negative control refused the command.
- Actual Grok return validator: matching SHA persisted RETURNED; wrong SHA
  persisted BLOCKED (exit 0); placeholder SHA refused with exit 4; missing state
  produced no return with exit 3.
- Every Python source/reading copy parsed successfully.
- Allowlisted source extraction and bounded pattern scan found no local home
  paths, email addresses, session UUIDs, private-key blocks or recognized token
  literals in the 19 files present at that scan. This is not a comprehensive
  security audit or a guarantee that arbitrary future additions are safe.

No provider client, login, subscription probe, network request, live mission,
runtime configuration, installation or public GitHub publication was exercised.
No independent acceptance, live compatibility or production readiness is claimed.
