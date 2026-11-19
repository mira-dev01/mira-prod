from datetime import datetime
from typing import Literal

from pydantic import BaseModel

OnboardingStep = Literal["profile", "capabilities", "setup", "review"]
OnboardingStatus = Literal["not_started", "in_progress", "completed"]


class OnboardingOut(BaseModel):
    status: OnboardingStatus
    source: Literal["onboarding", "legacy"] | None
    current_step: str
    completed_steps: list[str]
    selected_capabilities: list[str]
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
