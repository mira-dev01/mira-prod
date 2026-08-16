from pydantic import BaseModel


class QualityEventAnalyticsOut(BaseModel):
    most_frequent: list[dict]
    over_time: list[dict]
