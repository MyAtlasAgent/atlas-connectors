# Version 0.2 boundaries

Implemented: standalone installation, official login command, fresh CLI sessions,
one user policy, strict structured requests and actual bounded file enforcement.

Remaining:

- Executors for arbitrary commands, network workload tools, Git and installation
  effects. Until enforceable, these requests explicitly refuse.
- Automatic Grok Bot desktop integration; retain its separate manual route.
- Windows support; directory-descriptor enforcement is POSIX only.
- Qualification of additional client versions and OS combinations.
- First-time new-account onboarding on a clean workstation. Existing logins with
  fresh conversations are a distinct result, recorded in VALIDATION.md.
- Independent adversarial assessment beyond this release's bounded checks.
  Vendor clients and host administrators are trusted; this is not OS isolation.
- Durable cross-process custody/idempotency and long-running resumable agents.

Historical Atlas source and installed Atlas runtime permissions remain separate.
They are not invoked, installed or migrated by this package.
