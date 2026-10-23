"""One user permission preference, independent of provider.

This module resolves configuration. A preference is not an authority grant or a
claim that a vendor client enforces it. No provider process is launched here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

PROVIDERS = ("claude", "codex", "supergrok_cli", "grok_bot_desktop")
CAPABILITIES = (
    "read_files", "write_files", "run_commands", "network", "git_commit",
    "git_push", "connected_tools", "read_credentials", "merge", "install",
)
CHOICES = ("allow", "ask", "deny")
# Native policy options belong to the future policy translator, not a route.
POLICY_FLAGS = (
    "--sandbox", "-s", "--permission-mode", "--permission-profile", "-P",
    "--ask-for-approval", "-a", "--full-auto", "--yolo", "--always-approve",
    "--dangerously-bypass-approvals-and-sandbox", "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions", "--allow", "--deny", "--tools",
    "--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools",
    "--add-dir", "--settings", "--config", "-c", "--profile",
)


class ConfigurationError(ValueError):
    """A configuration that cannot preserve the shared user preference."""


def _object(value, label):
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be an object")
    return value


@dataclass(frozen=True)
class PermissionPolicy:
    """Immutable, normalized user choices; no provider-specific override field."""

    _choices: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if not isinstance(self._choices, tuple) or len(self._choices) != len(CAPABILITIES):
            raise ConfigurationError("Policy must contain every capability exactly once in canonical order")
        for capability, item in zip(CAPABILITIES, self._choices):
            if (not isinstance(item, tuple) or len(item) != 2
                    or item[0] != capability or item[1] not in CHOICES):
                raise ConfigurationError("Invalid or noncanonical permission choice")

    @classmethod
    def from_dict(cls, document):
        document = _object(document, "permission policy")
        if set(document) - {"version", "default", "capabilities"}:
            raise ConfigurationError("Unknown permission-policy field; provider overrides are not supported")
        if type(document.get("version")) is not int or document["version"] != 1:
            raise ConfigurationError("Permission policy version must be 1")
        default = document.get("default")
        if default not in CHOICES:
            raise ConfigurationError("Choose a shared default: allow, ask or deny")
        selected = _object(document.get("capabilities", {}), "capabilities")
        if set(selected) - set(CAPABILITIES):
            raise ConfigurationError("Unknown capability")
        if any(value not in CHOICES for value in selected.values()):
            raise ConfigurationError("Each permission must be allow, ask or deny")
        return cls(tuple((name, selected.get(name, default)) for name in CAPABILITIES))

    def preference(self, capability):
        if capability not in CAPABILITIES:
            raise ConfigurationError(f"Unknown capability: {capability}")
        return dict(self._choices)[capability]

    def as_dict(self):
        return dict(self._choices)

    @property
    def fingerprint(self):
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class ConnectorConfiguration:
    policy: PermissionPolicy
    providers: Mapping[str, Mapping[str, object]]

    def permissions_for(self, provider):
        if provider not in self.providers:
            raise ConfigurationError(f"Provider is not configured: {provider}")
        return self.policy


def load_configuration(path):
    """Read a transport configuration and its single adjacent user policy.

    The policy file must be inside the configuration directory. Native policy
    flags and per-provider policy fields are refused rather than silently taking
    precedence. This validates configuration, not effective vendor permissions.
    """
    path = Path(path).resolve()
    document = _object(json.loads(path.read_text()), "configuration")
    if set(document) - {"description", "permission_policy", "providers"}:
        raise ConfigurationError("Unknown configuration field")
    policy_ref = document.get("permission_policy")
    if not isinstance(policy_ref, str) or not policy_ref.strip() or Path(policy_ref).is_absolute():
        raise ConfigurationError("Select one relative permission_policy file")
    policy_path = (path.parent / policy_ref).resolve()
    if not policy_path.is_relative_to(path.parent):
        raise ConfigurationError("Permission policy must stay inside the configuration directory")
    policy = PermissionPolicy.from_dict(json.loads(policy_path.read_text()))
    providers = _object(document.get("providers"), "providers")
    if not providers or set(providers) - set(PROVIDERS):
        raise ConfigurationError("Configure at least one supported provider")
    routes = {}
    for name, raw_route in providers.items():
        route = _object(raw_route, name)
        if set(route) - {"prompt_mode", "cmd", "transport", "automatic_connector"}:
            raise ConfigurationError(f"{name}: provider-specific permission/configuration override refused")
        cmd = route.get("cmd")
        if cmd is not None:
            if not isinstance(cmd, list) or not cmd or any(not isinstance(arg, str) for arg in cmd):
                raise ConfigurationError(f"{name}: cmd must be a nonempty string array or null")
            for arg in cmd:
                if any(arg == flag or arg.startswith(flag + "=")
                       or (len(flag) == 2 and arg.startswith(flag) and not arg.startswith("--"))
                       for flag in POLICY_FLAGS):
                    raise ConfigurationError(f"{name}: native permission/configuration flag refused: {arg}")
                if "dangerously" in arg.lower():
                    raise ConfigurationError(f"{name}: permission bypass flag refused")
        routes[name] = MappingProxyType({**route, "cmd": tuple(cmd) if cmd is not None else None})
    return ConnectorConfiguration(policy, MappingProxyType(routes))
