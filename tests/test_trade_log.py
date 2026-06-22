"""Tests for trade log schema and daily NAV output.

Covers:
- Trade log has all required columns
- Events are recorded correctly
- Daily NAV schema
"""

from __future__ import annotations

from datetime import date

import pytest

from momentum_agent.events import Event, EventType
from momentum_agent.reporting.daily_nav import DailyNavRow, DailyNavWriter, DAILY_NAV_COLUMNS
from momentum_agent.reporting.trade_log import TRADE_LOG_COLUMNS, TradeLogWriter


# ---------------------------------------------------------------------------
# Trade log schema
# ---------------------------------------------------------------------------


class TestTradeLogSchema:
    def test_empty_log_has_all_columns(self):
        writer = TradeLogWriter()
        df = writer.to_dataframe()
        for col in TRADE_LOG_COLUMNS:
            assert col in df.columns, f"Missing column: {col}"

    def test_schema_validation_passes_on_empty(self):
        writer = TradeLogWriter()
        assert writer.validate_schema() is True

    def test_required_columns_present(self):
        required = [
            "date", "event_type", "strategy_version", "ticker", "asset_family",
            "signal_price", "execution_open_price", "fill_price",
            "entry_vwap_fill_price", "target_weight", "target_shares",
            "actual_shares", "order_quantity", "filled_quantity", "cash",
            "reconciliation_status", "data_status", "proxy_used", "reason", "notes",
        ]
        for col in required:
            assert col in TRADE_LOG_COLUMNS

    def test_event_recorded_correctly(self):
        writer = TradeLogWriter()
        event = Event(
            date=date(2024, 1, 31),
            event_type=EventType.REBALANCE,
            ticker="QQQ",
            asset_family="GROWTH",
            target_weight=0.70,
            target_shares=23.456789,
            fill_price=300.0,
            cash=5000.0,
        )
        writer.record(event)
        df = writer.to_dataframe()
        assert len(df) == 1
        assert df["ticker"].iloc[0] == "QQQ"
        assert df["event_type"].iloc[0] == "REBALANCE"
        assert abs(float(df["target_weight"].iloc[0]) - 0.70) < 1e-9

    def test_multiple_events_recorded(self):
        writer = TradeLogWriter()
        for event_type in [
            EventType.REBALANCE,
            EventType.STOP_TRIGGER,
            EventType.KILL_SWITCH,
        ]:
            writer.record(
                Event(date=date(2024, 2, 1), event_type=event_type)
            )
        df = writer.to_dataframe()
        assert len(df) == 3

    def test_proxy_used_column_present(self):
        writer = TradeLogWriter()
        event = Event(
            date=date(2024, 1, 31),
            event_type=EventType.REBALANCE,
            proxy_used=True,
        )
        writer.record(event)
        df = writer.to_dataframe()
        assert "proxy_used" in df.columns
        assert bool(df["proxy_used"].iloc[0]) is True

    def test_all_event_types_representable(self):
        writer = TradeLogWriter()
        for event_type in EventType:
            writer.record(
                Event(date=date(2024, 1, 1), event_type=event_type)
            )
        df = writer.to_dataframe()
        assert len(df) == len(EventType)


# ---------------------------------------------------------------------------
# Daily NAV schema
# ---------------------------------------------------------------------------


class TestDailyNavSchema:
    def test_empty_nav_has_all_columns(self):
        writer = DailyNavWriter()
        df = writer.to_dataframe()
        for col in DAILY_NAV_COLUMNS:
            assert col in df.columns, f"Missing column: {col}"

    def test_nav_row_recorded(self):
        writer = DailyNavWriter()
        row = DailyNavRow(
            date=date(2024, 2, 1),
            nav=100_500.0,
            cash=2_000.0,
            positions_value=98_500.0,
            high_water_mark=101_000.0,
            drawdown_pct=-0.005,
            strategy_state="NORMAL",
            data_status="ok",
            proxy_used=False,
        )
        writer.record(row)
        df = writer.to_dataframe()
        assert len(df) == 1
        assert abs(float(df["nav"].iloc[0]) - 100_500.0) < 1e-3

    def test_drawdown_pct_column_present(self):
        writer = DailyNavWriter()
        row = DailyNavRow(
            date=date(2024, 2, 1),
            nav=95.0,
            cash=0.0,
            positions_value=95.0,
            high_water_mark=100.0,
            drawdown_pct=-0.05,
            strategy_state="NORMAL",
            data_status="ok",
            proxy_used=False,
        )
        writer.record(row)
        df = writer.to_dataframe()
        assert "drawdown_pct" in df.columns
        assert abs(float(df["drawdown_pct"].iloc[0]) - (-0.05)) < 1e-9

    def test_nav_series_drops_none(self):
        writer = DailyNavWriter()
        writer.record(DailyNavRow(
            date=date(2024, 1, 1), nav=None, cash=0.0, positions_value=0.0,
            high_water_mark=None, drawdown_pct=None, strategy_state="DATA_INTEGRITY",
            data_status="invalid", proxy_used=False
        ))
        writer.record(DailyNavRow(
            date=date(2024, 1, 2), nav=100.0, cash=0.0, positions_value=100.0,
            high_water_mark=100.0, drawdown_pct=0.0, strategy_state="NORMAL",
            data_status="ok", proxy_used=False
        ))
        nav_series = writer.get_nav_series()
        assert len(nav_series) == 1
        assert abs(nav_series.iloc[0] - 100.0) < 1e-9
