"""Scores a finished call from the rows it left behind, and prints the scorecard. READ-ONLY: it only
selects, so it is safe against any database, and it is how a threshold change is checked against real calls
before it ships.

    python -m scripts.rescore_call 56947e99-1e17-4315-89ac-6e993cfcbf1d
    python -m scripts.rescore_call --recent 20      # the 20 latest calls, one line each

A call from before the usage/performance telemetry has no timing, so only the transcript rules can fire.
"""

import argparse
import asyncio
import json
import sys
import uuid

from sqlalchemy import select

from app.database import AsyncSessionLocal, engine
from app.models.call_quality_event import CALL_METRICS_RULE, CallQualityEvent
from app.models.call_session import CallSession
from app.services.call_scorecard import build_scorecard


async def _score(db, session: CallSession) -> dict:
    summary = await db.scalar(
        select(CallQualityEvent.metadata_json["summary"]).where(
            CallQualityEvent.call_session_id == session.id, CallQualityEvent.rule == CALL_METRICS_RULE
        )
    )
    return build_scorecard(transcript=session.transcript, metrics_summary=summary or None)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("call_id", nargs="?", type=uuid.UUID)
    parser.add_argument("--recent", type=int, help="score the N most recent calls instead")
    args = parser.parse_args()
    if args.call_id is None and not args.recent:
        parser.error("give a call id or --recent N")

    async with AsyncSessionLocal() as db:
        if args.call_id is not None:
            session = await db.get(CallSession, args.call_id)
            if session is None:
                print(f"no call {args.call_id}", file=sys.stderr)
                return 1
            print(json.dumps(await _score(db, session), indent=2, default=str))
        else:
            sessions = (await db.scalars(select(CallSession).order_by(CallSession.created_at.desc()).limit(args.recent))).all()
            for session in sessions:
                card = await _score(db, session)
                reasons = ", ".join(flag["code"] for flag in card["flags"]) or "-"
                print(f"{session.id}  {'FLAGGED' if card['flagged'] else 'ok     '}  {reasons}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
