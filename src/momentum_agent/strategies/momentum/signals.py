"""Momentum signal calculations.

All functions are pure and deterministic.  No LLM, no randomness, no IO.

Signal formula (v1.0.0):
    momentum = adj_close[t-21] / adj_close[t-273] - 1

Trend filter:
    adj_close[t] > SMA200[t]

Correlation (for second-asset selection):
    90-day rolling correlation of daily returns <= 0.70

Volatility (for inverse-vol weighting):
    60-day annualised standard deviation of daily returns
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Momentum score
# ---------------------------------------------------------------------------


def calculate_momentum_score(
    adj_prices: pd.Series,
    exclude_recent: int = 21,
    lookback: int = 273,
) -> float:
    """Compute momentum score for a price series ending at signal date t.

    Formula: adj_close[t - exclude_recent] / adj_close[t - lookback] - 1

    adj_prices must have at least (lookback + 1) observations.
    Index convention:
        iloc[-1]            = t        (signal date)
        iloc[-(k+1)]        = t - k    (k trading days before signal date)
        iloc[-(exclude_recent+1)]  = t - 21  (near price)
        iloc[-(lookback+1)]        = t - 273 (far price)
    """
    min_length = lookback + 1
    if len(adj_prices) < min_length:
        raise ValueError(
            f"Insufficient price history for momentum: "
            f"need {min_length}, got {len(adj_prices)}"
        )

    near_price = float(adj_prices.iloc[-(exclude_recent + 1)])
    far_price = float(adj_prices.iloc[-(lookback + 1)])

    if not (np.isfinite(near_price) and np.isfinite(far_price)):
        raise ValueError(
            f"Non-finite price: near={near_price}, far={far_price}"
        )
    if near_price <= 0 or far_price <= 0:
        raise ValueError(
            f"Non-positive price: near={near_price}, far={far_price}"
        )

    return near_price / far_price - 1.0


# ---------------------------------------------------------------------------
# Trend filter
# ---------------------------------------------------------------------------


def calculate_sma(prices: pd.Series, window: int) -> pd.Series:
    """Simple moving average over a rolling window."""
    return prices.rolling(window=window, min_periods=window).mean()


def apply_trend_filter(
    adj_prices: pd.Series,
    sma_window: int = 200,
) -> bool:
    """Return True if current price is above the SMA(sma_window).

    If there are fewer than sma_window data points, the filter fails (False).
    """
    if len(adj_prices) < sma_window:
        return False

    current_price = float(adj_prices.iloc[-1])
    sma_series = calculate_sma(adj_prices, sma_window)
    sma_value = float(sma_series.iloc[-1])

    if not (np.isfinite(current_price) and np.isfinite(sma_value)):
        return False

    return current_price > sma_value


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def calculate_rolling_correlation(
    prices_a: pd.Series,
    prices_b: pd.Series,
    window: int = 90,
) -> Optional[float]:
    """Return correlation of daily returns over the last `window` observations.

    Returns None if there is insufficient data.
    """
    returns_a = prices_a.pct_change().dropna()
    returns_b = prices_b.pct_change().dropna()

    # Align by date
    aligned = pd.concat([returns_a, returns_b], axis=1).dropna()

    if len(aligned) < window:
        return None

    recent = aligned.iloc[-window:]
    corr = float(recent.iloc[:, 0].corr(recent.iloc[:, 1]))

    if not np.isfinite(corr):
        return None

    return corr


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


def calculate_annualized_volatility(
    prices: pd.Series,
    window: int = 60,
) -> Optional[float]:
    """Return annualised volatility of daily returns over last `window` obs.

    Returns None if insufficient data or volatility is zero / non-finite.
    """
    returns = prices.pct_change().dropna()

    if len(returns) < window:
        return None

    recent = returns.iloc[-window:]
    vol = float(recent.std()) * np.sqrt(252)

    if not np.isfinite(vol) or vol <= 0:
        return None

    return vol
