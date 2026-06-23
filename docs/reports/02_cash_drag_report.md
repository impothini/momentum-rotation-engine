# Report 02 — Cash Drag

**Snapshot ID:** `53bf8444-e746-4d64-b99f-e6b5e8c17215`  
**Strategy Version:** `1.0.0`

> Analysis window: 2008-03-03 → 2025-12-30  
> (4,487 trading days, post-BIL-proxy warmup)

## 1. Average Capital Allocation

| Component | Average % of NAV |
|-----------|-----------------|
| Risk assets (non-SGOV) | 90.5% |
| SGOV (intentional safety) | 9.4% |
| Idle cash | 0.30% |

## 2. Idle Cash Detail

| Metric | Value |
|--------|-------|
| Average idle cash % of NAV | 0.30% |
| Maximum idle cash % of NAV | 2.20% |

## 3. Risk-On / Safety Regime

| State | Days | % of Time |
|-------|------|-----------|
| Any risk asset held (>5% NAV) | 4,174 | 93.0% |
| Fully in SGOV/safety | 313 | 7.0% |

## 4. Interpretation

Idle cash averages 0.30% — negligible. Capital deployment is not the explanation for the 4.5% CAGR.

SGOV averages 9.4% of NAV. This is **intentional** — the strategy's
momentum, trend, and correlation filters rejected all risk assets on 7% of
trading days. The 4.5% CAGR reflects a portfolio that was risk-on 93% of
the time. Construct the blended benchmark: (% risk-on × VOO CAGR) + (% safe × T-bill)
before comparing against any single index.
