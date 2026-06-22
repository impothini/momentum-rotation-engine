"""Shared test fixtures for the momentum rotation engine.

All fixtures use synthetic, deterministic price data — no network calls.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Price series builders (deterministic synthetic data)
# ---------------------------------------------------------------------------


def make_trending_prices(
    n: int = 400,
    start_price: float = 100.0,
    daily_drift: float = 0.0005,
    seed: int = 42,
) -> pd.Series:
    """Trending upward price series (passes trend filter and has momentum)."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(daily_drift, 0.01, size=n)
    prices = start_price * np.cumprod(1 + returns)
    return pd.Series(prices)


def make_declining_prices(
    n: int = 400,
    start_price: float = 100.0,
    daily_drift: float = -0.002,
    seed: int = 99,
) -> pd.Series:
    """Declining price series (fails trend filter)."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(daily_drift, 0.01, size=n)
    prices = start_price * np.cumprod(1 + returns)
    return pd.Series(prices)


def make_flat_prices(n: int = 400, price: float = 100.0) -> pd.Series:
    """Flat price series (price == SMA, border case)."""
    return pd.Series([price] * n)


def make_date_index(
    n: int = 400,
    start: date = date(2020, 1, 2),
    freq: str = "B",
) -> pd.DatetimeIndex:
    """Business-day date index."""
    return pd.bdate_range(start=start.isoformat(), periods=n, freq=freq)


def make_price_dataframe(
    tickers: list[str],
    n: int = 400,
    drift: float = 0.0003,
    seed_offset: int = 0,
) -> pd.DataFrame:
    """Build a price DataFrame with one column per ticker."""
    idx = make_date_index(n)
    data = {}
    for i, ticker in enumerate(tickers):
        rng = np.random.default_rng(i + seed_offset)
        returns = rng.normal(drift, 0.01, size=n)
        prices = 100.0 * np.cumprod(1 + returns)
        data[ticker] = prices
    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def trending_series() -> pd.Series:
    return make_trending_prices(n=400)


@pytest.fixture
def declining_series() -> pd.Series:
    return make_declining_prices(n=400)


@pytest.fixture
def universe_tickers() -> list[str]:
    from momentum_agent.config import TRADABLE_UNIVERSE
    return TRADABLE_UNIVERSE


@pytest.fixture
def price_df(universe_tickers) -> pd.DataFrame:
    """400-day price DataFrame for full universe."""
    return make_price_dataframe(universe_tickers, n=400)
