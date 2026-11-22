"""Has the host tested their agent since they last changed it?"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_session import CallSession
from app.models.user import User
from app.services import call_service


@dataclass(frozen=True)
class AgentTestStatus:
    needs_test: bool
    changed_at: datetime | None
    last_tested_at: datetime | None


async def get_status(db: AsyncSession, user: User) -> AgentTestStatus:
    """needs_test: a change was made and no browser test call has started after it.

    Only the host's own test calls count (CallSession.user_id), so one host's test can
    never clear another host's highlight.
    """
    last_tested = (
        await db.execute(
            select(func.max(CallSession.started_at)).where(
                CallSession.user_id == user.id,
                CallSession.caller_number == call_service.BROWSER_TEST_CALLER_NUMBER,
            )
        )
    ).scalar_one_or_none()
    changed = user.agent_customized_at
    needs = changed is not None and (last_tested is None or last_tested < changed)
    return AgentTestStatus(needs_test=needs, changed_at=changed, last_tested_at=last_tested)
