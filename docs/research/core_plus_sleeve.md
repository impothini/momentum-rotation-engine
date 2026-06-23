# Research: Core + Sleeve Portfolio Blending

**Written:** 2026-06-23  
**Status:** LOCKED — do not amend after blending scripts run  
**Snapshot:** `53bf8444-e746-4d64-b99f-e6b5e8c17215`  
**Depends on:** docs/reports/05_comparison_report.md

---

## Purpose

The strategy comparison (Report 05) established that Momentum v1 is not dominated
by static alternatives. It sacrifices approximately 1pp of CAGR to obtain
dramatically lower tail correlation with broad equities:

| Metric | Momentum v1 | Static Equal Weight |
|--------|-------------|---------------------|
| CAGR | 5.34% | 6.53% |
| Tail Corr (VOO worst 5%) | 0.226 | 0.863 |
| 2020 COVID drawdown | -15.2% | -23.5% |

The remaining question is not whether Momentum v1 is a good standalone portfolio.
It is whether Momentum v1 improves a portfolio it is paired with.

This experiment evaluates the strategy as a satellite sleeve paired with a VOO core.

---

## Hypothesis

A small allocation to Momentum v1 (5–20%) will:

1. Not materially reduce full-period CAGR (within 0.5pp of 100% VOO)
2. Meaningfully reduce maximum drawdown (> 1pp improvement)
3. Meaningfully reduce tail correlation with broad equities (> 0.03 improvement)
4. Improve or not worsen the worst calendar year
5. Improve or not worsen the worst rolling 12-month return

If all five hold for any allocation: the sleeve adds value to the core portfolio.
If none hold: the sleeve is pure drag.

---

## Evaluation Window

Same as the strategy comparison: **2011-10-20 → 2025-12-30** (14.2 years).

Rationale: SCHD real data starts 2011-10-20; the sleeve strategy uses real signals
from this date. Both core and sleeve are normalized to $100,000 on this date.

---

## Portfolio Allocations to Test

| Label | VOO weight | Momentum v1 weight |
|-------|-----------|-------------------|
| 100% VOO | 100% | 0% |
| 95/5 | 95% | 5% |
| 90/10 | 90% | 10% |
| 80/20 | 80% | 20% |
| 70/30 | 70% | 30% |
| 100% Momentum | 0% | 100% |

100% VTI is included as a second passive benchmark (not a blend, just reference).
100% Momentum is included as a reference endpoint, not as a proposed allocation.

---

## Rebalancing Assumption

Daily rebalancing to target weights. Implemented as:

```
blend_ret[t] = core_pct × voo_ret[t] + sleeve_pct × mom_ret[t]
```

This is equivalent to continuously rebalancing back to the target allocation.
It is the standard approach for portfolio-level return attribution.

Monthly rebalancing would produce slightly different results at the extremes but
the qualitative shape of the trade-off curve is robust to this choice.

---

## Metrics

All metrics computed over the full evaluation window (2011-10-20 → 2025-12-30):

| Metric | Definition |
|--------|-----------|
| CAGR | Annualized growth rate |
| Max Drawdown | Largest peak-to-trough decline |
| Sharpe Ratio | Annualized, rf = 0 |
| Tail Correlation | Correlation with VOO on VOO's worst 5% of days |
| Worst Calendar Year | Lowest annual return (Jan–Dec) |
| Worst Rolling 12-Month | Lowest return in any 252-trading-day window |

---

## Improvement Thresholds

These are written before results are known. They are guidelines for interpreting the
trade-off curve, not hard gates.

An allocation is considered to offer a useful trade-off if:

- CAGR reduction vs 100% VOO is ≤ 0.5pp
- Max drawdown improvement vs 100% VOO is ≥ 1pp  
- Tail correlation improvement vs 100% VOO is ≥ 0.03 (absolute)

"Useful" does not mean optimal. It means the sleeve is not pure performance drag.
Whether to act on it depends on the investor's specific drawdown tolerance.

---

## Anti-Gaming Rules

1. Thresholds above are written before results are known — do not revise them.
2. The evaluation window is fixed — do not shift it to improve any allocation's appearance.
3. New allocations (e.g., 85/15) may not be added after results are seen.
4. All allocations are reported regardless of outcome — no cherry-picking.
5. If the best result is at an extreme (e.g., 70/30 dominates all others),
   that is reported as-is. Do not conclude the optimal allocation is at that extreme —
   that would be in-sample optimization.

---

## What This Does Not Test

- Tax efficiency of rebalancing
- Transaction costs of maintaining the sleeve
- Correlation stability across future regimes
- Whether the sleeve CAGR is explained by known factor premia (momentum premium)
- Forward-looking expected return for the sleeve

This experiment only asks: **given the backtested behavior of the sleeve, what
would it have done to a VOO core over this specific historical window?**

That is a necessary but not sufficient condition for live deployment.
