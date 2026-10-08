import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from fastapi import HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.utils.dates import IST
from app.models.user import User


async def get_owned_property(db: AsyncSession, property_id: uuid.UUID, user: User) -> Property:
    property_ = await db.get(Property, property_id)
    if property_ is None or property_.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property not found")
    return property_


async def owned_property_ids(db: AsyncSession, user: User) -> list[uuid.UUID]:
    rows = (await db.scalars(select(Property.id).where(Property.user_id == user.id))).all()
    return list(rows)


@dataclass
class DateRange:
    """Optional start/end date filter, interpreted as IST calendar days --
    the same business day as app/utils/dates.today_ist and the day the
    dashboard's date picker means (it sends the host's local dates). These
    were UTC days until the Overview redesign; with local dates coming in,
    UTC days shifted every timestamp filter by 5.5 hours (a single picked
    day covered 05:30 IST to 05:30 IST the next day).

    `end_date` is an inclusive whole calendar day -- filtering must use
    `created_at < until`, not `<= end_date`, since a naive `<=` would exclude
    same-day records with a nonzero time-of-day component.
    """

    start_date: date | None
    end_date: date | None

    @property
    def since(self) -> datetime | None:
        if self.start_date is None:
            return None
        return datetime.combine(self.start_date, time.min, tzinfo=IST)

    @property
    def until(self) -> datetime | None:
        if self.end_date is None:
            return None
        return datetime.combine(self.end_date + timedelta(days=1), time.min, tzinfo=IST)


def date_range_query(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> DateRange:
    return DateRange(start_date=start_date, end_date=end_date)
