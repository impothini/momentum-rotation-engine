# Core + Sleeve Blending Report

**Snapshot ID:** `53bf8444-e746-4d64-b99f-e6b5e8c17215`  
**Momentum run:** `75d30465-b69b-499c-b204-e775f74b9199`  
**Evaluation window:** 2011-10-20 → 2025-12-30  
**Specification:** docs/research/core_plus_sleeve.md (locked before running)

---

## 1. Primary Metrics

| Allocation | CAGR | Max DD | Vol | Sharpe | Sortino | Tail Corr | Avg worst-5% day |
|------------|------|--------|-----|--------|---------|-----------|-----------------|
| 100% VOO | 14.90% | -33.99% | 16.89% | 0.909 | 1.117 | 1.000 | -2.55% |
| 95/5 | 14.46% | -33.08% | 16.36% | 0.910 | 1.121 | 0.999 | -2.47% |
| 90/10 | 14.02% | -32.17% | 15.85% | 0.910 | 1.121 | 0.994 | -2.39% |
| 80/20 | 13.13% | -30.33% | 14.91% | 0.905 | 1.121 | 0.973 | -2.23% |
| 70/30 | 12.22% | -28.47% | 14.06% | 0.893 | 1.109 | 0.930 | -2.07% |
| 100% Momentum | 5.34% | -25.34% | 12.59% | 0.477 | 0.604 | 0.226 | -0.95% |
| 100% VTI | 14.67% | -35.00% | 17.17% | 0.886 | 1.093 | 0.995 | -2.58% |

---

## 2. Worst-Period Analysis

| Allocation | Worst Calendar Year | Return | Worst Rolling 12M |
|------------|---------------------|--------|-------------------|
| 100% VOO | 2022 | -18.17% | -19.73% |
| 95/5 | 2022 | -17.84% | -19.35% |
| 90/10 | 2022 | -17.52% | -18.98% |
| 80/20 | 2022 | -16.90% | -18.27% |
| 70/30 | 2022 | -16.32% | -17.60% |
| 100% Momentum | 2022 | -13.40% | -21.06% |
| 100% VTI | 2022 | -19.52% | -21.36% |

---

## 3. Improvement Threshold Check (vs 100% VOO)

Thresholds from spec (written before running):  
- CAGR reduction ≤ 0.5%  
- Max drawdown improvement ≥ 1.0%  
- Tail correlation improvement ≥ 0.03 (absolute)  

**95/5:** **MIXED** — CAGR delta -0.43% (ok)  DD delta +0.91% (fail)  Tail delta +0.001 (fail)  
**90/10:** **MIXED** — CAGR delta -0.87% (fail)  DD delta +1.82% (ok)  Tail delta +0.006 (fail)  
**80/20:** **DRAG** — CAGR delta -1.76% (fail)  DD delta +3.67% (ok)  Tail delta +0.027 (fail)  
**70/30:** **DRAG** — CAGR delta -2.68% (fail)  DD delta +5.52% (ok)  Tail delta +0.070 (ok)  
**100% Momentum:** **DRAG** — CAGR delta -9.56% (fail)  DD delta +8.66% (ok)  Tail delta +0.774 (ok)  

---

## 4. Observation

This experiment answers one question: what does the Momentum v1 sleeve do to a
VOO core portfolio over the backtested window? It does not imply an optimal
allocation, predict forward returns, or account for tax or transaction costs.

A result of USEFUL means the trade-off existed historically. It does not mean
it will persist. Refer to `docs/research/core_plus_sleeve.md` for the full
context and limitations before drawing forward-looking conclusions.
