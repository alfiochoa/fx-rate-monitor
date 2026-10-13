"""Schema validation for API responses and the bundled snapshot."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, field_validator


class Snapshot(BaseModel):
    """Daily rates, 1 unit of `base` = rate units of the quote currency."""
    source: str
    base: str
    fetched_at: datetime
    rates: dict[date, dict[str, float]]

    @field_validator("rates")
    @classmethod
    def positive(cls, v):
        for d, row in v.items():
            for ccy, r in row.items():
                if not (r > 0) or len(ccy) != 3 or not ccy.isupper():
                    raise ValueError(f"bad rate {ccy}={r} on {d}")
        return v

    @property
    def currencies(self) -> list[str]:
        return sorted({c for row in self.rates.values() for c in row} | {self.base})

    @property
    def last_date(self) -> date:
        return max(self.rates)


class FrankfurterRange(BaseModel):
    """GET /v1/{start}..{end}?base=EUR&symbols=USD,GBP"""
    base: str
    start_date: date
    end_date: date
    rates: dict[date, dict[str, float]]
