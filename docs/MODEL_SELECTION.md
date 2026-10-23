# Model-agnostic provider connections

Atlas Connectors requires no model name, model catalog, minimum plan tier or
API credentials to configure a provider connection. Availability changes by
account; the provider knows what its signed-in user can use.

1. Use the user's authenticated provider-native session. The user may choose
   their strongest available model in the provider client UI, or let the client
   use its default. The connector does not promise the default is the best.
2. Do not insert native model flags while building provider commands. If an
   authenticated client has no model available, report it as unavailable.
   Never switch to billable API access or another provider without approval.
3. The configured strategy is provider_session; requested_model is None and
   observed_model is UNKNOWN until a separately qualified transport observes
   what actually executed. This is not T3/T4 review eligibility.
4. Preserve shared user permission choices, real grants, budget limits,
   reviewer independence, safety refusals and exact-head acceptance.

## Scope

The current library enforces model-free route configuration and provides a
final-argv model-pin check for future live adapters. Its offline demonstration
uses this same check after the synthetic routing seam. The Grok Bot desktop
route is still manual-only.

This does not implement provider login, dynamic discovery, live model
selection or transport execution. See RELEASE_GAPS.md.
