# Report 03 — Correlation

**Snapshot ID:** `53bf8444-e746-4d64-b99f-e6b5e8c17215`  
**Strategy Version:** `1.0.0`

## 1. Full-Period Daily Return Correlation

| Pair | Correlation | N days |
|------|-------------|--------|
| Strategy vs VOO | 0.343 | 4,778 |
| Strategy vs VTI | 0.353 | 4,778 |

## 2. Tail Correlation (VOO worst days)

| Window | VOO threshold | N days | Corr (strat/VOO) | Avg VOO ret | Avg Strategy ret |
|--------|--------------|--------|------------------|-------------|-----------------|
| Worst 5%  | -1.85% | 239 | -0.059 | -3.05% | -0.69% |
| Worst 10% | -1.22% | 478 | -0.012 | -2.27% | -0.64% |

## 3. Interpretation

Full-period correlation with VOO is 0.34. The strategy moves materially independently of broad US equities.

**Tail correlation (-0.06 on VOO's worst 5% days) is the satellite portfolio test.**
On VOO's worst 5% days (avg -3.05%), this strategy averaged -0.69%.

Partial downside protection — the strategy loses less than VOO at the tail, but not dramatically.

A 4.5% CAGR with tail correlation near zero is a fundamentally different product than
4.5% CAGR that co-moves with VOO. Evaluate Sharpe contribution to the combined
portfolio, not as a standalone return maximizer.
