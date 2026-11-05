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


class PolicyPropertyOut(BaseModel):
    id: uuid.UUID
    name: str
    city: str | None


class PolicyOverviewOut(BaseModel):
    rows: list[PolicyRowOut]
    drafts: list[PolicyOut]
    documents: list[PolicyDocumentOut]
    properties: list[PolicyPropertyOut]
    topics: list[PolicyTopicOut]


class PolicyUploadOut(BaseModel):
    document: PolicyDocumentOut
    drafts: list[PolicyOut]
    # Policies in the file that are already live (or already waiting), left out.
    skipped: int


class PolicyApproveOut(BaseModel):
    policy: PolicyOut
    # True when approving replaced a live policy for the same topic.
    replaced: bool


class PolicyApproveAllIn(BaseModel):
    document_id: uuid.UUID | None = None


class PolicyApproveAllOut(BaseModel):
    approved: int
