"""Trade log writer — sparse event log (one row per significant event).

Required columns (spec §trade_log.csv):
  date, event_type, strategy_version, ticker, asset_family,
  signal_price, execution_open_price, fill_price, entry_vwap_fill_price,
  target_weight, target_shares, actual_shares, order_quantity,
  filled_quantity, cash, reconciliation_status, data_status,
  proxy_used, reason, notes
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from momentum_agent.events import Event

TRADE_LOG_COLUMNS = [
    "date",
    "event_type",
    "strategy_version",
    "ticker",
    "asset_family",
    "signal_price",
    "execution_open_price",
    "fill_price",
    "entry_vwap_fill_price",
    "target_weight",
    "target_shares",
    "actual_shares",
    "order_quantity",
    "filled_quantity",
    "cash",
    "reconciliation_status",
    "data_status",
    "proxy_used",
    "reason",
    "notes",
]


class TradeLogWriter:
    """Accumulates events and writes them to trade_log.csv."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    def record(self, event: Event) -> None:
        self._rows.append(event.to_dict())

    def record_many(self, events: list[Event]) -> None:
        for e in events:
            self.record(e)

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=TRADE_LOG_COLUMNS)
        df = pd.DataFrame(self._rows)
        # Ensure all required columns are present
        for col in TRADE_LOG_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[TRADE_LOG_COLUMNS]

    def write_csv(self, path: Path) -> None:
        self.to_dataframe().to_csv(path, index=False)

    def validate_schema(self) -> bool:
        """Return True if the trade log has all required columns."""
        df = self.to_dataframe()
        return all(col in df.columns for col in TRADE_LOG_COLUMNS)

    @property
    def event_count(self) -> int:
        return len(self._rows)
