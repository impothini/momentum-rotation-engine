"""Portfolio state: position tracking, NAV, and high-water mark."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class PositionState:
    """Tracks the entry metadata for one held position."""

    ticker: str
    shares: float
    entry_vwap_fill_price: float
    entry_timestamp: date
    entry_rebalance_id: str
    asset_family: str

    def update_entry_vwap(self, additional_shares: float, fill_price: float) -> None:
        """Update VWAP entry price when adding to an existing position."""
        if additional_shares <= 0:
            return
        total_shares = self.shares + additional_shares
        # VWAP across all shares: weighted average
        new_vwap = (
            self.shares * self.entry_vwap_fill_price
            + additional_shares * fill_price
        ) / total_shares
        self.shares = total_shares
        self.entry_vwap_fill_price = new_vwap


@dataclass
class PortfolioState:
    """Mutable state of the portfolio across the backtest."""

    positions: dict[str, PositionState] = field(default_factory=dict)
    cash: float = 0.0
    high_water_mark: Optional[float] = None
    last_rebalance_date: Optional[date] = None
    last_rebalance_id: Optional[str] = None

    # Tickers that cannot be purchased until the next monthly rebalance.
    lockout_set: set[str] = field(default_factory=set)

    # Per-ticker consecutive data failure counts.
    failure_counts: dict[str, int] = field(default_factory=dict)

    # Engine's last-known positions (before executing orders).
    # Used to detect unauthorized external changes.
    last_known_positions: dict[str, float] = field(default_factory=dict)

    def nav(self, raw_closes: dict[str, Optional[float]]) -> Optional[float]:
        """Calculate NAV from raw close prices.

        Returns None if ANY held position lacks a valid close (spec §PARTIAL NAV RULE).
        """
        nav_value = self.cash
        for ticker, position in self.positions.items():
            if position.shares == 0.0:
                continue
            close = raw_closes.get(ticker)
            if close is None or close <= 0:
                return None
            nav_value += position.shares * close
        return nav_value

    def position_shares(self, ticker: str) -> float:
        pos = self.positions.get(ticker)
        return pos.shares if pos else 0.0

    def add_position(
        self,
        ticker: str,
        shares: float,
        fill_price: float,
        fill_date: date,
        rebalance_id: str,
        asset_family: str,
    ) -> None:
        """Add shares to a position, updating entry VWAP if already held."""
        existing = self.positions.get(ticker)
        if existing is not None and existing.shares > 0:
            existing.update_entry_vwap(shares, fill_price)
        else:
            self.positions[ticker] = PositionState(
                ticker=ticker,
                shares=shares,
                entry_vwap_fill_price=fill_price,
                entry_timestamp=fill_date,
                entry_rebalance_id=rebalance_id,
                asset_family=asset_family,
            )

    def reduce_position(self, ticker: str, shares: float) -> None:
        """Reduce or fully close a position."""
        existing = self.positions.get(ticker)
        if existing is None:
            return
        existing.shares -= shares
        if existing.shares <= 1e-9:
            del self.positions[ticker]

    def clear_position(self, ticker: str) -> None:
        self.positions.pop(ticker, None)

    def record_valid_bar(self, ticker: str) -> None:
        self.failure_counts[ticker] = 0

    def record_failure(self, ticker: str) -> int:
        count = self.failure_counts.get(ticker, 0) + 1
        self.failure_counts[ticker] = count
        return count

    def get_failure_count(self, ticker: str) -> int:
        return self.failure_counts.get(ticker, 0)

    def snapshot_positions(self) -> dict[str, float]:
        """Return {ticker: shares} snapshot of current positions."""
        return {t: p.shares for t, p in self.positions.items() if p.shares > 1e-9}
