"""Reads and writes the host's Agent rules (schemas/agent_config.py).

Reads are tolerant, writes are strict: a stored value that no longer validates
(an old shape, a hand edit) falls back to the default for that section and is
logged, so a bad row can never stop a live call; the API only ever stores
values that validate.
"""

import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.models.user import User
from app.schemas.agent_config import (
    AgentConfig,
    AgentConfigOut,
    AgentConfigUpdate,
    PaymentConfig,
    PaymentOverride,
)

logger = logging.getLogger(__name__)

_SECTIONS = ("collect", "hot_lead", "booking", "payment", "busy_calls")


def parse_config(stored: dict | None) -> AgentConfig:
    """The effective portfolio config for a stored document (None = defaults).
    Each section is validated on its own, so one bad section costs only itself."""
    stored = stored if isinstance(stored, dict) else {}
    try:
        return AgentConfig.model_validate(stored)
    except ValidationError:
        pass
    good = {}
    for name in _SECTIONS:
        if name not in stored:
            continue
        try:
            AgentConfig.model_validate({name: stored[name]})
            good[name] = stored[name]
        except ValidationError:
            logger.warning("agent_config: invalid stored %r section ignored, using defaults", name)
    return AgentConfig.model_validate(good)


def parse_payment_override(stored: dict | None) -> PaymentOverride | None:
    if not stored:
        return None
    try:
        return PaymentOverride.model_validate(stored.get("payment") or {})
    except ValidationError:
        logger.warning("agent_config: invalid stored payment override ignored")
        return None


def get_config(user: User) -> AgentConfig:
    return parse_config(user.agent_config)


def resolve_config(user: User, property_: Property | None = None) -> AgentConfig:
    """The config a call about `property_` should follow: the portfolio config
    with that property's payment override laid over it."""
    config = get_config(user)
    override = parse_payment_override(property_.agent_config_overrides) if property_ is not None else None
    if override is None:
        return config
    merged = config.payment.model_dump()
    merged.update(override.model_dump(exclude_none=True))
    return config.model_copy(update={"payment": PaymentConfig.model_validate(merged)})


async def get_out(db: AsyncSession, user: User) -> AgentConfigOut:
    rows = (
        await db.execute(
            select(Property.id, Property.agent_config_overrides).where(
                Property.user_id == user.id, Property.agent_config_overrides.is_not(None)
            )
        )
    ).all()
    overrides = {}
    for property_id, stored in rows:
        override = parse_payment_override(stored)
        if override is not None:
            overrides[str(property_id)] = override
    return AgentConfigOut(config=get_config(user), payment_overrides=overrides)


async def update_config(db: AsyncSession, user: User, payload: AgentConfigUpdate) -> AgentConfigOut:
    # Start from the validated current config, replace the sections sent, and
    # store the whole thing explicitly so later default changes never silently
    # move a host who has saved their choices.
    current = get_config(user).model_dump()
    for name in _SECTIONS:
        section = getattr(payload, name)
        if section is not None:
            current[name] = section.model_dump()
    user.agent_config = AgentConfig.model_validate(current).model_dump()
    await db.commit()
    await db.refresh(user)
    return await get_out(db, user)


async def set_payment_override(
    db: AsyncSession, user: User, property_: Property, override: PaymentOverride | None
) -> AgentConfigOut:
    """Sets (or, with None, clears) a property's payment override."""
    property_.agent_config_overrides = (
        {"payment": override.model_dump(exclude_none=True)} if override is not None else None
    )
    await db.commit()
    return await get_out(db, user)
