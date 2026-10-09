from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

CapabilityState = Literal["available", "disabled", "needs_setup", "ready", "unavailable"]
SetupState = Literal["not_started", "in_progress", "complete", "deferred"]


class CapabilityGroupOut(BaseModel):
    id: str
    name: str
    description: str


class RequirementDefOut(BaseModel):
    id: str
    label: str
    hard: bool
    action_label: str
    action_route: str


class RequirementOut(RequirementDefOut):
    met: bool


class IntegrationStatusOut(BaseModel):
    id: str
    label: str
    required: bool
    # Platform-level (env-configured) connection, not per host.
    connected: bool


class CapabilityDefinitionOut(BaseModel):
    id: str
    name: str
    description: str
    benefit: str
    group: str
    activation: Literal["core", "bound", "preference"]
    enforcement: Literal["always_on", "enforced", "advisory"]
    default_enabled: bool
    selectable: bool
    hard_dependencies: list[str]
    soft_dependencies: list[str]
    requirements: list[RequirementDefOut]
    integrations: list[str]
    routes: list[str]
    voice_tools: list[str]
    on_effect: str
    off_effect: str
    onboarding_setup: str | None
    config_keys: list[str]


class CapabilityCatalogOut(BaseModel):
    groups: list[CapabilityGroupOut]
    capabilities: list[CapabilityDefinitionOut]


class CapabilityStatusOut(BaseModel):
    """One capability as it stands for the authenticated host. Keeps the
    five concepts separate: `available` (platform integrations/eligibility),
    `enabled` (activation), `state` (readiness, derived), `integrations`
    (connection status) and `setup_state` (the host's own bookkeeping)."""

    id: str
    name: str
    description: str
    benefit: str
    group: str
    activation: Literal["core", "bound", "preference"]
    enforcement: Literal["always_on", "enforced", "advisory"]
    selectable: bool
    available: bool
    enabled: bool
    state: CapabilityState
    setup_state: SetupState
    # True when the backend is doing this for the host right now because of
    # existing configuration (e.g. a saved Guest Call Number), independent
    # of the stored preference. None when the capability has no such check.
    live_in_backend: bool | None
    requirements: list[RequirementOut]
    missing_dependencies: list[str]
    integrations: list[IntegrationStatusOut]
    can_enable: bool
    enable_blocked_reason: str | None
    can_disable: bool
    disable_blocked_reason: str | None
    # What the current on/off state actually does, never overstated.
    effect: str
    on_effect: str
    off_effect: str
    routes: list[str]
    onboarding_setup: str | None
    config: dict[str, Any]
    updated_at: datetime | None


class HostCapabilitiesOut(BaseModel):
    groups: list[CapabilityGroupOut]
    capabilities: list[CapabilityStatusOut]


class CapabilityUpdate(BaseModel):
    enabled: bool | None = None
    setup_state: SetupState | None = None
    # Merged into the stored config; keys must be in the registry's
    # config_keys for this capability.
    config: dict[str, Any] | None = None
