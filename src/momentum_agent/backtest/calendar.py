"""Trading calendar using pandas-market-calendars (NYSE).

Signal date: last trading day of each month.
Execution:   next trading session after signal date.

Do not approximate trading days — use the exchange calendar directly.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Optional

import pandas as pd
import pandas_market_calendars as mcal


class TradingCalendar:
    """Wraps the NYSE calendar from pandas-market-calendars."""

    def __init__(self, exchange: str = "NYSE") -> None:
        self._cal = mcal.get_calendar(exchange)

    def get_trading_days(self, start: date, end: date) -> list[date]:
        """Return all trading days (inclusive) in [start, end]."""
        schedule = self._cal.schedule(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        return [ts.date() for ts in schedule.index]

    def is_trading_day(self, d: date) -> bool:
        days = self.get_trading_days(d, d)
        return len(days) > 0

    def get_month_end_signal_dates(
        self, start: date, end: date
    ) -> set[date]:
        """Return the set of last trading days of each calendar month."""
        trading_days = self.get_trading_days(start, end)
        if not trading_days:
            return set()

        series = pd.Series(trading_days)
        # Group by (year, month) and take the last in each group
        df = pd.DataFrame({"date": series})
        df["ym"] = df["date"].apply(lambda d: (d.year, d.month))
        signal_dates = set(df.groupby("ym")["date"].last().tolist())
        return signal_dates

    def next_trading_day(self, from_date: date) -> Optional[date]:
        """Return the first trading day strictly after from_date."""
        end_search = date(from_date.year + 1, from_date.month, from_date.day)
        days = self.get_trading_days(from_date, end_search)
        for d in days:
            if d > from_date:
                return d
        return None
