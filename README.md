# Momentum Rotation Engine

A deterministic, auditable momentum-based ETF rotation engine with a full backtesting framework.

**Strategy Version:** 1.0.0  
**Status:** Backtest only — no live broker integration

---

## What This Is

This engine implements a systematic momentum rotation strategy over a curated ETF universe.
On the last trading day of each month, it ranks qualifying ETFs by momentum, selects the top
candidates, applies risk filters, and sizes positions using inverse-volatility weighting.

All logic is deterministic Python. No LLM makes any investment decision.

---

## Tradable Universe

| Ticker | Family           |
|--------|------------------|
| QQQ    | US Growth        |
| IWM    | US Small Cap     |
| SCHD   | US Dividend      |
| VEA    | Developed Intl   |
| VWO    | Emerging Markets |
| GLD    | Gold             |
| DBC    | Commodities      |
| TLT    | Long Treasuries  |
| SGOV   | Cash (fallback)  |

VOO and VTI are benchmarks only — not tradable.

---

## Quick Start

```bash
pip install -e ".[dev]"

# Run tests
pytest

# Run a synthetic backtest example
python examples/run_backtest.py
```

---

## Strategy Rules (v1.0.0)

| Parameter                  | Value                            |
|----------------------------|----------------------------------|
| Momentum lookback          | t-273 trading days               |
| Momentum exclude-recent    | 21 trading days                  |
| Trend filter               | Price > 200-day SMA              |
| Correlation filter         | 90-day rolling ≤ 0.70            |
| Volatility window          | 60 trading days                  |
| Weight bounds              | 30% – 70%                        |
| Single-asset weight        | 70% asset / 30% SGOV             |
| Daily stop threshold       | 10% from entry VWAP              |
| Kill switch threshold      | 15% from high-water mark         |
| Fail-safe trigger          | 5 consecutive data failures      |
| Rebalance signal date      | Last trading day of month (close)|
| Execution                  | Next session open                |

---

## Output Files

| File               | Description                      |
|--------------------|----------------------------------|
| `trade_log.csv`    | Sparse audit event log           |
| `daily_nav.csv`    | One row per trading day          |
| `metrics.json`     | Risk/return metrics              |
| `run_metadata.json`| Run provenance and snapshot IDs  |

---

## Reproducibility

Every backtest run records a `data_snapshot_id` and `adjusted_price_hash`. To replay a
backtest exactly, use the same frozen data snapshot. Historical adjusted prices change
due to dividends and splits — never silently recompute old backtests against new data.

---

## Governance

Parameter changes require governance review before merging. Bug fixes (implementation
does not match specification) may be merged after targeted review.

See [CLAUDE.md](CLAUDE.md) for full governance rules.

---

## Project Structure

```
src/momentum_agent/
├── config.py           # Universe, params, strategy constants
├── events.py           # Event types and audit log entries
├── data/               # Market data loading and validation
├── broker/             # Broker interface and adversarial simulator
├── risk/               # State machine, kill switch, stops
├── portfolio/          # Position state and reconciliation
├── strategies/
│   └── momentum/       # Signal calculation, selection, weighting
├── backtest/           # Trading calendar and main backtest engine
└── reporting/          # Trade log, NAV, metrics output
```
