# CLAUDE.md — Momentum Rotation Engine

## Project Purpose

This is a deterministic portfolio-management engine implementing a momentum-based ETF rotation
strategy (Strategy Version 1.0.0). It includes a full backtesting framework, state-machine-driven
risk controls, adversarial broker simulation, reconciliation, audit logging, and governance controls.

**This is NOT an AI trading bot.**

## LLM Boundaries — Non-Negotiable

LLMs MUST NOT:
- Calculate momentum signals
- Rank assets
- Size positions
- Select investments
- Generate trades
- Interpret news or sentiment
- Override strategy rules

LLMs MAY only be used for:
- Reporting (explaining what happened, not deciding what to do)
- Summaries of trade log output
- Documentation generation

**All investment logic must be deterministic Python code.**

## Strategy Intent

This account is a satellite portfolio. The user maintains separate broad-market exposure elsewhere.

Purpose:
- Evaluate systematic momentum and risk controls
- Provide differentiated exposure from the core portfolio
- Compare against passive benchmarks
- Generate a trade log that can be audited and reproduced

VOO and VTI are benchmark assets only — intentionally excluded from the tradable universe.

## Universe Philosophy

The tradable universe contains exactly one asset per return-driver family:

| Ticker | Family           | Notes                              |
|--------|------------------|------------------------------------|
| QQQ    | GROWTH           | US Growth                          |
| IWM    | SMALL_CAP        | US Small Cap                       |
| SCHD   | DIVIDEND         | US Dividend / Quality              |
| VEA    | DEVELOPED_INTL   | Developed International            |
| VWO    | EMERGING_MARKETS | Emerging Markets                   |
| GLD    | GOLD             | Gold                               |
| DBC    | COMMODITIES      | Broad Commodities (futures-based)  |
| TLT    | LONG_BOND        | Long Treasuries                    |
| SGOV   | CASH             | Cash / Treasury Bills (fallback)   |

SGOV is never a momentum candidate. It is the safety allocation.

Universe changes are governed exactly like parameter changes and require governance review.

## Deterministic Engine Rule

```
same code + same frozen data snapshot = same outputs
```

The engine must be:
1. Fully reproducible given frozen data
2. Testable without any network calls
3. Testable without any LLM
4. Consistent between backtest and live modes

## Governance Rules

### Bug Fix vs. Parameter Change

**Bug Fix** (may be merged after review of the specific fix):
> The implementation does not match the written specification.
> Examples: off-by-one in momentum lookback, wrong SMA window, incorrect stop threshold.

**Parameter Change** (requires governance review before merging):
> The implementation matches the specification, but the specification itself changes.
> Examples: changing lookback from 273 to 252, changing stop threshold from 0.90 to 0.85,
> adding or removing a ticker from the universe, changing correlation threshold.

### Governance Review Windows

Parameter changes must:
1. Be proposed in writing with before/after values
2. Be documented in `docs/specification-v1.md`
3. Have a sensitivity-analysis run to confirm v1 sits on a stable plateau
4. Have at least one positive and one negative test case

### Sensitivity Analysis Rules

Sensitivity analysis may ONLY answer: "Does v1 sit on a stable plateau?"

It MUST NOT auto-select the best-performing configuration. Doing so constitutes data mining
and invalidates the strategy's forward-looking validity.

## Trade-Log-First Principle

Trade-log correctness is more important than performance metrics.

Every trade must be:
- Fully traceable to a signal
- Attributable to a specific rebalance_id
- Reconciled against broker positions
- Reproducible from frozen data

Do not trust metrics until the trade log passes reconciliation.

## Testing Instructions

```bash
# Install dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=src/momentum_agent --cov-report=term-missing

# Run specific test file
pytest tests/test_momentum.py -v
```

All tests must pass without network access. Tests use synthetic price data.

## What Is and Is Not Implemented

### Implemented (v1.0.0)
- Momentum signal: adj_close[t-21] / adj_close[t-273] - 1
- Trend filter: price > SMA200
- Correlation filter: 90-day rolling, threshold 0.70
- Volatility weighting: 60-day, bounds 30-70%
- Selection pipeline: top-2 with family constraint
- Daily stop: 10% drawdown from entry VWAP
- Kill switch: 15% drawdown from high-water mark
- Data integrity checks with failure counter
- Fail-safe liquidation after 5 consecutive failures
- Adversarial simulated broker
- Full reconciliation
- Audit event log

### Not Implemented (future)
- Robinhood integration
- Live trading credentials
- News or sentiment feeds
- Machine learning
- Stock picking
- Options, leverage, or crypto
- Magic Formula
