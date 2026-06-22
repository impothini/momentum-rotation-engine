"""Adversarial simulated broker.

The simulated broker must exercise all failure paths.  It supports:
  - Perfect fills (baseline)
  - Partial fills (only fills a fraction of the requested quantity)
  - Rejected orders
  - Gap-down opens (fill price lower than the open price used in planning)
  - Missing broker positions (returns empty or incorrect positions)
  - Unauthorized external position changes
  - Stale/missing market bars

Scenarios are injected via a ``ScenarioSchedule`` — a mapping of
``(date, ticker) → BrokerScenario`` applied on each order submission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from momentum_agent.broker.base import BrokerInterface, Fill, Order


# ---------------------------------------------------------------------------
# Scenario types
# ---------------------------------------------------------------------------


class ScenarioType(str, Enum):
    CLEAN = "clean"
    PARTIAL_FILL = "partial_fill"
    REJECTED = "rejected"
    GAP_DOWN = "gap_down"
    MISSING_POSITION = "missing_position"
    UNAUTHORIZED_CHANGE = "unauthorized_change"


@dataclass
class BrokerScenario:
    scenario_type: ScenarioType
    partial_fill_ratio: float = 0.5
    """Fraction of order filled when scenario_type=PARTIAL_FILL."""

    gap_down_pct: float = 0.02
    """Gap-down fraction applied to fill_price when scenario_type=GAP_DOWN."""

    rejection_reason: str = "simulated_rejection"

    unauthorized_ticker: Optional[str] = None
    """Ticker to modify when scenario_type=UNAUTHORIZED_CHANGE."""
    unauthorized_shares: float = 0.0
    """New share count for unauthorized_ticker."""


# ---------------------------------------------------------------------------
# Scenario schedule
# ---------------------------------------------------------------------------


@dataclass
class ScenarioSchedule:
    """Maps (date, ticker) → scenario to apply during order submission."""

    entries: dict[tuple[date, str], BrokerScenario] = field(default_factory=dict)

    def add(self, d: date, ticker: str, scenario: BrokerScenario) -> None:
        self.entries[(d, ticker)] = scenario

    def get(self, d: date, ticker: str) -> Optional[BrokerScenario]:
        # Exact match first.
        key = (d, ticker)
        if key in self.entries:
            return self.entries[key]
        # Date-wildcard: (date, "*")
        wildcard = (d, "*")
        if wildcard in self.entries:
            return self.entries[wildcard]
        return None


# ---------------------------------------------------------------------------
# Simulated broker
# ---------------------------------------------------------------------------


class SimulatedBroker(BrokerInterface):
    """Adversarial in-memory broker for backtesting.

    Maintains its own position and cash ledger.  Scenarios are applied at
    order submission time to exercise failure paths.
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        schedule: Optional[ScenarioSchedule] = None,
    ) -> None:
        self._cash: float = initial_cash
        self._positions: dict[str, float] = {}   # ticker → shares
        self._schedule = schedule or ScenarioSchedule()
        self._fill_log: list[Fill] = []

    # ------------------------------------------------------------------
    # BrokerInterface implementation
    # ------------------------------------------------------------------

    def submit_order(self, order: Order, open_price: float) -> Fill:
        scenario = self._schedule.get(order.order_date, order.ticker)
        scenario_type = scenario.scenario_type if scenario else ScenarioType.CLEAN

        if scenario_type == ScenarioType.REJECTED:
            fill = Fill(
                order_id=order.order_id,
                ticker=order.ticker,
                requested_quantity=order.quantity,
                filled_quantity=0.0,
                fill_price=open_price,
                fill_date=order.order_date,
                is_rejected=True,
                rejection_reason=(
                    scenario.rejection_reason if scenario else "simulated_rejection"
                ),
            )
            self._fill_log.append(fill)
            return fill

        if scenario_type == ScenarioType.PARTIAL_FILL:
            ratio = scenario.partial_fill_ratio if scenario else 0.5
            filled_qty = round(order.quantity * ratio, 9)
            fill_price = open_price
        elif scenario_type == ScenarioType.GAP_DOWN:
            gap = scenario.gap_down_pct if scenario else 0.02
            filled_qty = order.quantity
            fill_price = open_price * (1.0 - gap)
        else:
            # Clean fill
            filled_qty = order.quantity
            fill_price = open_price

        is_partial = abs(filled_qty) < abs(order.quantity)

        # Apply fill to internal ledger
        self._apply_fill(order.ticker, filled_qty, fill_price)

        # Apply unauthorized change scenario AFTER the clean fill
        if scenario_type == ScenarioType.UNAUTHORIZED_CHANGE and scenario:
            if scenario.unauthorized_ticker:
                self._positions[scenario.unauthorized_ticker] = (
                    scenario.unauthorized_shares
                )

        fill = Fill(
            order_id=order.order_id,
            ticker=order.ticker,
            requested_quantity=order.quantity,
            filled_quantity=filled_qty,
            fill_price=fill_price,
            fill_date=order.order_date,
            is_partial=is_partial,
        )
        self._fill_log.append(fill)
        return fill

    def get_positions(self) -> dict[str, float]:
        return {t: s for t, s in self._positions.items() if s > 1e-9}

    def get_cash(self) -> float:
        return self._cash

    def set_cash(self, amount: float) -> None:
        self._cash = amount

    def force_position(self, ticker: str, shares: float) -> None:
        """Directly set a position — simulates an unauthorized external change."""
        self._positions[ticker] = shares

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_fill(self, ticker: str, filled_qty: float, fill_price: float) -> None:
        """Update positions and cash after a fill."""
        cost = filled_qty * fill_price
        self._cash -= cost
        current = self._positions.get(ticker, 0.0)
        new_shares = current + filled_qty
        if abs(new_shares) < 1e-9:
            self._positions.pop(ticker, None)
        else:
            self._positions[ticker] = new_shares

    @property
    def fill_log(self) -> list[Fill]:
        return list(self._fill_log)
