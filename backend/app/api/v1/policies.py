import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.policy import (
    PolicyApproveAllIn,
    PolicyApproveAllOut,
    PolicyApproveIn,
    PolicyApproveOut,
    PolicyCreate,
    PolicyCreateScoped,
    PolicyOut,
    PolicyOverviewOut,
    PolicyUpdate,
    PolicyUploadOut,
)
from app.services import policy_documents, policy_service
from app.services.policy_documents import PolicyParseError
from app.services.policy_service import PolicyError

router = APIRouter(prefix="/policies", tags=["policies"])


def _http(exc: PolicyError | PolicyParseError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if getattr(exc, "not_found", False) else status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(code, str(exc))


async def _out(db: AsyncSession, user: User, policy) -> dict:
    names, filenames = await policy_service.label_maps(db, user.id)
    return policy_service.policy_out(policy, names, filenames)


@router.get("", response_model=PolicyOverviewOut)
async def get_policies(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return await policy_service.overview(db, current_user)


@router.post("", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def create_policy(
    payload: PolicyCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        policy = await policy_service.create_policy(
            db, current_user, topic=payload.topic, text=payload.text, title=payload.title, property_id=payload.property_id
        )
    except PolicyError as exc:
        raise _http(exc)
    return await _out(db, current_user, policy)


@router.post("/scoped", response_model=list[PolicyOut], status_code=status.HTTP_201_CREATED)
async def create_policies_for_properties(
    payload: PolicyCreateScoped, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """One policy for the whole portfolio, or for each of the chosen properties (all or nothing)."""
    try:
        created = await policy_service.create_policies(
            db, current_user, topic=payload.topic, text=payload.text, title=payload.title, property_ids=payload.property_ids
        )
    except PolicyError as exc:
        raise _http(exc)
    names, filenames = await policy_service.label_maps(db, current_user.id)
    return [policy_service.policy_out(p, names, filenames) for p in created]


@router.post("/documents", response_model=PolicyUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_policy_document(
    file: UploadFile = File(...), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """Reads the file into DRAFT policies for the host to review. Nothing is
    live until each one is approved."""
    filename = (file.filename or "policy").strip()
    try:
        policy_documents.kind_of(filename)  # refuse an unsupported type before reading it all
        data = await file.read(policy_documents.MAX_BYTES + 1)
        document, drafts, skipped = await policy_service.ingest_document(
            db, current_user, filename=filename, content_type=file.content_type, data=data
        )
    except (PolicyError, PolicyParseError) as exc:
        raise _http(exc)
    names, filenames = await policy_service.label_maps(db, current_user.id)
    return {
        "document": {
            "id": document.id,
            "filename": document.filename,
            "item_count": document.item_count,
            "truncated": document.truncated,
            "created_at": document.created_at,
        },
        "drafts": [policy_service.policy_out(d, names, filenames) for d in drafts],
        "skipped": skipped,
    }


@router.post("/approve-all", response_model=PolicyApproveAllOut)
async def approve_all_policies(
    payload: PolicyApproveAllIn, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    return {"approved": await policy_service.approve_all(db, current_user, payload.document_id)}


@router.patch("/{policy_id}", response_model=PolicyOut)
async def update_policy(
    policy_id: uuid.UUID,
    payload: PolicyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        policy = await policy_service.update_policy(
            db, current_user, policy_id, text=payload.text, title=payload.title, topic=payload.topic
        )
    except PolicyError as exc:
        raise _http(exc)
    return await _out(db, current_user, policy)


@router.post("/{policy_id}/approve", response_model=PolicyApproveOut)
async def approve_policy(
    policy_id: uuid.UUID,
    payload: PolicyApproveIn | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        policy, replaced = await policy_service.approve(
            db,
            current_user,
            policy_id,
            property_ids=payload.property_ids if payload else None,
            all_properties=payload.apply_to_all if payload else False,
        )
    except PolicyError as exc:
        raise _http(exc)
    return {"policy": await _out(db, current_user, policy), "replaced": replaced}


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    try:
        await policy_service.delete_policy(db, current_user, policy_id)
    except PolicyError as exc:
        raise _http(exc)
