"""Tests for risk controls.

Covers:
- Daily stop trigger and lockout
- Stop / rebalance collision
- Kill switch trigger
- HWM update and reset
- Data integrity failures
- Failure counter reset on valid bar
- Fail-safe liquidation
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from momentum_agent.config import RiskParams
from momentum_agent.portfolio.state import PortfolioState, PositionState
from momentum_agent.risk.kill_switch import KillSwitchEvaluator
from momentum_agent.risk.state_machine import RiskStateMachine, StrategyState
from momentum_agent.risk.stops import StopLossEvaluator


# ---------------------------------------------------------------------------
# Daily stop
# ---------------------------------------------------------------------------


class TestDailyStop:
    def _make_position(
        self,
        ticker: str = "QQQ",
        shares: float = 10.0,
        entry_vwap: float = 100.0,
    ) -> PositionState:
        return PositionState(
            ticker=ticker,
            shares=shares,
            entry_vwap_fill_price=entry_vwap,
            entry_timestamp=date(2024, 1, 2),
            entry_rebalance_id="reb_2024-01-31",
            asset_family="GROWTH",
        )

    def test_stop_triggers_at_threshold(self):
        """raw_close == entry * 0.90 → triggered."""
        params = RiskParams(stop_loss_pct=0.90)
        evaluator = StopLossEvaluator(params)
        pos = self._make_position(entry_vwap=100.0)
        result = evaluator.check_position(pos, raw_close=90.0)
        assert result.triggered is True

    def test_stop_triggers_below_threshold(self):
        params = RiskParams(stop_loss_pct=0.90)
        evaluator = StopLossEvaluator(params)
        pos = self._make_position(entry_vwap=100.0)
        result = evaluator.check_position(pos, raw_close=85.0)
        assert result.triggered is True

    def test_stop_not_triggered_above_threshold(self):
        params = RiskParams(stop_loss_pct=0.90)
        evaluator = StopLossEvaluator(params)
        pos = self._make_position(entry_vwap=100.0)
        result = evaluator.check_position(pos, raw_close=95.0)
        assert result.triggered is False

    def test_sgov_exempt_from_stop(self):
        """SGOV must never trigger a stop regardless of price."""
        params = RiskParams(stop_loss_pct=0.90)
        evaluator = StopLossEvaluator(params)
        pos = PositionState(
            ticker="SGOV",
            shares=1000.0,
            entry_vwap_fill_price=100.0,
            entry_timestamp=date(2024, 1, 2),
            entry_rebalance_id="reb",
            asset_family="CASH",
        )
        # Even if price dropped drastically
        result = evaluator.check_position(pos, raw_close=50.0)
        assert result.triggered is False
        assert result.reason == "sgov_exempt"

    def test_missing_price_does_not_trigger(self):
        """Missing raw_close → stop does not trigger (no data → freeze)."""
        params = RiskParams()
        evaluator = StopLossEvaluator(params)
        pos = self._make_position(entry_vwap=100.0)
        result = evaluator.check_position(pos, raw_close=None)
        assert result.triggered is False

    def test_stop_adds_to_lockout(self):
        """After stop trigger, ticker is in lockout_set."""
        portfolio = PortfolioState()
        pos = self._make_position(ticker="QQQ", entry_vwap=100.0)
        portfolio.positions["QQQ"] = pos

        params = RiskParams(stop_loss_pct=0.90)
        evaluator = StopLossEvaluator(params)
        results = evaluator.check_all_positions(
            portfolio.positions, {"QQQ": 89.0}
        )
        triggered = evaluator.get_triggered(results)
        for sr in triggered:
            portfolio.lockout_set.add(sr.ticker)

        assert "QQQ" in portfolio.lockout_set


# ---------------------------------------------------------------------------
# Stop / rebalance collision
# ---------------------------------------------------------------------------


class TestStopRebalanceCollision:
    def test_stopped_ticker_excluded_from_rebalance(self):
        """On collision day, stopped ticker goes into lockout before selection."""
        from momentum_agent.config import TRADABLE_UNIVERSE, MomentumParams
        from momentum_agent.strategies.momentum.selection import SelectionPipeline
        import pandas as pd
        import numpy as np

        # Build price DataFrame — QQQ trending up, rest trending up too
        n = 400
        idx = pd.bdate_range("2020-01-02", periods=n)
        data = {}
        for i, ticker in enumerate(TRADABLE_UNIVERSE):
            rng = np.random.default_rng(i)
            returns = rng.normal(0.0005, 0.01, size=n)
            data[ticker] = 100.0 * np.cumprod(1 + returns)
        df = pd.DataFrame(data, index=idx)

        params = MomentumParams()
        pipeline = SelectionPipeline(params)
        signal_date = df.index[-1].date()

        # QQQ triggered stop → in lockout
        result = pipeline.run(
            adj_prices=df,
            signal_date=signal_date,
            valid_tickers=set(TRADABLE_UNIVERSE),
            lockout_set={"QQQ"},
        )
        assert "QQQ" not in result.allocations or result.allocations.get("QQQ", 0) == 0.0


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_triggers_at_threshold(self):
        """NAV == HWM * 0.85 → kill switch fires."""
        params = RiskParams(kill_switch_pct=0.85)
        ks = KillSwitchEvaluator(params)
        assert ks.should_trigger(nav=85.0, high_water_mark=100.0) is True

    def test_triggers_below_threshold(self):
        params = RiskParams(kill_switch_pct=0.85)
        ks = KillSwitchEvaluator(params)
        assert ks.should_trigger(nav=80.0, high_water_mark=100.0) is True

    def test_not_triggered_above_threshold(self):
        params = RiskParams(kill_switch_pct=0.85)
        ks = KillSwitchEvaluator(params)
        assert ks.should_trigger(nav=90.0, high_water_mark=100.0) is False

    def test_nav_none_does_not_trigger(self):
        """NAV invalid → kill switch cannot be evaluated."""
        params = RiskParams()
        ks = KillSwitchEvaluator(params)
        assert ks.should_trigger(nav=None, high_water_mark=100.0) is False

    def test_hwm_none_does_not_trigger(self):
        params = RiskParams()
        ks = KillSwitchEvaluator(params)
        assert ks.should_trigger(nav=80.0, high_water_mark=None) is False

    def test_hwm_updates_on_new_high(self):
        params = RiskParams()
        ks = KillSwitchEvaluator(params)
        new_hwm = ks.update_hwm(nav=110.0, current_hwm=100.0)
        assert new_hwm == 110.0

    def test_hwm_does_not_decrease(self):
        """HWM never goes down."""
        params = RiskParams()
        ks = KillSwitchEvaluator(params)
        new_hwm = ks.update_hwm(nav=90.0, current_hwm=100.0)
        assert new_hwm == 100.0

    def test_hwm_reset_on_resume(self):
        """After kill switch, HWM resets to current NAV."""
        params = RiskParams()
        ks = KillSwitchEvaluator(params)
        resume_hwm = ks.reset_hwm_on_resume(nav=95.0)
        assert resume_hwm == 95.0

    def test_state_machine_transitions(self):
        sm = RiskStateMachine()
        assert sm.state == StrategyState.NORMAL
        sm.set_kill_switch("test_trigger")
        assert sm.state == StrategyState.KILLED
        assert sm.is_killed
        sm.resume_from_kill_switch("monthly_rebalance_resume")
        assert sm.state == StrategyState.NORMAL


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


class TestDataIntegrity:
    def test_missing_price_emits_failure(self):
        """Missing price for held position sets DATA_INTEGRITY state."""
        sm = RiskStateMachine()
        sm.set_data_integrity_failure("held_position_missing_price")
        assert sm.is_data_integrity_failure

    def test_data_integrity_blocks_kill_switch_evaluation(self):
        sm = RiskStateMachine()
        sm.set_data_integrity_failure("test")
        assert sm.can_evaluate_kill_switch() is False

    def test_data_integrity_clears_on_valid_bar(self):
        sm = RiskStateMachine()
        sm.set_data_integrity_failure("test")
        sm.clear_data_integrity_failure()
        assert sm.state == StrategyState.NORMAL

    def test_failure_counter_resets_on_valid_bar(self):
        portfolio = PortfolioState()
        portfolio.record_failure("QQQ")
        portfolio.record_failure("QQQ")
        assert portfolio.get_failure_count("QQQ") == 2
        portfolio.record_valid_bar("QQQ")
        assert portfolio.get_failure_count("QQQ") == 0

    def test_failure_counter_increments_consecutively(self):
        portfolio = PortfolioState()
        for i in range(1, 6):
            count = portfolio.record_failure("IWM")
            assert count == i

    def test_failsafe_threshold(self):
        """After 5 consecutive failures, failsafe should trigger."""
        params = RiskParams(failure_count_threshold=5)
        portfolio = PortfolioState()
        for _ in range(5):
            count = portfolio.record_failure("IWM")
        assert count >= params.failure_count_threshold


# ---------------------------------------------------------------------------
# Fail-safe liquidation
# ---------------------------------------------------------------------------


class TestFailsafeLiquidation:
    def test_five_failures_reaches_threshold(self):
        params = RiskParams(failure_count_threshold=5)
        portfolio = PortfolioState()
        pos = PositionState(
            ticker="IWM",
            shares=10.0,
            entry_vwap_fill_price=100.0,
            entry_timestamp=date(2024, 1, 2),
            entry_rebalance_id="reb",
            asset_family="SMALL_CAP",
        )
        portfolio.positions["IWM"] = pos

        for _ in range(5):
            portfolio.record_failure("IWM")

        count = portfolio.get_failure_count("IWM")
        assert count >= params.failure_count_threshold

    def test_four_failures_does_not_reach_threshold(self):
        params = RiskParams(failure_count_threshold=5)
        portfolio = PortfolioState()
        for _ in range(4):
            portfolio.record_failure("TLT")
        assert portfolio.get_failure_count("TLT") < params.failure_count_threshold
