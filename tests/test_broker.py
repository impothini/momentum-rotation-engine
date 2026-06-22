"""Tests for the adversarial simulated broker.

Covers:
- Clean fills
- Partial fills
- Rejected orders
- Gap-down opens
- Missing broker positions
- Unauthorized position changes
"""

from __future__ import annotations

from datetime import date

import pytest

from momentum_agent.broker.base import Order
from momentum_agent.broker.simulated import (
    BrokerScenario,
    ScenarioSchedule,
    ScenarioType,
    SimulatedBroker,
)


def make_buy_order(ticker: str = "QQQ", quantity: float = 10.0) -> Order:
    return Order(
        ticker=ticker,
        quantity=quantity,
        order_type="market",
        order_date=date(2024, 1, 31),
    )


class TestCleanFill:
    def test_clean_fill_at_open_price(self):
        broker = SimulatedBroker(initial_cash=10_000.0)
        order = make_buy_order("QQQ", 5.0)
        fill = broker.submit_order(order, open_price=300.0)
        assert fill.is_rejected is False
        assert fill.is_partial is False
        assert abs(fill.filled_quantity - 5.0) < 1e-9
        assert abs(fill.fill_price - 300.0) < 1e-9

    def test_clean_fill_updates_position(self):
        broker = SimulatedBroker(initial_cash=10_000.0)
        order = make_buy_order("QQQ", 5.0)
        broker.submit_order(order, open_price=100.0)
        positions = broker.get_positions()
        assert "QQQ" in positions
        assert abs(positions["QQQ"] - 5.0) < 1e-9

    def test_clean_fill_deducts_cash(self):
        broker = SimulatedBroker(initial_cash=10_000.0)
        order = make_buy_order("QQQ", 5.0)
        broker.submit_order(order, open_price=100.0)
        assert abs(broker.get_cash() - 9_500.0) < 1e-9

    def test_sell_reduces_position(self):
        broker = SimulatedBroker(initial_cash=5_000.0)
        buy = Order(
            ticker="IWM", quantity=10.0, order_type="market",
            order_date=date(2024, 1, 31)
        )
        broker.submit_order(buy, open_price=200.0)
        sell = Order(
            ticker="IWM", quantity=-5.0, order_type="market",
            order_date=date(2024, 1, 31)
        )
        broker.submit_order(sell, open_price=200.0)
        positions = broker.get_positions()
        assert abs(positions.get("IWM", 0.0) - 5.0) < 1e-9


class TestPartialFill:
    def test_partial_fill_ratio(self):
        schedule = ScenarioSchedule()
        schedule.add(
            date(2024, 1, 31),
            "QQQ",
            BrokerScenario(
                scenario_type=ScenarioType.PARTIAL_FILL,
                partial_fill_ratio=0.5,
            ),
        )
        broker = SimulatedBroker(initial_cash=10_000.0, schedule=schedule)
        order = make_buy_order("QQQ", 10.0)
        fill = broker.submit_order(order, open_price=100.0)
        assert fill.is_partial is True
        assert abs(fill.filled_quantity - 5.0) < 1e-9

    def test_partial_fill_marked_correctly(self):
        schedule = ScenarioSchedule()
        schedule.add(
            date(2024, 1, 31),
            "QQQ",
            BrokerScenario(
                scenario_type=ScenarioType.PARTIAL_FILL,
                partial_fill_ratio=0.3,
            ),
        )
        broker = SimulatedBroker(initial_cash=50_000.0, schedule=schedule)
        order = make_buy_order("QQQ", 100.0)
        fill = broker.submit_order(order, open_price=100.0)
        assert fill.is_partial is True
        assert not fill.is_rejected


class TestRejectedOrder:
    def test_rejected_order(self):
        schedule = ScenarioSchedule()
        schedule.add(
            date(2024, 1, 31),
            "QQQ",
            BrokerScenario(
                scenario_type=ScenarioType.REJECTED,
                rejection_reason="insufficient_funds",
            ),
        )
        broker = SimulatedBroker(initial_cash=10_000.0, schedule=schedule)
        order = make_buy_order("QQQ", 10.0)
        fill = broker.submit_order(order, open_price=100.0)
        assert fill.is_rejected is True
        assert fill.rejection_reason == "insufficient_funds"
        assert fill.filled_quantity == 0.0

    def test_rejected_order_does_not_change_position(self):
        schedule = ScenarioSchedule()
        schedule.add(
            date(2024, 1, 31),
            "QQQ",
            BrokerScenario(scenario_type=ScenarioType.REJECTED),
        )
        broker = SimulatedBroker(initial_cash=10_000.0, schedule=schedule)
        order = make_buy_order("QQQ", 10.0)
        broker.submit_order(order, open_price=100.0)
        assert "QQQ" not in broker.get_positions()

    def test_rejected_order_does_not_change_cash(self):
        schedule = ScenarioSchedule()
        schedule.add(
            date(2024, 1, 31),
            "*",
            BrokerScenario(scenario_type=ScenarioType.REJECTED),
        )
        broker = SimulatedBroker(initial_cash=5_000.0, schedule=schedule)
        order = make_buy_order("QQQ", 5.0)
        broker.submit_order(order, open_price=100.0)
        assert abs(broker.get_cash() - 5_000.0) < 1e-9


class TestGapDown:
    def test_gap_down_fill_price(self):
        schedule = ScenarioSchedule()
        schedule.add(
            date(2024, 1, 31),
            "QQQ",
            BrokerScenario(
                scenario_type=ScenarioType.GAP_DOWN,
                gap_down_pct=0.05,
            ),
        )
        broker = SimulatedBroker(initial_cash=20_000.0, schedule=schedule)
        order = make_buy_order("QQQ", 10.0)
        fill = broker.submit_order(order, open_price=100.0)
        # fill_price = 100 * (1 - 0.05) = 95
        assert abs(fill.fill_price - 95.0) < 1e-9
        assert not fill.is_rejected
        assert not fill.is_partial


class TestUnauthorizedPositionChange:
    def test_force_position_changes_shares(self):
        broker = SimulatedBroker(initial_cash=10_000.0)
        # First, establish a position
        order = make_buy_order("QQQ", 5.0)
        broker.submit_order(order, open_price=100.0)
        # Simulate external actor changing the position
        broker.force_position("QQQ", 3.0)
        assert abs(broker.get_positions()["QQQ"] - 3.0) < 1e-9

    def test_unauthorized_change_scenario(self):
        schedule = ScenarioSchedule()
        schedule.add(
            date(2024, 1, 31),
            "IWM",
            BrokerScenario(
                scenario_type=ScenarioType.UNAUTHORIZED_CHANGE,
                unauthorized_ticker="QQQ",
                unauthorized_shares=99.0,
            ),
        )
        broker = SimulatedBroker(initial_cash=50_000.0, schedule=schedule)
        # Establish a QQQ position first
        setup = Order(
            ticker="QQQ", quantity=10.0, order_type="market",
            order_date=date(2024, 1, 30)
        )
        broker.submit_order(setup, open_price=100.0)
        # Now trigger the unauthorized change via an IWM order
        order = Order(
            ticker="IWM", quantity=5.0, order_type="market",
            order_date=date(2024, 1, 31)
        )
        broker.submit_order(order, open_price=200.0)
        # QQQ should now be at 99.0 (unauthorized change)
        assert abs(broker.get_positions().get("QQQ", 0.0) - 99.0) < 1e-9
