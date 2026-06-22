"""Abstract broker interface and order/fill data types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
import uuid


# ---------------------------------------------------------------------------
# Order and Fill
# ---------------------------------------------------------------------------


@dataclass
class Order:
    ticker: str
    quantity: float          # positive = buy, negative = sell
    order_type: str          # "market" only in v1
    order_date: date
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    notes: Optional[str] = None


@dataclass
class Fill:
    order_id: str
    ticker: str
    requested_quantity: float
    filled_quantity: float
    fill_price: float
    fill_date: date
    is_partial: bool = False
    is_rejected: bool = False
    rejection_reason: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return not self.is_rejected and not self.is_partial


# ---------------------------------------------------------------------------
# Broker interface
# ---------------------------------------------------------------------------


class BrokerInterface(ABC):
    """Minimal interface that both the simulated and (future) live broker implement."""

    @abstractmethod
    def submit_order(self, order: Order, open_price: float) -> Fill:
        """Submit a market order. open_price is the session-open price."""
        ...

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """Return {ticker: shares} for all currently held positions."""
        ...

    @abstractmethod
    def get_cash(self) -> float:
        """Return current cash balance."""
        ...

    @abstractmethod
    def set_cash(self, amount: float) -> None:
        """Set cash balance (used during backtest initialisation)."""
        ...

    @abstractmethod
    def force_position(self, ticker: str, shares: float) -> None:
        """Directly set a position (used to inject unauthorised changes in tests)."""
        ...
