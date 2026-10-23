# Release gaps

This is an extraction candidate for an engineer to inspect. These gaps prevent
presenting it as a portable live connector library:

- Extract the actual transport implementations into importable modules without
  the full dispatcher's configuration reads, scheduler, authority or top-level
  execution. Keep the reference snapshots as evidence of the donor code.
- Replace host-specific paths through a documented configuration boundary.
  Redacted paths in the reading copies are placeholders, not working defaults.
- Establish the source owner and accepted lineage of `grok-return-validate`.
  Its current SHA check can write a BLOCKED return while exiting zero. Its PR
  mismatch path adds a comment rather than enforcing exact PR identity. Its
  `--worktree` check is optional. These limits must be addressed or constrained
  explicitly before using it for authoritative admission.
- Decide and test one public return contract, identity/session semantics,
  cancellation, timeout/unknown outcomes and duplicate suppression across routes.
  Current per-route scripts do not yet implement the proposed unified envelope.
- Revalidate supported provider CLI versions and permission behavior with each
  user's authorized account. No provider command or subscription probe was run
  while preparing this package. Never silently switch to billable API access.
- Codex needs an independently portable endpoint beyond generic command assembly.
- Grok Bot desktop has a documented manual path, not an extracted automatic
  connector. Keep it experimental/manual unless real owned source is supplied.
- Preserve one owner for custody/lifecycle. A portable adapter must not inherit
  Atlas merge, install, reviewer or grant authority from a caller label.
- Keep source provenance and content checks current as files are added. The
  Principal selected MIT and authorized the separate public repository;
  the license file is included.

The existing DOM-451/580 implementation pause is not lifted by preparing this
read-only extraction. Any runtime implementation or cutover remains separate.
