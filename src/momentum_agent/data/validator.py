"""Market data validation — bar-level and signal-level checks.

Rules:
- Missing price → invalid
- NaN / null price → invalid
- Non-positive price → invalid
- Stale bar (same date repeated) → invalid
- Per-ticker failure_count tracks consecutive invalid bars
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class BarValidation:
    ticker: str
    bar_date: date
    is_valid: bool
    reason: Optional[str] = None


@dataclass
class DataValidationState:
    """Tracks failure counts per ticker across trading days."""

    failure_counts: dict[str, int] = field(default_factory=dict)

    def record_valid(self, ticker: str) -> None:
        self.failure_counts[ticker] = 0

    def record_failure(self, ticker: str) -> int:
        count = self.failure_counts.get(ticker, 0) + 1
        self.failure_counts[ticker] = count
        return count

    def get_failure_count(self, ticker: str) -> int:
        return self.failure_counts.get(ticker, 0)


def validate_bar(
    ticker: str,
    bar_date: date,
    price: object,
    prev_date: Optional[date] = None,
) -> BarValidation:
    """Validate a single price bar."""
    if price is None:
        return BarValidation(ticker, bar_date, False, "missing_price")

    try:
        p = float(price)
    except (TypeError, ValueError):
        return BarValidation(ticker, bar_date, False, "non_numeric_price")

    if not np.isfinite(p):
        return BarValidation(ticker, bar_date, False, "nan_or_inf_price")

    if p <= 0:
        return BarValidation(ticker, bar_date, False, "non_positive_price")

    if prev_date is not None and bar_date == prev_date:
        return BarValidation(ticker, bar_date, False, "stale_date")

    return BarValidation(ticker, bar_date, True)


def validate_price_series(
    ticker: str,
    prices: pd.Series,
    min_length: int = 1,
) -> tuple[bool, Optional[str]]:
    """Validate an entire price series for signal computation eligibility."""
    if len(prices) < min_length:
        return False, f"insufficient_length:{len(prices)}<{min_length}"

    null_count = prices.isna().sum()
    if null_count > 0:
        return False, f"contains_nulls:{null_count}"

    non_positive = (prices <= 0).sum()
    if non_positive > 0:
        return False, f"non_positive_prices:{non_positive}"

    return True, None


def validate_signal_inputs(
    adj_prices: pd.DataFrame,
    tickers: list[str],
    t_idx: int,
    min_lookback: int,
) -> dict[str, bool]:
    """
    Check each ticker has sufficient, valid price history at position t_idx.

    Returns dict[ticker → is_valid].
    """
    result: dict[str, bool] = {}
    for ticker in tickers:
        if ticker not in adj_prices.columns:
            result[ticker] = False
            continue

        series = adj_prices[ticker].iloc[: t_idx + 1]
        ok, _ = validate_price_series(ticker, series, min_length=min_lookback + 1)
        result[ticker] = ok
    return result
