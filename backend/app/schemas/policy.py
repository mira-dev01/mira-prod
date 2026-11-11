import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.services.policy_documents import TEXT_MAX, TITLE_MAX

PolicyStatus = Literal["draft", "active"]
# everyone: all properties follow the host-wide policy. some_differ: some
# properties have their own. property_only: no host-wide policy, only some
# properties have one. unset: nothing yet.
RowState = Literal["everyone", "some_differ", "property_only", "unset"]


class PolicyOut(BaseModel):
    id: uuid.UUID
    topic: str
    title: str
    text: str
    status: PolicyStatus
    property_id: uuid.UUID | None
    property_name: str | None
    source_document_id: uuid.UUID | None
    source_filename: str | None
    source_excerpt: str | None
    review_notes: list[str]
    created_at: datetime
    updated_at: datetime


class PolicyCreate(BaseModel):
    topic: str
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    # null = the whole portfolio; an id = that property's own version.
    property_id: uuid.UUID | None = None


class PolicyCreateScoped(BaseModel):
    """Add one policy to the whole portfolio (no property_ids) or to each chosen property."""

    topic: str
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    property_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class PolicyUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX)
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    topic: str | None = None


class PolicyRowOut(BaseModel):
    topic: str
    label: str
    hint: str
    state: RowState
    override_count: int
    items: list[PolicyOut]


class PolicyDocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    item_count: int
    truncated: bool
    created_at: datetime


class PolicyTopicOut(BaseModel):
    key: str
    label: str
    hint: str


class PolicyLabelOut(BaseModel):
    key: str
    label: str


class PolicyPropertyOut(BaseModel):
    id: uuid.UUID
    name: str
    city: str | None
    # What this property's approved policies say about parties and pets
    # (derived, see app/services/policy_stance.py).
    labels: list[PolicyLabelOut] = []
    # The live policies this property actually follows (host-wide ones that are
    # about other properties only are not in it).
    policy_ids: list[uuid.UUID] = []
    # Policies waiting on the host for this property: drafts and placeholders
    # (a policy that named a property the dashboard could not map) to approve,
    # edit or delete from the property's pane.
    pending_policy_ids: list[uuid.UUID] = []


class PolicyOverviewOut(BaseModel):
    rows: list[PolicyRowOut]
    drafts: list[PolicyOut]
    documents: list[PolicyDocumentOut]
    properties: list[PolicyPropertyOut]
    topics: list[PolicyTopicOut]
    # Placeholders that name no property the host has: nothing to assign them to
    # until the host clarifies which property they are about.
    unmapped_policy_ids: list[uuid.UUID] = []


class PolicyUploadOut(BaseModel):
    document: PolicyDocumentOut
    drafts: list[PolicyOut]
    # Policies in the file that are already live (or already waiting), left out.
    skipped: int


class PolicyApproveIn(BaseModel):
    """Optional: change who a draft applies to while approving it. Send
    nothing to keep the scope the document gave it."""

    apply_to_all: bool = False
    property_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class PolicyApproveOut(BaseModel):
    policy: PolicyOut
    # True when approving replaced a live policy for the same topic.
    replaced: bool


class PolicyForPropertyIn(BaseModel):
    property_id: uuid.UUID


class PolicyApproveAllIn(BaseModel):
    document_id: uuid.UUID | None = None


class PolicyApproveAllOut(BaseModel):
    approved: int
