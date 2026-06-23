# Momentum v1 Evaluation Specification

**Written:** 2026-06-23  
**Status:** LOCKED — do not amend after comparison scripts run  
**Snapshot:** `53bf8444-e746-4d64-b99f-e6b5e8c17215`  
**Strategy Version:** `1.0.0`

---

## Purpose

This document defines the decision rule that will be applied to the comparison
results. It is written before any comparison numbers are computed. The rule must
not be updated after results are known.

The experiment is not:

> Did momentum beat static?

The experiment is:

> Did momentum earn the complexity it introduced?

These are different questions. A momentum strategy with kill switch, daily stops,
state-machine risk controls, and monthly signal generation is significantly more
complex than a monthly rebalancing rule. Complexity has a cost: implementation
risk, overfitting risk, and regime-specific failure modes. The static benchmarks
measure what a simpler system would have produced under identical conditions.

---

## Fixed Evaluation Window

All strategies must use:

- **Start:** 2011-10-20 (first date SCHD has real data — full universe)
- **End:** 2025-12-30 (last valid trading day in snapshot)
- **Duration:** ~14.2 years
- **Snapshot:** `snap_20260623_001339_0a61575b` (frozen, hash-verified)

Rationale: SCHD is in the tradable universe. Before 2011-10-20 it is represented
by VIG (proxy). Using proxy data for a universe member biases momentum signal
quality in an unquantifiable direction. The evaluation window uses only real data
for all nine tradable assets.

---

## Strategies Under Comparison

All strategies:
- Use the same frozen adj_close and raw_close prices
- Rebalance monthly on the same signal dates (last trading day of month)
- Execute at the following day's open price
- Start with $100,000 initial capital
- Include no transaction costs (equal treatment)

### 1. Momentum v1 (existing)

- Signal: `adj_close[t-21] / adj_close[t-273] - 1`
- Trend filter: price > SMA200
- Correlation filter: 90-day rolling, threshold 0.70
- Volatility weighting: 60-day, bounds 30–70%
- Selection: top-2 with family constraint
- Risk: 10% daily stop, 15% kill switch, SGOV fallback
- Defined in: `src/momentum_agent/`

### 2. Static Equal Weight

- Universe: QQQ, IWM, SCHD, VEA, VWO, GLD, DBC, TLT (8 risk assets — SGOV excluded)
- Weight: 1/8 each asset, every month
- No momentum signal, no trend filter, no stops, no kill switch
- Rebalances to 1/8 on every monthly signal date regardless of market conditions

### 3. Static Inverse Volatility

- Universe: same 8 risk assets
- Weight: `(1/σ_i) / Σ(1/σ_j)` where σ = 60-day trailing daily-return std dev
- If any asset has fewer than 60 days of history at signal date: equal weight for
  that asset, normalized with the rest
- No momentum signal, no trend filter, no stops, no kill switch
- Monthly rebalance

---

## Primary Metrics

All three metrics must be evaluated together. No single metric is sufficient.

| Metric | Definition |
|--------|-----------|
| CAGR | Annualized growth from start to end |
| Max Drawdown | Largest peak-to-trough decline in NAV |
| Tail Correlation | Daily return correlation with VOO on VOO's worst 5% of days |

Secondary (reported but not in the decision rule):
- Annualized volatility
- Sharpe ratio (annualized, risk-free = 0)
- Sortino ratio

---

## Rolling Analysis

For each strategy, generate 3-year rolling windows (756 trading days) stepping
monthly. Report:
- Rolling CAGR
- Rolling Sharpe
- Rolling max drawdown within window

The rolling analysis exists to detect regime-specific performance. A strategy
that wins the full period but only performs in one regime (e.g., 2020–2022
flight-to-safety) is not the same as one that wins consistently.

---

## Crash-Period Analysis

Explicitly isolate three market stress periods:

| Period | Window | Characterization |
|--------|--------|-----------------|
| 2018 Q4 | 2018-10-01 → 2018-12-31 | Fed tightening, equity selloff |
| 2020 COVID | 2020-02-19 → 2020-03-23 | Sharp crash, fastest -30% on record |
| 2022 Rate Shock | 2022-01-03 → 2022-12-31 | Simultaneous equity + bond drawdown |

For each period report: total return, max drawdown within period, and daily
return correlation with VOO.

---

## Decision Rule

**Momentum v1 is considered dominated only if ALL three conditions hold:**

1. A static benchmark achieves strictly higher full-period CAGR
2. The same static benchmark achieves equal or better tail correlation
   (correlation with VOO on worst-5% days ≥ momentum's tail correlation)
3. The same static benchmark achieves equal or better max drawdown
   (shallower peak-to-trough decline)

All three conditions must be met by the same single benchmark. If condition 1
holds but conditions 2 or 3 do not, momentum is providing differentiated value
that the CAGR comparison does not capture.

**Momentum v1 retains its value if:**
- It achieves meaningfully better tail correlation than static (the crash
  protection case), even if CAGR is lower
- It achieves meaningfully better max drawdown, even if CAGR is lower
- The rolling analysis shows it avoids regime-specific crashes that static
  portfolios absorb

**The strategy is not evaluated on whether it maximizes CAGR.** It was designed
to provide differentiated exposure with explicit downside controls. Evaluate it
for what it was designed to do.

---

## What a "Pass" Looks Like

If the decision rule above results in "not dominated," the conclusion is:

> Momentum v1 provides differentiated value relative to static allocation.
> The cost of complexity is justified by [specific metric that differs].

If the decision rule results in "dominated," the conclusion is:

> Static [equal-weight / inverse-vol] achieves the same risk profile at lower
> complexity. Momentum v1 does not earn its implementation cost.
> Recommendation: retire v1 or redesign the signal.

Either outcome is valid. The point of the evaluation is to know which it is.

---

## Anti-Gaming Rules

1. The decision rule above must not be changed after running the comparison.
2. The evaluation window (2011-10-20 → 2025-12-30) must not be changed to
   improve any strategy's appearance.
3. Rolling and crash-period results are reported regardless of whether they
   favor momentum.
4. If a new variant of momentum is proposed based on these results, it must
   be re-frozen as a new snapshot and evaluated from scratch. Results from
   this evaluation do not validate any future variant.
