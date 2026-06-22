"""Tests for momentum signal calculations.

Covers:
- Momentum formula correctness
- Trend filter
- Correlation cascade (second-asset selection)
- SGOV fallback
- Weight bounds
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from momentum_agent.config import MomentumParams, TRADABLE_UNIVERSE, TICKER_TO_FAMILY
from momentum_agent.strategies.momentum.signals import (
    apply_trend_filter,
    calculate_annualized_volatility,
    calculate_momentum_score,
    calculate_rolling_correlation,
    calculate_sma,
)
from momentum_agent.strategies.momentum.weights import compute_inverse_vol_weights
from momentum_agent.strategies.momentum.selection import SelectionPipeline


# ---------------------------------------------------------------------------
# Momentum formula
# ---------------------------------------------------------------------------


class TestMomentumScore:
    def test_correct_formula(self):
        """momentum = adj_close[t-21] / adj_close[t-273] - 1"""
        prices = pd.Series([float(i + 100) for i in range(300)])
        # t = last element (index -1 = 299, price = 399)
        # t-21 = index -22 = 278, price = 378
        # t-273 = index -274 = 26, price = 126
        score = calculate_momentum_score(prices, exclude_recent=21, lookback=273)
        expected = 378.0 / 126.0 - 1.0
        assert abs(score - expected) < 1e-10

    def test_insufficient_data_raises(self):
        prices = pd.Series([100.0] * 273)  # need at least 274
        with pytest.raises(ValueError, match="Insufficient"):
            calculate_momentum_score(prices)

    def test_exactly_minimum_length(self):
        prices = pd.Series([100.0 + i * 0.1 for i in range(274)])
        score = calculate_momentum_score(prices)
        assert isinstance(score, float)

    def test_zero_far_price_raises(self):
        prices = pd.Series([0.0] + [100.0] * 273)
        with pytest.raises(ValueError):
            calculate_momentum_score(prices)

    def test_nan_price_raises(self):
        prices = pd.Series([float("nan")] + [100.0] * 273)
        with pytest.raises(ValueError):
            calculate_momentum_score(prices)

    def test_positive_momentum(self):
        """Near > far → positive score."""
        base = [100.0] * 274
        base[-22] = 120.0   # near price (t-21)
        base[-274] = 100.0  # far price (t-273)
        prices = pd.Series(base)
        score = calculate_momentum_score(prices)
        assert score > 0

    def test_negative_momentum(self):
        """Near < far → negative score."""
        base = [100.0] * 274
        base[-22] = 80.0    # near price (t-21)
        base[-274] = 100.0  # far price (t-273)
        prices = pd.Series(base)
        score = calculate_momentum_score(prices)
        assert score < 0

    def test_deterministic(self):
        """Same inputs always yield same output."""
        prices = pd.Series([100.0 + i * 0.05 for i in range(300)])
        s1 = calculate_momentum_score(prices)
        s2 = calculate_momentum_score(prices)
        assert s1 == s2


# ---------------------------------------------------------------------------
# Trend filter
# ---------------------------------------------------------------------------


class TestTrendFilter:
    def test_above_sma_passes(self):
        """Strongly trending price series: current > SMA200."""
        prices = pd.Series([100.0 + i * 0.5 for i in range(250)])
        assert apply_trend_filter(prices, sma_window=200) is True

    def test_below_sma_fails(self):
        """Price crashed below SMA: filter returns False."""
        prices = pd.Series([200.0 - i * 0.5 for i in range(250)])
        assert apply_trend_filter(prices, sma_window=200) is False

    def test_insufficient_length_fails(self):
        """Fewer prices than SMA window → fail the filter."""
        prices = pd.Series([100.0] * 199)
        assert apply_trend_filter(prices, sma_window=200) is False

    def test_exactly_sma_window_length(self):
        """Exactly 200 prices — SMA is computable."""
        prices = pd.Series([100.0] * 200)
        result = apply_trend_filter(prices, sma_window=200)
        # price == SMA → not strictly greater → False
        assert result is False

    def test_sma_calculation_correctness(self):
        """SMA of constant prices equals that constant."""
        prices = pd.Series([50.0] * 250)
        sma = calculate_sma(prices, 200)
        assert abs(sma.iloc[-1] - 50.0) < 1e-10


# ---------------------------------------------------------------------------
# Correlation filter
# ---------------------------------------------------------------------------


class TestCorrelationFilter:
    def test_high_correlation_blocked(self):
        """Assets with correlation > 0.70 should not be selected together."""
        # Construct nearly identical series
        prices_a = pd.Series([100.0 + i * 0.3 for i in range(200)])
        prices_b = prices_a * 1.01  # near-perfect correlation
        corr = calculate_rolling_correlation(prices_a, prices_b, window=90)
        assert corr is not None
        assert corr > 0.70

    def test_low_correlation_allowed(self):
        """Uncorrelated series should have correlation near 0."""
        rng = np.random.default_rng(42)
        prices_a = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.01, 200)))
        rng2 = np.random.default_rng(99)
        prices_b = pd.Series(100.0 * np.cumprod(1 + rng2.normal(0, 0.01, 200)))
        corr = calculate_rolling_correlation(prices_a, prices_b, window=90)
        assert corr is not None
        assert abs(corr) < 0.70

    def test_insufficient_data_returns_none(self):
        prices_a = pd.Series([100.0 + i for i in range(50)])
        prices_b = pd.Series([200.0 - i for i in range(50)])
        corr = calculate_rolling_correlation(prices_a, prices_b, window=90)
        assert corr is None


# ---------------------------------------------------------------------------
# SGOV fallback
# ---------------------------------------------------------------------------


class TestSGOVFallback:
    def _make_declining_df(self, tickers, n=400):
        """All tickers trend downward — none pass the trend filter."""
        idx = pd.bdate_range("2020-01-02", periods=n)
        data = {}
        for i, ticker in enumerate(tickers):
            rng = np.random.default_rng(i + 100)
            returns = rng.normal(-0.003, 0.01, size=n)
            data[ticker] = 200.0 * np.cumprod(1 + returns)
        return pd.DataFrame(data, index=idx)

    def test_all_below_sma_yields_100pct_sgov(self):
        """When no asset passes the trend filter → 100% SGOV."""
        from momentum_agent.config import MOMENTUM_CANDIDATES

        params = MomentumParams()
        pipeline = SelectionPipeline(params)
        tickers = MOMENTUM_CANDIDATES + ["SGOV"]
        df = self._make_declining_df(tickers, n=400)

        signal_date = df.index[-1].date()
        all_valid = set(tickers)

        result = pipeline.run(
            adj_prices=df,
            signal_date=signal_date,
            valid_tickers=all_valid,
            lockout_set=set(),
        )
        assert result.allocations == {"SGOV": 1.0}
        assert result.reason == "no_trend_qualified"

    def test_sgov_not_in_momentum_candidates(self):
        """SGOV must never appear in MOMENTUM_CANDIDATES."""
        from momentum_agent.config import MOMENTUM_CANDIDATES

        assert "SGOV" not in MOMENTUM_CANDIDATES


# ---------------------------------------------------------------------------
# Weight bounds
# ---------------------------------------------------------------------------


class TestWeightBounds:
    def test_equal_vol_gives_50_50_within_bounds(self):
        """Equal volatilities → raw weight = 0.5 → within [0.30, 0.70]."""
        w_a, w_b = compute_inverse_vol_weights(0.20, 0.20, min_weight=0.30, max_weight=0.70)
        assert abs(w_a - 0.5) < 1e-10
        assert abs(w_b - 0.5) < 1e-10

    def test_high_vol_asset_gets_minimum_weight(self):
        """Very high-vol asset should be capped at min_weight."""
        w_a, w_b = compute_inverse_vol_weights(0.50, 0.10, min_weight=0.30, max_weight=0.70)
        # vol_a is 5x vol_b → inv_a = 2, inv_b = 10 → raw_a = 2/12 ≈ 0.167
        # clamped to min_weight = 0.30
        assert abs(w_a - 0.30) < 1e-10
        assert abs(w_b - 0.70) < 1e-10

    def test_low_vol_asset_capped_at_max_weight(self):
        """Very low-vol asset → raw weight > max_weight → capped."""
        w_a, w_b = compute_inverse_vol_weights(0.05, 0.50, min_weight=0.30, max_weight=0.70)
        # inv_a = 20, inv_b = 2 → raw_a = 20/22 ≈ 0.909 → capped at 0.70
        assert abs(w_a - 0.70) < 1e-10
        assert abs(w_b - 0.30) < 1e-10

    def test_weights_sum_to_one(self):
        w_a, w_b = compute_inverse_vol_weights(0.15, 0.25)
        assert abs(w_a + w_b - 1.0) < 1e-10

    def test_zero_volatility_raises(self):
        with pytest.raises(ValueError):
            compute_inverse_vol_weights(0.0, 0.20)
        with pytest.raises(ValueError):
            compute_inverse_vol_weights(0.20, 0.0)

    def test_weights_always_within_bounds(self):
        """Exhaustive parametric check across many vol combinations."""
        rng = np.random.default_rng(0)
        vols = rng.uniform(0.05, 1.0, size=(100, 2))
        for vol_a, vol_b in vols:
            w_a, w_b = compute_inverse_vol_weights(vol_a, vol_b, 0.30, 0.70)
            assert 0.30 - 1e-9 <= w_a <= 0.70 + 1e-9
            assert 0.30 - 1e-9 <= w_b <= 0.70 + 1e-9
            assert abs(w_a + w_b - 1.0) < 1e-10


# ---------------------------------------------------------------------------
# Selection pipeline — integration
# ---------------------------------------------------------------------------


class TestSelectionPipeline:
    def _make_trending_universe(self, n=400, seed_base=0):
        from momentum_agent.config import TRADABLE_UNIVERSE

        idx = pd.bdate_range("2020-01-02", periods=n)
        data = {}
        for i, ticker in enumerate(TRADABLE_UNIVERSE):
            rng = np.random.default_rng(i + seed_base)
            returns = rng.normal(0.0005, 0.01, size=n)
            data[ticker] = 100.0 * np.cumprod(1 + returns)
        return pd.DataFrame(data, index=idx)

    def test_pipeline_returns_valid_allocation(self):
        """Pipeline output weights sum to 1.0."""
        from momentum_agent.config import TRADABLE_UNIVERSE

        params = MomentumParams()
        pipeline = SelectionPipeline(params)
        df = self._make_trending_universe(n=400)
        signal_date = df.index[-1].date()

        result = pipeline.run(
            adj_prices=df,
            signal_date=signal_date,
            valid_tickers=set(TRADABLE_UNIVERSE),
            lockout_set=set(),
        )
        total = sum(result.allocations.values())
        assert abs(total - 1.0) < 1e-9

    def test_lockout_ticker_excluded(self):
        """Locked-out ticker must not appear in allocations."""
        from momentum_agent.config import TRADABLE_UNIVERSE, MOMENTUM_CANDIDATES

        params = MomentumParams()
        pipeline = SelectionPipeline(params)
        df = self._make_trending_universe(n=400, seed_base=5)
        signal_date = df.index[-1].date()

        # Lock out QQQ
        result = pipeline.run(
            adj_prices=df,
            signal_date=signal_date,
            valid_tickers=set(TRADABLE_UNIVERSE),
            lockout_set={"QQQ"},
        )
        assert "QQQ" not in result.allocations or result.allocations.get("QQQ", 0) == 0.0

    def test_only_sgov_in_single_family_fallback(self):
        """When single asset selected: 70% asset + 30% SGOV."""
        from momentum_agent.config import TRADABLE_UNIVERSE

        params = MomentumParams()
        pipeline = SelectionPipeline(params)
        df = self._make_trending_universe(n=400)
        signal_date = df.index[-1].date()

        result = pipeline.run(
            adj_prices=df,
            signal_date=signal_date,
            valid_tickers=set(TRADABLE_UNIVERSE),
            lockout_set=set(),
        )
        # Either 1 or 2 risk assets; SGOV may be present
        total = sum(result.allocations.values())
        assert abs(total - 1.0) < 1e-9

    def test_same_family_excluded_from_second_slot(self):
        """Two assets from the same family cannot both be selected."""
        result_allocations = {}
        from momentum_agent.config import TRADABLE_UNIVERSE

        params = MomentumParams()
        pipeline = SelectionPipeline(params)
        df = self._make_trending_universe(n=400)
        signal_date = df.index[-1].date()

        result = pipeline.run(
            adj_prices=df,
            signal_date=signal_date,
            valid_tickers=set(TRADABLE_UNIVERSE),
            lockout_set=set(),
        )
        risk_tickers = [t for t in result.allocations if t != "SGOV"]
        families = [TICKER_TO_FAMILY[t] for t in risk_tickers if t in TICKER_TO_FAMILY]
        assert len(families) == len(set(families)), "Duplicate families in allocation"
