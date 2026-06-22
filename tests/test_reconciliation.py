"""Tests for reconciliation and unauthorized position change detection.

Covers:
- Reconciliation pass within tolerance
- Reconciliation failure outside tolerance
- Cash tolerance
- Unauthorized position change detection
- Proxy requirements
"""

from __future__ import annotations

from datetime import date

import pytest

from momentum_agent.config import ReconciliationParams
from momentum_agent.data.proxy import ASSET_INCEPTION_DATES, ProxyConfig
from momentum_agent.events import EventType
from momentum_agent.portfolio.reconciliation import Reconciler


# ---------------------------------------------------------------------------
# Reconciliation tolerance
# ---------------------------------------------------------------------------


class TestReconciliationTolerance:
    def _make_reconciler(self) -> Reconciler:
        return Reconciler(
            ReconciliationParams(
                share_tolerance=0.0001,
                weight_tolerance=0.005,
                cash_tolerance=1.00,
            )
        )

    def test_exact_match_passes(self):
        r = self._make_reconciler()
        result = r.reconcile(
            target_shares={"QQQ": 10.0, "SGOV": 5.0},
            actual_shares={"QQQ": 10.0, "SGOV": 5.0},
            target_cash=500.0,
            actual_cash=500.0,
            nav=10_000.0,
            recon_date=date(2024, 2, 1),
        )
        assert result.passed is True

    def test_within_share_tolerance_passes(self):
        r = self._make_reconciler()
        result = r.reconcile(
            target_shares={"QQQ": 10.0},
            actual_shares={"QQQ": 10.00005},  # diff = 0.00005 < 0.0001
            target_cash=0.0,
            actual_cash=0.0,
            nav=10_000.0,
            recon_date=date(2024, 2, 1),
        )
        assert result.passed is True

    def test_outside_share_tolerance_fails(self):
        r = self._make_reconciler()
        result = r.reconcile(
            target_shares={"QQQ": 10.0},
            actual_shares={"QQQ": 10.5},  # diff = 0.5 >> 0.0001
            target_cash=0.0,
            actual_cash=0.0,
            nav=10_000.0,
            recon_date=date(2024, 2, 1),
        )
        assert result.passed is False
        assert any(
            e.event_type == EventType.RECONCILIATION_FAILURE
            for e in result.events
        )

    def test_cash_within_tolerance_passes(self):
        r = self._make_reconciler()
        result = r.reconcile(
            target_shares={},
            actual_shares={},
            target_cash=1000.0,
            actual_cash=1000.50,  # diff = 0.50 < 1.00
            nav=1000.0,
            recon_date=date(2024, 2, 1),
        )
        assert result.passed is True

    def test_cash_outside_tolerance_fails(self):
        r = self._make_reconciler()
        result = r.reconcile(
            target_shares={},
            actual_shares={},
            target_cash=1000.0,
            actual_cash=1002.5,  # diff = 2.5 > 1.00
            nav=1000.0,
            recon_date=date(2024, 2, 1),
        )
        assert result.passed is False

    def test_reconciliation_failure_event_emitted(self):
        r = self._make_reconciler()
        result = r.reconcile(
            target_shares={"IWM": 20.0},
            actual_shares={"IWM": 15.0},  # large discrepancy
            target_cash=0.0,
            actual_cash=0.0,
            nav=5000.0,
            recon_date=date(2024, 2, 1),
        )
        assert len(result.events) > 0
        assert result.events[0].event_type == EventType.RECONCILIATION_FAILURE


# ---------------------------------------------------------------------------
# Unauthorized position change detection
# ---------------------------------------------------------------------------


class TestUnauthorizedPositionChangeDetection:
    def _make_reconciler(self) -> Reconciler:
        return Reconciler(ReconciliationParams())

    def test_detects_external_position_change(self):
        r = self._make_reconciler()
        events = r.detect_unauthorized_changes(
            prev_known_shares={"QQQ": 10.0},
            current_broker_shares={"QQQ": 5.0},  # changed externally
            authorized_tickers=set(),
            check_date=date(2024, 2, 1),
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.UNAUTHORIZED_POSITION_CHANGE
        assert events[0].ticker == "QQQ"

    def test_authorized_change_not_flagged(self):
        r = self._make_reconciler()
        events = r.detect_unauthorized_changes(
            prev_known_shares={"QQQ": 10.0},
            current_broker_shares={"QQQ": 15.0},
            authorized_tickers={"QQQ"},  # engine traded this
            check_date=date(2024, 2, 1),
        )
        assert len(events) == 0

    def test_new_position_without_engine_order_flagged(self):
        r = self._make_reconciler()
        events = r.detect_unauthorized_changes(
            prev_known_shares={},               # engine knew of nothing
            current_broker_shares={"SPY": 50.0},  # mysterious new position
            authorized_tickers=set(),
            check_date=date(2024, 2, 1),
        )
        assert len(events) == 1
        assert events[0].ticker == "SPY"

    def test_within_share_tolerance_not_flagged(self):
        r = self._make_reconciler()
        # Difference = 0.00005 < share_tolerance (0.0001)
        events = r.detect_unauthorized_changes(
            prev_known_shares={"SGOV": 100.0},
            current_broker_shares={"SGOV": 100.00005},
            authorized_tickers=set(),
            check_date=date(2024, 2, 1),
        )
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Proxy requirements
# ---------------------------------------------------------------------------


class TestProxyRequirements:
    def test_sgov_needs_proxy_before_inception(self):
        proxy_config = ProxyConfig(proxy_map={})
        missing = proxy_config.validate(["SGOV"], start_date=date(2020, 1, 1))
        assert "SGOV" in missing

    def test_sgov_ok_after_inception(self):
        proxy_config = ProxyConfig(proxy_map={})
        inception = ASSET_INCEPTION_DATES["SGOV"]
        missing = proxy_config.validate(["SGOV"], start_date=inception)
        assert "SGOV" not in missing

    def test_default_proxy_map_covers_sgov(self):
        proxy_config = ProxyConfig.default()
        missing = proxy_config.validate(["SGOV"], start_date=date(2020, 1, 1))
        assert "SGOV" not in missing  # has BIL as proxy

    def test_qqq_no_proxy_needed_after_1999(self):
        proxy_config = ProxyConfig(proxy_map={})
        missing = proxy_config.validate(["QQQ"], start_date=date(2000, 1, 1))
        assert "QQQ" not in missing

    def test_missing_proxy_list_populated_correctly(self):
        proxy_config = ProxyConfig(proxy_map={})
        missing = proxy_config.validate(
            ["SGOV", "SCHD", "QQQ"], start_date=date(2010, 1, 1)
        )
        # QQQ inception 1999 → fine
        assert "QQQ" not in missing
        # SGOV inception 2023 → needs proxy
        assert "SGOV" in missing
        # SCHD inception 2011-10-20, backtest starts 2010-01-01 → needs proxy
        assert "SCHD" in missing
