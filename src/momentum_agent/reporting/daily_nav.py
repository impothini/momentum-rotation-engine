"""Daily NAV writer — one row per trading day.

Columns (spec §daily_nav.csv):
  date, nav, cash, positions_value, high_water_mark,
  drawdown_pct, strategy_state, data_status, proxy_used
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

DAILY_NAV_COLUMNS = [
    "date",
    "nav",
    "cash",
    "positions_value",
    "high_water_mark",
    "drawdown_pct",
    "strategy_state",
    "data_status",
    "proxy_used",
]


@dataclass
class DailyNavRow:
    date: date
    nav: Optional[float]
    cash: float
    positions_value: Optional[float]
    high_water_mark: Optional[float]
    drawdown_pct: Optional[float]
    strategy_state: str
    data_status: str
    proxy_used: bool

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "nav": self.nav,
            "cash": self.cash,
            "positions_value": self.positions_value,
            "high_water_mark": self.high_water_mark,
            "drawdown_pct": self.drawdown_pct,
            "strategy_state": self.strategy_state,
            "data_status": self.data_status,
            "proxy_used": self.proxy_used,
        }


class DailyNavWriter:
    """Accumulates daily NAV rows and writes daily_nav.csv."""

    def __init__(self) -> None:
        self._rows: list[DailyNavRow] = []

    def record(self, row: DailyNavRow) -> None:
        self._rows.append(row)

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=DAILY_NAV_COLUMNS)
        data = [r.to_dict() for r in self._rows]
        df = pd.DataFrame(data)
        for col in DAILY_NAV_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[DAILY_NAV_COLUMNS]

    def write_csv(self, path: Path) -> None:
        self.to_dataframe().to_csv(path, index=False)

    def get_nav_series(self) -> pd.Series:
        """Return a NAV time series (date → nav), dropping None values."""
        df = self.to_dataframe()
        s = pd.Series(
            df["nav"].values,
            index=pd.to_datetime(df["date"]),
            name="nav",
        )
        return s.dropna().astype(float)
