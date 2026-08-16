from pydantic import BaseModel


class ObjectionTagStats(BaseModel):
    tag: str
    total_calls: int
    resolved_count: int
    unresolved_count: int
    conversion_rate: float | None


class BaselineStats(BaseModel):
    total_calls: int
    resolved_count: int
    unresolved_count: int
    conversion_rate: float | None


class ObjectionConversionAnalyticsOut(BaseModel):
    by_tag: list[ObjectionTagStats]
    baseline: BaselineStats
