"""Per-call mutable state tracked programmatically alongside the LLM's own
context, rather than relying solely on the model remembering facts across
turns (see memory-architecture-plan.md section 2 for the bug this fixes).

Currently only tracks which property is "locked" for the rest of a Lead
Agent (portfolio-wide) call, once the guest has named/selected one -- Guest
Support calls are already fixed to one property before the pipeline is
built (see app/voice/pipeline.py) and never need this.
"""

from dataclasses import dataclass


@dataclass
class ConversationState:
    selected_property_id: str | None = None
    selected_property_name: str | None = None

    def lock_property(self, property_id: str | None, property_name: str | None = None) -> None:
        if not property_id:
            return
        self.selected_property_id = property_id
        if property_name:
            self.selected_property_name = property_name
