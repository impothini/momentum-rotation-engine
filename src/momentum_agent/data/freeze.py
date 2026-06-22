"""Frozen data snapshot workflow.

Downloads market data, splices proxy series for pre-inception periods, hashes
everything, and writes an immutable parquet snapshot to disk.  All subsequent
backtests should load from the frozen snapshot — never re-download raw data.

Usage (CLI):
    python -m momentum_agent.data.freeze \\
        --tickers QQQ IWM SCHD VEA VWO GLD DBC TLT SGOV VOO VTI AGG BIL VIG \\
        --start 2000-01-01 \\
        --end   2025-12-31 \\
        --out   data/snapshots/

Produced files:
    data/snapshots/snap_<YYYYMMDD_HHMMSS>_<hash8>/
        adjusted_close.parquet
        raw_close.parquet
        raw_open.parquet
        snapshot.json
        proxy_log.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional
import uuid

import pandas as pd

from momentum_agent.config import BENCHMARKS, TRADABLE_UNIVERSE
from momentum_agent.data.loader import DataSnapshot, _hash_dataframe
from momentum_agent.data.proxy import ProxyConfig


def _utc_timestamp_str() -> str:
    """Return current UTC time as YYYYMMDD_HHMMSS.  Extracted for testability."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _utc_iso() -> str:
    """Return current UTC time as ISO-8601 string.  Extracted for testability."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Proxy log entry
# ---------------------------------------------------------------------------


@dataclass
class ProxyLogEntry:
    """Records one proxy substitution period in the snapshot.

    A separate entry is created per price channel (adj_close, raw_close, raw_open)
    because each channel is scaled independently.
    """

    ticker: str
    proxy: str
    channel: str             # "adj_close" | "raw_close" | "raw_open"
    period_start: str        # ISO date — first day proxy data is used
    period_end: str          # ISO date — last day proxy data is used
    splice_date: str         # ISO date — first day actual ticker data starts
    scale_factor: float      # proxy prices multiplied by this at the splice point
    rows_spliced: int        # number of trading days covered by proxy
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Proxy splicer
# ---------------------------------------------------------------------------


class ProxySplicer:
    """Splices a proxy price series into a ticker's pre-inception gap.

    Splice algorithm (produces zero phantom return at the boundary):

        splice_date      = first date actual ticker has data
        last_proxy_date  = last date in proxy series strictly before splice_date

        scale_factor = actual[splice_date] / proxy[last_proxy_date]

        scaled_proxy = proxy[: last_proxy_date] * scale_factor
        spliced      = concat([scaled_proxy, actual[splice_date :]])

    Scale factors are computed independently for each price channel (adj_close,
    raw_close, raw_open) so that each channel has internal price continuity.

    Raises ValueError if the proxy or actual price at the boundary is missing,
    zero, or negative.
    """

    def splice(
        self,
        ticker: str,
        proxy: str,
        actual_prices: pd.Series,   # one channel for the real ticker
        proxy_prices: pd.Series,    # same channel for the proxy ticker
        channel: str = "unknown",
    ) -> tuple[pd.Series, Optional[ProxyLogEntry]]:
        """Splice a single price channel.  Returns (spliced_series, log_entry)."""
        actual_clean = actual_prices.dropna()
        proxy_clean = proxy_prices.dropna()

        if actual_clean.empty:
            return proxy_clean.copy(), None

        splice_ts = actual_clean.index[0]
        proxy_before = proxy_clean[proxy_clean.index < splice_ts]

        if proxy_before.empty:
            # Proxy has no data before actual ticker's first date — nothing to prepend
            return actual_clean.copy(), None

        last_proxy_date = proxy_before.index[-1]
        last_proxy_price = proxy_before.iloc[-1]
        first_actual_price = float(actual_clean.iloc[0])

        # Boundary validation
        if last_proxy_price is None or pd.isna(last_proxy_price):
            raise ValueError(
                f"Proxy '{proxy}' [{channel}] is missing on last pre-splice date "
                f"{last_proxy_date.date()}"
            )
        last_proxy_price = float(last_proxy_price)
        if last_proxy_price <= 0:
            raise ValueError(
                f"Proxy '{proxy}' [{channel}] has non-positive price "
                f"{last_proxy_price} on {last_proxy_date.date()}"
            )
        if first_actual_price <= 0:
            raise ValueError(
                f"Ticker '{ticker}' [{channel}] has non-positive price "
                f"{first_actual_price} on {splice_ts.date()}"
            )

        scale_factor = first_actual_price / last_proxy_price
        scaled_proxy = proxy_before * scale_factor

        spliced = pd.concat([scaled_proxy, actual_clean])
        # Remove any duplicate index entries (keep the actual price if overlap)
        spliced = spliced[~spliced.index.duplicated(keep="last")].sort_index()

        entry = ProxyLogEntry(
            ticker=ticker,
            proxy=proxy,
            channel=channel,
            period_start=scaled_proxy.index[0].date().isoformat(),
            period_end=scaled_proxy.index[-1].date().isoformat(),
            splice_date=splice_ts.date().isoformat(),
            scale_factor=scale_factor,
            rows_spliced=len(scaled_proxy),
            note=(
                f"{proxy} scaled ×{scale_factor:.6f} so that "
                f"{proxy}[{last_proxy_date.date()}]×scale ≈ "
                f"{ticker}[{splice_ts.date()}]={first_actual_price:.4f}"
            ),
        )
        return spliced, entry


# ---------------------------------------------------------------------------
# Freeze result
# ---------------------------------------------------------------------------


@dataclass
class FreezeResult:
    snapshot_dir: Path
    snapshot: DataSnapshot
    proxy_log: list[ProxyLogEntry] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Snapshot ID   : {self.snapshot.snapshot_id}",
            f"Directory     : {self.snapshot_dir}",
            f"Tickers       : {', '.join(self.snapshot.tickers)}",
            f"Date range    : {self.snapshot.date_range[0]} → {self.snapshot.date_range[1]}",
            f"Adj hash      : {self.snapshot.adjusted_price_hash[:16]}...",
            f"Proxy splices : {len(self.proxy_log)}",
        ]
        shown: set[str] = set()
        for entry in self.proxy_log:
            key = f"{entry.ticker}←{entry.proxy}"
            if key not in shown:
                lines.append(
                    f"  {entry.ticker} ← {entry.proxy}  "
                    f"[{entry.period_start} .. {entry.period_end}]  "
                    f"rows={entry.rows_spliced}"
                )
                shown.add(key)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default downloader (calls yfinance)
# ---------------------------------------------------------------------------


def _yfinance_downloader(
    tickers: list[str],
    start: date,
    end: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Download adj_close, raw_close, raw_open from yfinance."""
    import yfinance as yf

    ticker_str = " ".join(tickers)
    raw = yf.download(
        ticker_str,
        start=start.isoformat(),
        end=end.isoformat(),
        auto_adjust=False,
        actions=True,
        progress=True,
        threads=True,
        group_by="column",
    )

    if isinstance(raw.columns, pd.MultiIndex):
        adj_close = raw["Adj Close"].reindex(columns=tickers)
        raw_close = raw["Close"].reindex(columns=tickers)
        raw_open = raw["Open"].reindex(columns=tickers)
    else:
        t = tickers[0]
        adj_close = raw[["Adj Close"]].rename(columns={"Adj Close": t})
        raw_close = raw[["Close"]].rename(columns={"Close": t})
        raw_open = raw[["Open"]].rename(columns={"Open": t})

    return adj_close, raw_close, raw_open


# ---------------------------------------------------------------------------
# Main freeze function
# ---------------------------------------------------------------------------


def freeze(
    tickers: list[str],
    start: date,
    end: date,
    output_dir: Path,
    proxy_config: Optional[ProxyConfig] = None,
    overwrite: bool = False,
    vendor: str = "yfinance",
    downloader: Optional[Callable] = None,
    freeze_command_args: Optional[dict] = None,
) -> FreezeResult:
    """Download, splice, hash, and persist a frozen data snapshot.

    Args:
        tickers: Tickers to include in the snapshot.
        start: Start date (inclusive).
        end: End date (inclusive).
        output_dir: Parent directory for the snapshot folder.
        proxy_config: Proxy substitutions.  Defaults to ProxyConfig.default().
        overwrite: If False (default), raise if snapshot dir already exists.
        vendor: Data vendor label written into snapshot.json.
        downloader: Callable(tickers, start, end) → (adj_df, raw_close_df, raw_open_df).
                    Defaults to yfinance.  Pass a synthetic callable in tests.
        freeze_command_args: CLI args dict recorded in snapshot.json for provenance.
    """
    if proxy_config is None:
        proxy_config = ProxyConfig.default()
    if downloader is None:
        downloader = _yfinance_downloader

    # ----------------------------------------------------------------
    # Step 1: Validate proxy coverage before any download
    # ----------------------------------------------------------------
    missing_proxies = proxy_config.validate(tickers, start)
    if missing_proxies:
        raise ValueError(
            f"Tickers need a proxy for start_date={start} but none is configured: "
            f"{missing_proxies}. "
            f"Pass --proxy TICKER=PROXY_TICKER or use a start date after each "
            f"ticker's inception date."
        )

    # ----------------------------------------------------------------
    # Step 2: Build download list (tickers + any required proxies)
    # ----------------------------------------------------------------
    proxy_tickers: dict[str, str] = {}  # ticker → proxy_ticker
    download_set: set[str] = set(tickers)

    for ticker in tickers:
        if proxy_config.needs_proxy(ticker, start):
            proxy_ticker = proxy_config.get_proxy(ticker)
            if proxy_ticker:
                proxy_tickers[ticker] = proxy_ticker
                download_set.add(proxy_ticker)

    download_list = sorted(download_set)

    # ----------------------------------------------------------------
    # Step 3: Download
    # ----------------------------------------------------------------
    all_adj, all_raw_close, all_raw_open = downloader(download_list, start, end)

    # ----------------------------------------------------------------
    # Step 4: Apply proxy splices — independently per channel
    # ----------------------------------------------------------------
    splicer = ProxySplicer()
    proxy_log: list[ProxyLogEntry] = []

    for ticker, proxy_ticker in proxy_tickers.items():
        if ticker not in all_adj.columns or proxy_ticker not in all_adj.columns:
            print(
                f"WARNING: Cannot splice {ticker} ← {proxy_ticker}: "
                f"missing from downloaded data. Skipping.",
                file=sys.stderr,
            )
            continue

        for channel, df in (
            ("adj_close", all_adj),
            ("raw_close", all_raw_close),
            ("raw_open", all_raw_open),
        ):
            actual_series = df[ticker].dropna()
            proxy_series = df[proxy_ticker].dropna()
            spliced, entry = splicer.splice(
                ticker, proxy_ticker, actual_series, proxy_series, channel=channel
            )
            df[ticker] = spliced.reindex(df.index)
            if entry is not None:
                proxy_log.append(entry)

    # Keep only the requested tickers (drop proxy-only helper tickers)
    all_adj = all_adj.reindex(columns=tickers)
    all_raw_close = all_raw_close.reindex(columns=tickers)
    all_raw_open = all_raw_open.reindex(columns=tickers)

    # ----------------------------------------------------------------
    # Step 5: Compute hashes (NaN → 0 so hash is deterministic)
    # ----------------------------------------------------------------
    adj_hash = _hash_dataframe(all_adj.fillna(0))
    raw_close_hash = _hash_dataframe(all_raw_close.fillna(0))
    raw_open_hash = _hash_dataframe(all_raw_open.fillna(0))

    proxy_log_json_bytes = json.dumps(
        [e.to_dict() for e in proxy_log], sort_keys=True
    ).encode()
    proxy_log_hash = hashlib.sha256(proxy_log_json_bytes).hexdigest()

    # ----------------------------------------------------------------
    # Step 6: Build snapshot directory
    # ----------------------------------------------------------------
    ts_str = _utc_timestamp_str()
    dir_name = f"snap_{ts_str}_{adj_hash[:8]}"
    snapshot_dir = output_dir / dir_name

    if snapshot_dir.exists() and not overwrite:
        raise FileExistsError(
            f"Snapshot directory already exists: {snapshot_dir}. "
            f"Use --overwrite to replace it."
        )
    snapshot_dir.mkdir(parents=True, exist_ok=overwrite)

    # ----------------------------------------------------------------
    # Step 7: Write parquet files
    # ----------------------------------------------------------------
    all_adj.to_parquet(snapshot_dir / "adjusted_close.parquet")
    all_raw_close.to_parquet(snapshot_dir / "raw_close.parquet")
    all_raw_open.to_parquet(snapshot_dir / "raw_open.parquet")

    # ----------------------------------------------------------------
    # Step 8: Build and write snapshot.json
    # ----------------------------------------------------------------
    try:
        from importlib.metadata import version as pkg_version
        package_version: Optional[str] = pkg_version("momentum-rotation-engine")
    except Exception:
        package_version = None

    snapshot = DataSnapshot(
        snapshot_id=str(uuid.uuid4()),
        data_vendor=vendor,
        download_timestamp=_utc_iso(),
        date_range=(start.isoformat(), end.isoformat()),
        tickers=sorted(tickers),
        adjusted_price_hash=adj_hash,
        raw_close_hash=raw_close_hash,
        raw_open_hash=raw_open_hash,
        proxy_config={t: p for t, p in proxy_tickers.items()},
        is_proxy_spliced=bool(proxy_tickers),
        proxy_log_hash=proxy_log_hash,
        freeze_command_args=freeze_command_args or {},
        python_version=sys.version,
        package_version=package_version,
    )
    with open(snapshot_dir / "snapshot.json", "w") as f:
        json.dump(snapshot.to_dict(), f, indent=2)

    # ----------------------------------------------------------------
    # Step 9: Write proxy_log.json
    # ----------------------------------------------------------------
    with open(snapshot_dir / "proxy_log.json", "w") as f:
        json.dump([e.to_dict() for e in proxy_log], f, indent=2)

    return FreezeResult(
        snapshot_dir=snapshot_dir,
        snapshot=snapshot,
        proxy_log=proxy_log,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    default_tickers = sorted(set(TRADABLE_UNIVERSE) | set(BENCHMARKS))
    parser = argparse.ArgumentParser(
        prog="python -m momentum_agent.data.freeze",
        description="Download and freeze a reproducible market data snapshot.",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=default_tickers,
        metavar="TICKER",
        help=(
            "Tickers to download.  "
            f"Default: universe + benchmarks ({', '.join(default_tickers)})"
        ),
    )
    parser.add_argument(
        "--start",
        required=True,
        metavar="YYYY-MM-DD",
        help="Start date (inclusive).",
    )
    parser.add_argument(
        "--end",
        required=True,
        metavar="YYYY-MM-DD",
        help="End date (inclusive).",
    )
    parser.add_argument(
        "--out",
        default="data/snapshots",
        metavar="PATH",
        help="Parent directory for the snapshot folder.  Default: data/snapshots/",
    )
    parser.add_argument(
        "--proxy",
        nargs="*",
        default=[],
        metavar="TICKER=PROXY",
        help=(
            "Override proxy mappings, e.g. --proxy SGOV=BIL SCHD=VIG. "
            "Merged with defaults (overrides win)."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite an existing snapshot directory.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    output_dir = Path(args.out)

    proxy_map = dict(ProxyConfig.default().proxy_map)
    for kv in (args.proxy or []):
        if "=" not in kv:
            parser.error(f"Invalid --proxy format (expected TICKER=PROXY): {kv!r}")
        ticker_part, proxy_part = kv.split("=", 1)
        proxy_map[ticker_part.strip()] = proxy_part.strip()

    proxy_config = ProxyConfig(proxy_map=proxy_map)

    freeze_command_args = {
        "tickers": args.tickers,
        "start": args.start,
        "end": args.end,
        "out": args.out,
        "proxy": args.proxy,
        "overwrite": args.overwrite,
    }

    print(f"Freeze snapshot: {start} → {end}")
    print(f"Tickers : {' '.join(args.tickers)}")
    print(f"Output  : {output_dir.absolute()}")

    result = freeze(
        tickers=args.tickers,
        start=start,
        end=end,
        output_dir=output_dir,
        proxy_config=proxy_config,
        overwrite=args.overwrite,
        freeze_command_args=freeze_command_args,
    )

    print("\n" + result.summary())
    print(f"\nSnapshot ready: {result.snapshot_dir}")


if __name__ == "__main__":
    main()
