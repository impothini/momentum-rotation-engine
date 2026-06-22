"""Event types and audit log entries for the momentum rotation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    REBALANCE = "REBALANCE"
    STOP_TRIGGER = "STOP_TRIGGER"
    STOP_EXIT = "STOP_EXIT"
    KILL_SWITCH = "KILL_SWITCH"
    DATA_INTEGRITY_FAILURE = "DATA_INTEGRITY_FAILURE"
    FAILSAFE_LIQUIDATION = "FAILSAFE_LIQUIDATION"
    RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
    UNAUTHORIZED_POSITION_CHANGE = "UNAUTHORIZED_POSITION_CHANGE"
    ORDER_REJECTED = "ORDER_REJECTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    NO_ACTION = "NO_ACTION"


@dataclass
class Event:
    date: date
    event_type: EventType
    strategy_version: str = "1.0.0"
    ticker: Optional[str] = None
    asset_family: Optional[str] = None
    signal_price: Optional[float] = None
    execution_open_price: Optional[float] = None
    fill_price: Optional[float] = None
    entry_vwap_fill_price: Optional[float] = None
    target_weight: Optional[float] = None
    target_shares: Optional[float] = None
    actual_shares: Optional[float] = None
    order_quantity: Optional[float] = None
    filled_quantity: Optional[float] = None
    cash: Optional[float] = None
    reconciliation_status: Optional[str] = None
    data_status: Optional[str] = None
    proxy_used: bool = False
    reason: Optional[str] = None
    notes: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "event_type": self.event_type.value,
            "strategy_version": self.strategy_version,
            "ticker": self.ticker,
            "asset_family": self.asset_family,
            "signal_price": self.signal_price,
            "execution_open_price": self.execution_open_price,
            "fill_price": self.fill_price,
            "entry_vwap_fill_price": self.entry_vwap_fill_price,
            "target_weight": self.target_weight,
            "target_shares": self.target_shares,
            "actual_shares": self.actual_shares,
            "order_quantity": self.order_quantity,
            "filled_quantity": self.filled_quantity,
            "cash": self.cash,
            "reconciliation_status": self.reconciliation_status,
            "data_status": self.data_status,
            "proxy_used": self.proxy_used,
            "reason": self.reason,
            "notes": self.notes,
        }
