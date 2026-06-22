"""Example: run a synthetic backtest of Strategy v1.0.0.

This script uses entirely synthetic (deterministic) price data.
No network calls are made.

Usage:
    python examples/run_backtest.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# Allow running from the repo root without installing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from momentum_agent.backtest.calendar import TradingCalendar
from momentum_agent.backtest.engine import BacktestEngine
from momentum_agent.broker.simulated import SimulatedBroker
from momentum_agent.config import TRADABLE_UNIVERSE, EngineConfig
from momentum_agent.data.loader import MarketDataLoader


def build_synthetic_data(
    tickers: list[str],
    start: date,
    end: date,
    seed: int = 42,
) -> MarketDataLoader:
    """Build deterministic synthetic price DataFrames."""
    cal = TradingCalendar()
    trading_days = cal.get_trading_days(start, end)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in trading_days])

    rng = np.random.default_rng(seed)
    adj_data: dict[str, list[float]] = {}
    raw_close_data: dict[str, list[float]] = {}
    raw_open_data: dict[str, list[float]] = {}

    for i, ticker in enumerate(tickers):
        rng_t = np.random.default_rng(seed + i)
        # Slightly different drift per ticker
        drift = 0.0004 + i * 0.00003
        returns = rng_t.normal(drift, 0.012, size=len(idx))
        adj_prices = 100.0 * np.cumprod(1 + returns)
        # Raw close ~ adj_close (simplified — ignoring dividend adjustments)
        raw_close = adj_prices * rng_t.uniform(0.999, 1.001, size=len(idx))
        # Raw open ~ raw_close * small gap
        raw_open = raw_close * rng_t.uniform(0.998, 1.002, size=len(idx))
        adj_data[ticker] = adj_prices.tolist()
        raw_close_data[ticker] = raw_close.tolist()
        raw_open_data[ticker] = raw_open.tolist()

    adj_df = pd.DataFrame(adj_data, index=idx)
    raw_close_df = pd.DataFrame(raw_close_data, index=idx)
    raw_open_df = pd.DataFrame(raw_open_data, index=idx)

    return MarketDataLoader.from_dataframes(
        adj_close=adj_df,
        raw_close=raw_close_df,
        raw_open=raw_open_df,
        vendor="synthetic_example",
    )


def main() -> None:
    start = date(2020, 1, 2)
    end = date(2023, 12, 29)
    tickers = TRADABLE_UNIVERSE

    print("Building synthetic data...")
    loader = build_synthetic_data(tickers, start, end)

    config = EngineConfig(initial_capital=100_000.0)
    broker = SimulatedBroker(initial_cash=config.initial_capital)
    engine = BacktestEngine(config=config, broker=broker, data_loader=loader)

    print(f"Running backtest: {start} → {end}")
    result = engine.run(start_date=start, end_date=end, tickers=tickers)

    # Write outputs
    output_dir = Path("output")
    result.write_outputs(output_dir)

    print(f"\n=== Backtest Complete ===")
    print(f"Run ID:        {result.run_id}")
    print(f"Events logged: {result.trade_log_writer.event_count}")
    print(f"\nMetrics:")
    for key, val in result.metrics.items():
        if val is not None:
            print(f"  {key}: {val:.4f}" if isinstance(val, float) else f"  {key}: {val}")
    print(f"\nOutputs written to: {output_dir.absolute()}/")

    # Write run_metadata.json
    metadata = {
        "strategy_version": config.momentum_params.strategy_version,
        "parameter_set": {
            "lookback_days": config.momentum_params.lookback_days,
            "exclude_recent_days": config.momentum_params.exclude_recent_days,
            "trend_filter_window": config.momentum_params.trend_filter_window,
            "correlation_window": config.momentum_params.correlation_window,
            "correlation_threshold": config.momentum_params.correlation_threshold,
            "volatility_window": config.momentum_params.volatility_window,
            "min_weight": config.momentum_params.min_weight,
            "max_weight": config.momentum_params.max_weight,
            "stop_loss_pct": config.risk_params.stop_loss_pct,
            "kill_switch_pct": config.risk_params.kill_switch_pct,
        },
        "data_snapshot_id": loader.snapshot.snapshot_id,
        "proxy_config": loader.snapshot.proxy_config,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "benchmark_config": {
            "VOO": "benchmark_only",
            "VTI": "benchmark_only",
            "60/40": {"VOO": 0.60, "AGG": 0.40},
            "SGOV": "risk_free_rate_proxy",
        },
        "created_at": loader.snapshot.download_timestamp,
    }
    with open(output_dir / "run_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    print("  run_metadata.json written.")


if __name__ == "__main__":
    main()
