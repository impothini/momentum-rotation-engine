"""Performance metrics computation.

All metrics derived from daily NAV and benchmark NAV series.

Metrics (spec §metrics.json):
  CAGR, Annualized Volatility, Sharpe Ratio, Sortino Ratio, Calmar Ratio,
  Maximum Drawdown, Number of Trades, Turnover,
  Correlation to VOO, Correlation to VTI, Correlation to 60/40.

Risk-free rate: SGOV realized return (from the daily NAV series or a
  separate SGOV price series).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Core building blocks
# ---------------------------------------------------------------------------


def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change().dropna()


def calculate_cagr(nav: pd.Series) -> Optional[float]:
    """Compound annual growth rate."""
    if len(nav) < 2:
        return None
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    if years <= 0 or nav.iloc[0] <= 0:
        return None
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1.0 / years) - 1.0)


def calculate_annualized_vol(nav: pd.Series) -> Optional[float]:
    """Annualised standard deviation of daily returns."""
    rets = daily_returns(nav)
    if len(rets) < 2:
        return None
    return float(rets.std() * np.sqrt(252))


def calculate_max_drawdown(nav: pd.Series) -> Optional[float]:
    """Maximum peak-to-trough drawdown (as a negative fraction)."""
    if len(nav) < 2:
        return None
    rolling_max = nav.cummax()
    drawdowns = (nav - rolling_max) / rolling_max
    return float(drawdowns.min())


def calculate_sharpe(
    nav: pd.Series,
    risk_free_nav: Optional[pd.Series] = None,
) -> Optional[float]:
    """Annualised Sharpe ratio using SGOV as risk-free rate."""
    rets = daily_returns(nav)
    if risk_free_nav is not None and len(risk_free_nav) > 1:
        rf_rets = daily_returns(risk_free_nav).reindex(rets.index).fillna(0.0)
    else:
        rf_rets = pd.Series(0.0, index=rets.index)

    excess = rets - rf_rets
    if excess.std() == 0 or len(excess) < 2:
        return None
    return float((excess.mean() / excess.std()) * np.sqrt(252))


def calculate_sortino(
    nav: pd.Series,
    risk_free_nav: Optional[pd.Series] = None,
) -> Optional[float]:
    """Annualised Sortino ratio (downside deviation denominator)."""
    rets = daily_returns(nav)
    if risk_free_nav is not None and len(risk_free_nav) > 1:
        rf_rets = daily_returns(risk_free_nav).reindex(rets.index).fillna(0.0)
    else:
        rf_rets = pd.Series(0.0, index=rets.index)

    excess = rets - rf_rets
    downside = excess[excess < 0]
    if len(downside) < 2 or downside.std() == 0:
        return None
    downside_std = float(downside.std() * np.sqrt(252))
    annualized_excess = float(excess.mean() * 252)
    return annualized_excess / downside_std


def calculate_calmar(nav: pd.Series) -> Optional[float]:
    """Calmar ratio: CAGR / abs(max_drawdown)."""
    cagr = calculate_cagr(nav)
    mdd = calculate_max_drawdown(nav)
    if cagr is None or mdd is None or mdd == 0:
        return None
    return float(cagr / abs(mdd))


def calculate_correlation(
    nav_a: pd.Series,
    nav_b: pd.Series,
) -> Optional[float]:
    """Correlation of daily returns between two NAV series."""
    rets_a = daily_returns(nav_a)
    rets_b = daily_returns(nav_b)
    aligned = pd.concat([rets_a, rets_b], axis=1).dropna()
    if len(aligned) < 5:
        return None
    corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
    return corr if np.isfinite(corr) else None


# ---------------------------------------------------------------------------
# Full metrics bundle
# ---------------------------------------------------------------------------


def compute_metrics(
    strategy_nav: pd.Series,
    benchmark_navs: Optional[dict[str, pd.Series]] = None,
    risk_free_nav: Optional[pd.Series] = None,
    trade_count: int = 0,
    turnover: Optional[float] = None,
) -> dict:
    """Compute all required metrics and return as a dict (for metrics.json)."""
    metrics: dict = {}

    cagr = calculate_cagr(strategy_nav)
    vol = calculate_annualized_vol(strategy_nav)
    sharpe = calculate_sharpe(strategy_nav, risk_free_nav)
    sortino = calculate_sortino(strategy_nav, risk_free_nav)
    calmar = calculate_calmar(strategy_nav)
    mdd = calculate_max_drawdown(strategy_nav)

    metrics["cagr"] = cagr
    metrics["annualized_volatility"] = vol
    metrics["sharpe_ratio"] = sharpe
    metrics["sortino_ratio"] = sortino
    metrics["calmar_ratio"] = calmar
    metrics["max_drawdown"] = mdd
    metrics["num_trades"] = trade_count
    metrics["turnover"] = turnover

    if benchmark_navs:
        for name, bench_nav in benchmark_navs.items():
            corr = calculate_correlation(strategy_nav, bench_nav)
            metrics[f"correlation_to_{name.lower().replace('/', '_')}"] = corr

    return metrics
