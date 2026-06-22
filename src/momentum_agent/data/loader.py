"""Market data loader with snapshot metadata for reproducibility.

Design:
- Provides adjusted-close prices for signal generation.
- Provides raw open/close prices for execution and valuation.
- Records a snapshot ID and hash for every loaded dataset.
- Supports injecting pre-built DataFrames (for backtests using frozen data).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import uuid

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Snapshot metadata
# ---------------------------------------------------------------------------


@dataclass
class DataSnapshot:
    """Records exactly what data was used in a run (for reproducibility)."""

    snapshot_id: str
    data_vendor: str
    download_timestamp: str
    date_range: tuple[str, str]
    tickers: list[str]
    adjusted_price_hash: str
    raw_close_hash: str     # SHA-256 of raw close prices
    raw_open_hash: str      # SHA-256 of raw open prices
    proxy_config: dict[str, str] = field(default_factory=dict)
    is_proxy_spliced: bool = False
    proxy_log_hash: Optional[str] = None
    freeze_command_args: Optional[dict] = None
    python_version: Optional[str] = None
    package_version: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "data_vendor": self.data_vendor,
            "download_timestamp": self.download_timestamp,
            "date_range": list(self.date_range),
            "tickers": self.tickers,
            "adjusted_price_hash": self.adjusted_price_hash,
            "raw_close_hash": self.raw_close_hash,
            "raw_open_hash": self.raw_open_hash,
            "proxy_config": self.proxy_config,
            "is_proxy_spliced": self.is_proxy_spliced,
            "proxy_log_hash": self.proxy_log_hash,
            "freeze_command_args": self.freeze_command_args,
            "python_version": self.python_version,
            "package_version": self.package_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DataSnapshot":
        return cls(
            snapshot_id=d["snapshot_id"],
            data_vendor=d["data_vendor"],
            download_timestamp=d["download_timestamp"],
            date_range=tuple(d["date_range"]),
            tickers=d["tickers"],
            adjusted_price_hash=d["adjusted_price_hash"],
            raw_close_hash=d.get("raw_close_hash") or d.get("raw_price_hash", ""),
            raw_open_hash=d.get("raw_open_hash", ""),
            proxy_config=d.get("proxy_config", {}),
            is_proxy_spliced=d.get("is_proxy_spliced", False),
            proxy_log_hash=d.get("proxy_log_hash"),
            freeze_command_args=d.get("freeze_command_args"),
            python_version=d.get("python_version"),
            package_version=d.get("package_version"),
        )


def _hash_dataframe(df: pd.DataFrame) -> str:
    """Produce a deterministic SHA-256 hash of a DataFrame's values."""
    buf = df.to_csv(float_format="%.8f").encode()
    return hashlib.sha256(buf).hexdigest()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class MarketDataLoader:
    """Loads and manages market data for backtesting.

    In testing, inject DataFrames directly via the ``from_dataframes`` factory.
    In production backtests, call ``from_snapshot`` to load a frozen dataset.

    Adjusted close is used for signals.
    Raw close / open is used for execution and valuation.
    """

    def __init__(
        self,
        adj_close: pd.DataFrame,
        raw_close: pd.DataFrame,
        raw_open: pd.DataFrame,
        snapshot: DataSnapshot,
    ) -> None:
        self._adj_close = adj_close.copy()
        self._raw_close = raw_close.copy()
        self._raw_open = raw_open.copy()
        self.snapshot = snapshot

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_dataframes(
        cls,
        adj_close: pd.DataFrame,
        raw_close: pd.DataFrame,
        raw_open: pd.DataFrame,
        vendor: str = "synthetic",
        proxy_config: Optional[dict[str, str]] = None,
    ) -> "MarketDataLoader":
        """Create a loader from pre-built DataFrames (testing / frozen data)."""
        tickers = sorted(adj_close.columns.tolist())
        date_range = (
            adj_close.index[0].strftime("%Y-%m-%d"),
            adj_close.index[-1].strftime("%Y-%m-%d"),
        )
        snapshot = DataSnapshot(
            snapshot_id=str(uuid.uuid4()),
            data_vendor=vendor,
            download_timestamp=datetime.utcnow().isoformat(),
            date_range=date_range,
            tickers=tickers,
            adjusted_price_hash=_hash_dataframe(adj_close),
            raw_close_hash=_hash_dataframe(raw_close),
            raw_open_hash=_hash_dataframe(raw_open),
            proxy_config=proxy_config or {},
            is_proxy_spliced=bool(proxy_config),
        )
        return cls(adj_close, raw_close, raw_open, snapshot)

    @classmethod
    def from_yfinance(
        cls,
        tickers: list[str],
        start: date,
        end: date,
        cache_dir: Optional[Path] = None,
    ) -> "MarketDataLoader":
        """Download data from yfinance and create a loader.

        Note: yfinance is used only to bootstrap frozen datasets.
              Do not call this during reproducible backtests — use
              ``from_snapshot`` with a pre-saved frozen snapshot instead.
        """
        import yfinance as yf

        all_tickers = " ".join(tickers)
        raw = yf.download(
            all_tickers,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
            actions=True,
            progress=False,
            threads=True,
        )

        # Multi-ticker download returns MultiIndex columns.
        if isinstance(raw.columns, pd.MultiIndex):
            adj_close = raw["Adj Close"][tickers].copy()
            raw_close = raw["Close"][tickers].copy()
            raw_open = raw["Open"][tickers].copy()
        else:
            # Single ticker — wrap in DataFrame
            ticker = tickers[0]
            adj_close = raw[["Adj Close"]].rename(columns={"Adj Close": ticker})
            raw_close = raw[["Close"]].rename(columns={"Close": ticker})
            raw_open = raw[["Open"]].rename(columns={"Open": ticker})

        loader = cls.from_dataframes(
            adj_close, raw_close, raw_open, vendor="yfinance"
        )

        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            path = cache_dir / f"{loader.snapshot.snapshot_id}.parquet"
            adj_close.to_parquet(path.with_suffix(".adj.parquet"))
            raw_close.to_parquet(path.with_suffix(".raw_close.parquet"))
            raw_open.to_parquet(path.with_suffix(".raw_open.parquet"))
            with open(path.with_suffix(".snapshot.json"), "w") as f:
                json.dump(loader.snapshot.to_dict(), f, indent=2)

        return loader

    @classmethod
    def from_snapshot(
        cls,
        snapshot_dir: Path,
        verify_hash: bool = True,
    ) -> "MarketDataLoader":
        """Load a frozen snapshot from disk.

        Reads adjusted_close.parquet, raw_close.parquet, raw_open.parquet, and
        snapshot.json from ``snapshot_dir``.  When ``verify_hash`` is True (the
        default), all three SHA-256 hashes are recomputed and compared against
        the values stored in snapshot.json.  Any mismatch raises a ValueError so
        that vendor restatements or filesystem corruption are caught immediately.

        Args:
            snapshot_dir: Path to the snapshot directory (e.g.
                          ``data/snapshots/snap_20260622_143000_a3f8bc91/``).
            verify_hash:  Recompute and verify all three content hashes.

        Returns:
            MarketDataLoader ready for backtesting.

        Raises:
            FileNotFoundError: If any required file is missing.
            ValueError: If a hash mismatch is detected (when verify_hash=True).
        """
        snapshot_dir = Path(snapshot_dir)

        required = [
            "adjusted_close.parquet",
            "raw_close.parquet",
            "raw_open.parquet",
            "snapshot.json",
        ]
        for fname in required:
            p = snapshot_dir / fname
            if not p.exists():
                raise FileNotFoundError(
                    f"Snapshot file missing: {p}. "
                    f"Run 'python -m momentum_agent.data.freeze' to create a snapshot."
                )

        adj_close = pd.read_parquet(snapshot_dir / "adjusted_close.parquet")
        raw_close = pd.read_parquet(snapshot_dir / "raw_close.parquet")
        raw_open = pd.read_parquet(snapshot_dir / "raw_open.parquet")

        with open(snapshot_dir / "snapshot.json") as f:
            snapshot = DataSnapshot.from_dict(json.load(f))

        if verify_hash:
            _verify_hash(adj_close.fillna(0), snapshot.adjusted_price_hash, "adjusted_close")
            _verify_hash(raw_close.fillna(0), snapshot.raw_close_hash, "raw_close")
            _verify_hash(raw_open.fillna(0), snapshot.raw_open_hash, "raw_open")

        return cls(adj_close, raw_close, raw_open, snapshot)

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    @property
    def adj_close(self) -> pd.DataFrame:
        return self._adj_close

    @property
    def raw_close(self) -> pd.DataFrame:
        return self._raw_close

    @property
    def raw_open(self) -> pd.DataFrame:
        return self._raw_open

    def get_adj_close_series(self, ticker: str, up_to: date) -> pd.Series:
        """Return adjusted close series for a ticker up to and including a date."""
        mask = self._adj_close.index <= pd.Timestamp(up_to)
        return self._adj_close.loc[mask, ticker].dropna()

    def get_raw_close(self, ticker: str, on_date: date) -> Optional[float]:
        """Return raw close for a ticker on a specific date."""
        ts = pd.Timestamp(on_date)
        if ticker not in self._raw_close.columns:
            return None
        if ts not in self._raw_close.index:
            return None
        val = self._raw_close.loc[ts, ticker]
        if pd.isna(val):
            return None
        return float(val)

    def get_raw_open(self, ticker: str, on_date: date) -> Optional[float]:
        """Return raw open for a ticker on a specific date."""
        ts = pd.Timestamp(on_date)
        if ticker not in self._raw_open.columns:
            return None
        if ts not in self._raw_open.index:
            return None
        val = self._raw_open.loc[ts, ticker]
        if pd.isna(val):
            return None
        return float(val)

    def get_all_raw_closes(self, on_date: date) -> dict[str, Optional[float]]:
        ts = pd.Timestamp(on_date)
        result: dict[str, Optional[float]] = {}
        if ts in self._raw_close.index:
            for col in self._raw_close.columns:
                val = self._raw_close.loc[ts, col]
                result[col] = float(val) if not pd.isna(val) else None
        else:
            for col in self._raw_close.columns:
                result[col] = None
        return result

    def get_all_raw_opens(self, on_date: date) -> dict[str, Optional[float]]:
        ts = pd.Timestamp(on_date)
        result: dict[str, Optional[float]] = {}
        if ts in self._raw_open.index:
            for col in self._raw_open.columns:
                val = self._raw_open.loc[ts, col]
                result[col] = float(val) if not pd.isna(val) else None
        else:
            for col in self._raw_open.columns:
                result[col] = None
        return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _verify_hash(df: pd.DataFrame, expected: str, channel: str) -> None:
    """Recompute hash and raise ValueError on mismatch."""
    if not expected:
        return
    actual = _hash_dataframe(df)
    if actual != expected:
        raise ValueError(
            f"Snapshot hash mismatch for {channel}: "
            f"expected {expected}, got {actual}. "
            f"The parquet file may have been modified or vendor-restated. "
            f"Run 'python -m momentum_agent.data.freeze' to create a new snapshot."
        )
