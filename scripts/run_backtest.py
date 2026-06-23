"""Run a full backtest from a frozen snapshot.

Usage:
    python scripts/run_backtest.py --snapshot data/snapshots/<snap_dir> \
        --start 2007-01-01 --end 2025-12-31 \
        --out data/runs/

Outputs written to data/runs/<run_id>/:
    trade_log.csv
    daily_nav.csv
    metrics.json
    run_meta.json      (snapshot_id, strategy_version, date range, run_id)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from momentum_agent.backtest.engine import BacktestEngine
from momentum_agent.broker.simulated import SimulatedBroker
from momentum_agent.config import EngineConfig
from momentum_agent.data.loader import MarketDataLoader


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python scripts/run_backtest.py",
        description="Run a full backtest from a frozen snapshot.",
    )
    p.add_argument(
        "--snapshot",
        required=True,
        metavar="PATH",
        help="Path to the frozen snapshot directory (contains snapshot.json).",
    )
    p.add_argument(
        "--start",
        required=True,
        metavar="YYYY-MM-DD",
        help="Backtest start date (inclusive).",
    )
    p.add_argument(
        "--end",
        required=True,
        metavar="YYYY-MM-DD",
        help="Backtest end date (inclusive).",
    )
    p.add_argument(
        "--capital",
        type=float,
        default=100_000.0,
        metavar="DOLLARS",
        help="Initial capital in dollars.  Default: 100000.",
    )
    p.add_argument(
        "--out",
        default="data/runs",
        metavar="PATH",
        help="Parent directory for run output.  Default: data/runs/",
    )
    p.add_argument(
        "--no-verify-hash",
        action="store_true",
        default=False,
        help="Skip SHA-256 hash verification on snapshot load (not recommended).",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    snapshot_dir = Path(args.snapshot)
    if not snapshot_dir.is_dir():
        sys.exit(f"Error: snapshot directory not found: {snapshot_dir}")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print(f"Loading snapshot : {snapshot_dir}")
    loader = MarketDataLoader.from_snapshot(
        snapshot_dir, verify_hash=not args.no_verify_hash
    )
    snap = loader.snapshot
    print(f"  snapshot_id    : {snap.snapshot_id}")
    print(f"  data range     : {snap.date_range[0]} → {snap.date_range[1]}")
    print(f"  tickers        : {' '.join(sorted(snap.tickers))}")
    print(f"  adj_price_hash : {snap.adjusted_price_hash[:16]}...")

    config = EngineConfig(initial_capital=args.capital)
    broker = SimulatedBroker()
    engine = BacktestEngine(config=config, broker=broker, data_loader=loader)

    print(f"\nRunning backtest : {start} → {end}")
    print(f"  strategy       : {config.momentum_params.strategy_version}")
    print(f"  capital        : ${args.capital:,.0f}")

    result = engine.run(start_date=start, end_date=end)

    # Write outputs
    out_dir = Path(args.out) / result.run_id
    result.write_outputs(out_dir)

    # Write run metadata for reproducibility audit
    run_meta = {
        "run_id": result.run_id,
        "snapshot_id": snap.snapshot_id,
        "snapshot_dir": str(snapshot_dir.absolute()),
        "adjusted_price_hash": snap.adjusted_price_hash,
        "raw_close_hash": snap.raw_close_hash,
        "raw_open_hash": snap.raw_open_hash,
        "strategy_version": config.momentum_params.strategy_version,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "initial_capital": args.capital,
    }
    with open(out_dir / "run_meta.json", "w") as f:
        json.dump(run_meta, f, indent=2)

    # Print summary
    print(f"\nOutputs written  : {out_dir}")
    print(f"  run_id         : {result.run_id}")

    nav_series = result.daily_nav_writer.get_nav_series()
    if len(nav_series) > 0:
        final_nav = nav_series.iloc[-1]
        total_return = (final_nav / args.capital - 1) * 100
        print(f"\nFinal NAV        : ${final_nav:,.2f}")
        print(f"Total return     : {total_return:+.1f}%")

    trade_log_path = out_dir / "trade_log.csv"
    if trade_log_path.exists():
        import csv
        with open(trade_log_path) as f:
            rows = list(csv.DictReader(f))
        print(f"Trade log rows   : {len(rows)}")

    metrics = result.metrics
    if metrics:
        print("\nKey metrics:")
        for k, v in metrics.items():
            print(f"  {k:<30} {v}")

    print(f"\nRepeatability check:")
    print(f"  snapshot_id={snap.snapshot_id}")
    print(f"  strategy_version={config.momentum_params.strategy_version}")
    print(f"  => same inputs must produce identical trade_log.csv")


if __name__ == "__main__":
    main()
