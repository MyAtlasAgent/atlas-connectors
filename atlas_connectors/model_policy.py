"""Model-agnostic connection intent for the Atlas Connectors source preview.

No model catalog, tier assumptions, account probing, paid API fallback or
provider launch lives here. The authenticated provider client owns the model
available to the user, including a model they pick in that client's UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple


class ModelPinError(ValueError):
    """A route attempts to require a model that may not be available."""


# Native selectors rather than model names. Guard the entire resulting argv,
# not merely the template: a later model router can add a pin too.
_MODEL_FLAGS = frozenset({
    "--model", "--models", "--model-id", "--model_id", "--model-name",
    "--model_name", "--preferred-model", "--default-model",
    "--fallback-model", "--required-model",
})


@dataclass(frozen=True)
class ModelSelection:
    """One unforced connection strategy; NOT observed model identity or a grant."""

    strategy: str = "provider_session"
    requested_model: Optional[str] = None
    observed_model: str = "UNKNOWN"
    observation_source: str = "UNKNOWN"

    # A new instance is not evidence of having queried the provider.
    # The actual served model must be observed by a qualified transport.
    # Do not infer T3/T4 authority from this configuration object.


PROVIDER_SELECTED = ModelSelection()


def ensure_unpinned_command(argv: Sequence[str]) -> Tuple[str, ...]:
    """Refuse native model flags in command templates and post-routing output.

    The user chooses within the provider's own account/session. Leaving model
    selection to the provider avoids Atlas breaking when free-tier models move.
    Do not use this function as a substitute for live native permission checks.
    """
    if not isinstance(argv, (list, tuple)) or any(not isinstance(x, str) for x in argv):
        raise ModelPinError("Provider command must be a list or tuple of strings")
    for arg in argv:
        option = arg.split("=", 1)[0]
        if option in _MODEL_FLAGS or arg in {"-m"} or arg.startswith("-m="):
            raise ModelPinError(
                "Atlas provider connections do not require a model; "
                "select an available model in the provider's own client"
            )
    return tuple(argv)

