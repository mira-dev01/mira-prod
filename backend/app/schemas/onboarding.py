from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OnboardingStep = Literal["profile", "capabilities", "setup", "review"]
OnboardingStatus = Literal["not_started", "in_progress", "completed"]
ImportStatus = Literal["importing", "completed", "failed"]


class FirstPropertyImportOut(BaseModel):
    airbnb_url: str
    ical_url: str | None = None
    live_pricing: bool = False
    status: ImportStatus
    property_id: str | None = None
    property_name: str | None = None
    error: str | None = None
    requested_at: datetime | None = None
    finished_at: datetime | None = None


class OnboardingOut(BaseModel):
    status: OnboardingStatus
    source: Literal["onboarding", "legacy"] | None
    current_step: str
    completed_steps: list[str]
    selected_capabilities: list[str]
    first_property: FirstPropertyImportOut | None
    completed_at: datetime | None
    # Selections that couldn't be applied as chosen (e.g. turning off a
    # capability that's still live in the backend) -- only on a save.
    selection_notes: list[str] = []


class OnboardingProgressUpdate(BaseModel):
    current_step: OnboardingStep | None = None
    completed_steps: list[OnboardingStep] | None = None
    # Full desired selection of selectable capability ids. Applied through
    # capability_service, same validation as Settings.
    selected_capabilities: list[str] | None = None


class FirstPropertyImportRequest(BaseModel):
    airbnb_url: str = Field(min_length=1, max_length=2048)
    ical_url: str | None = Field(default=None, max_length=1024)
    live_pricing: bool = False
