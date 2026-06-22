# Strategy Specification — Momentum Rotation Engine v1.0.0

**Version:** 1.0.0  
**Status:** Active  
**Governance:** Parameter changes require review before merging (see CLAUDE.md)

---

## Purpose

This is a satellite portfolio strategy designed to:
- Evaluate systematic momentum and risk controls
- Provide differentiated exposure from a core broad-market portfolio
- Compare against passive benchmarks
- Generate an auditable, reproducible trade log

---

## Tradable Universe

| Ticker | Family           | Description                        |
|--------|------------------|------------------------------------|
| QQQ    | GROWTH           | US Growth                          |
| IWM    | SMALL_CAP        | US Small Cap                       |
| SCHD   | DIVIDEND         | US Dividend / Quality              |
| VEA    | DEVELOPED_INTL   | Developed International            |
| VWO    | EMERGING_MARKETS | Emerging Markets                   |
| GLD    | GOLD             | Gold                               |
| DBC    | COMMODITIES      | Broad Commodities (futures-based)  |
| TLT    | LONG_BOND        | Long Treasuries                    |
| SGOV   | CASH             | Cash / Treasury Bills              |

**SGOV** is never a momentum candidate. It is the safety allocation.

**VOO and VTI** are benchmarks only — intentionally excluded from the tradable universe.

Only one asset per family may be selected at any time.

---

## Benchmarks

- VOO
- VTI
- QQQ
- 60/40 Portfolio: 60% VOO / 40% AGG, rebalanced monthly
- SGOV (risk-free rate proxy)

---

## Data Conventions

| Use Case                    | Price Series        |
|-----------------------------|---------------------|
| Momentum signal             | Adjusted close      |
| Trend filter (SMA200)       | Adjusted close      |
| Correlation calculation     | Adjusted close      |
| Volatility calculation      | Adjusted close      |
| Benchmark calculation       | Adjusted close      |
| Portfolio valuation (NAV)   | Raw close           |
| Stop checks                 | Raw close           |
| Kill-switch checks          | Raw close           |
| Order fills                 | Raw open            |

Do not mix signal-generation prices and valuation prices.

---

## Momentum Formula

```
momentum = adj_close[t - 21] / adj_close[t - 273] - 1
```

- t = signal date (last trading day of month)
- t-21 = near price (excludes most recent month to avoid noise)
- t-273 = far price (approximately 12-month lookback)
- Require at least 273 trading days of history before first signal

---

## Trend Filter

Asset qualifies only if:

```
adj_close[t] > SMA200[t]
```

SMA200 uses adjusted closes.

---

## Correlation Filter

- Window: 90 trading days
- Price series: adjusted-close returns
- Constraint: correlation ≤ 0.70 for the second selected asset
- Both assets must be from different families

---

## Volatility Weighting

Applies when TWO risk assets qualify.

- Window: 60 trading days
- Price series: adjusted-close returns
- Weight bounds: [30%, 70%]
- Formula: inverse-volatility weighting, clipped to bounds

---

## Selection Pipeline (exact order)

1. Exclude SGOV from candidates.
2. Validate data (require lookback_days + 1 history).
3. Apply trend filter.
4. If zero assets qualify → 100% SGOV.
5. Rank qualifying assets by momentum.
6. Select highest-ranked asset.
7. Search for second asset: different family + correlation ≤ 0.70.
8. If second exists → inverse-vol weighting.
9. If only one qualifies → 70% asset + 30% SGOV.
10. If second not found → 70% asset + 30% SGOV.

---

## Rebalance Timing

- Signal date: last trading day of month (close prices)
- Execution: next trading session open
- Calendar: NYSE via pandas-market-calendars

---

## Fractional Shares

```
target_shares = target_dollar_value / execution_open_price
```

Round to 6 decimal places. Residual cash remains cash.

---

## Entry Price Logic

Track: `entry_vwap_fill_price`, `entry_timestamp`, `entry_rebalance_id`

When resizing a position, update VWAP:
```
new_vwap = (existing_shares * old_vwap + new_shares * fill_price) / total_shares
```

---

## Daily Stop Rule

**Trigger:** `raw_close[t] <= entry_vwap_fill_price * 0.90`

**Actions:**
1. Emit STOP_TRIGGER
2. Exit next session open
3. Emit STOP_EXIT after fill
4. Route proceeds to SGOV

**Lockout:** Asset excluded from current rebalance and cannot be repurchased until the following monthly rebalance.

SGOV is exempt from stop checks.

---

## Stop / Rebalance Collision

If a held asset triggers a stop on a rebalance signal date:
- Add asset to lockout set
- Exclude from same rebalance selection
- Generate ONE net order batch
- Do not execute separate stop and rebalance orders for same asset

---

## Kill Switch

**High-water mark:** Since strategy inception.

**Trigger:** `NAV <= HWM * 0.85`

**Action:**
- Liquidate all positions
- Move to SGOV
- Emit KILL_SWITCH

**Resume:**
- Wait until next monthly rebalance
- Resume normal strategy
- Reset HWM = current NAV on resume date

---

## Data Integrity

**Conditions:** Missing price, null price, stale price, missing indicator input, invalid data.

**Actions:**
- Emit DATA_INTEGRITY_FAILURE
- Freeze current allocation
- Skip signal generation
- Skip rebalance

No interpolation. No forward-fill. No inferred prices.

---

## Partial NAV Rule

If ANY held position lacks a valid raw close:
- NAV = INVALID
- Kill switch cannot be evaluated
- Emit DATA_INTEGRITY_FAILURE

When NAV invalid: `nav = null`, `high_water_mark = null`, `drawdown_pct = null`

---

## Failure Counter

- Valid bar: `failure_count = 0`
- Missing/stale bar: `failure_count += 1`

---

## Fail-Safe Liquidation

If `failure_count >= 5` for a held position:
- Emit FAILSAFE_LIQUIDATION
- Liquidate next open
- Route proceeds to SGOV

---

## Reconciliation

Run after every execution.

**Tolerances:**
- share_tolerance = 0.0001
- weight_tolerance = 0.005
- cash_tolerance = 1.00

Reconcile against post-rounding target shares. Expected residual cash from rounding is valid.

Emit RECONCILIATION_FAILURE if tolerance exceeded.

---

## Unauthorized Position Changes

Broker positions are the source of truth.

If positions change without engine-generated orders → emit UNAUTHORIZED_POSITION_CHANGE.

---

## Output Files

| File               | Description                           |
|--------------------|---------------------------------------|
| trade_log.csv      | Sparse event log (one row per event)  |
| daily_nav.csv      | One row per trading day               |
| metrics.json       | Risk/return metrics                   |
| run_metadata.json  | Run provenance, snapshot IDs          |

---

## Sensitivity Analysis Policy

Pre-committed v1 parameters:
- Top 2 candidates
- 12-month momentum excluding recent 21 days
- 200-day trend filter
- Correlation filter ON
- Daily stop ON
- Inverse-vol weighting ON

Sensitivity analysis may ONLY answer: "Does v1 sit on a stable plateau?"

It MUST NOT auto-select the best-performing configuration.

---

## Inception Dates and Proxies

| Ticker | Inception Date | Default Proxy |
|--------|---------------|---------------|
| QQQ    | 1999-03-10    | —             |
| IWM    | 2000-05-22    | —             |
| SCHD   | 2011-10-20    | VIG           |
| VEA    | 2007-07-26    | —             |
| VWO    | 2005-03-10    | —             |
| GLD    | 2004-11-18    | —             |
| DBC    | 2006-02-03    | —             |
| TLT    | 2002-07-30    | —             |
| SGOV   | 2023-10-26    | BIL           |

Engine refuses backtests that start before an asset's inception date unless a proxy is configured.

---

## Change Log

| Version | Date       | Change Type     | Description                    |
|---------|------------|-----------------|--------------------------------|
| 1.0.0   | 2026-06-22 | Initial release | Momentum strategy implemented  |
