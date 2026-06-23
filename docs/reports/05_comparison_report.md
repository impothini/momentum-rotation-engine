# Report 05 — Strategy Comparison

**Snapshot ID:** `53bf8444-e746-4d64-b99f-e6b5e8c17215`  
**Strategy Version:** `1.0.0`  
**Momentum run:** `75d30465-b69b-499c-b204-e775f74b9199`  
**Evaluation window:** 2011-10-20 → 2025-12-30  
**Decision rule:** docs/evaluation_spec.md (locked before running)

---

## 1. Primary Metrics

| Metric | Momentum v1 | Static Equal Weight | Static Inverse Vol |
|--------|---|---|---|
| CAGR | 5.34% | 6.53% | 6.00% |
| Max Drawdown | -25.34% | -24.21% | -23.90% |
| Annualized Vol | 12.59% | 11.78% | 10.92% |
| Sharpe Ratio | 0.477 | 0.597 | 0.589 |
| Sortino Ratio | 0.604 | 0.769 | 0.757 |
| Full-period Corr (VOO) | 0.490 | 0.887 | 0.862 |
| Tail Corr (VOO worst 5%) | 0.226 | 0.863 | 0.829 |
| Avg return on VOO worst-5% days | -0.95% | -1.54% | -1.38% |

---

## 2. Decision Rule Application

> From `docs/evaluation_spec.md` (written before running):  
> Momentum v1 is considered dominated only if ALL three conditions hold:  
> 1. A static benchmark achieves strictly higher full-period CAGR  
> 2. The same benchmark achieves equal or better tail correlation  
> 3. The same benchmark achieves equal or better max drawdown  

**Static Equal Weight vs Momentum v1:**

| Condition | Result | Details |
|-----------|--------|---------|
| 1. Higher CAGR | PASS | Static Equal Weight: 6.53% vs Momentum: 5.34% |
| 2. Equal/better tail corr | FAIL | Static Equal Weight: 0.863 vs Momentum: 0.226 |
| 3. Equal/better drawdown | PASS | Static Equal Weight: -24.21% vs Momentum: -25.34% |

**Static Inverse Vol vs Momentum v1:**

| Condition | Result | Details |
|-----------|--------|---------|
| 1. Higher CAGR | PASS | Static Inverse Vol: 6.00% vs Momentum: 5.34% |
| 2. Equal/better tail corr | FAIL | Static Inverse Vol: 0.829 vs Momentum: 0.226 |
| 3. Equal/better drawdown | PASS | Static Inverse Vol: -23.90% vs Momentum: -25.34% |

**VERDICT: Momentum v1 is NOT dominated.**  
It provides differentiated value. The cost of complexity is justified by at least one dimension (tail protection or drawdown control).

---

## 3. Crash-Period Analysis

### 2018 Q4 (Fed tightening)  `2018-10-01 → 2018-12-31`

| Strategy | Total Return | Max Drawdown | Corr to VOO |
|----------|-------------|--------------|-------------|
| Momentum v1 | -13.60% | -13.68% | 0.608 |
| Static Equal Weight | -10.01% | -12.82% | 0.936 |
| Static Inverse Vol | -7.72% | -10.02% | 0.902 |

### 2020 COVID crash  `2020-02-19 → 2020-03-23`

| Strategy | Total Return | Max Drawdown | Corr to VOO |
|----------|-------------|--------------|-------------|
| Momentum v1 | -15.19% | -15.94% | 0.472 |
| Static Equal Weight | -23.51% | -24.21% | 0.935 |
| Static Inverse Vol | -22.74% | -23.90% | 0.911 |

### 2022 Rate Shock  `2022-01-03 → 2022-12-31`

| Strategy | Total Return | Max Drawdown | Corr to VOO |
|----------|-------------|--------------|-------------|
| Momentum v1 | -13.93% | -21.36% | 0.391 |
| Static Equal Weight | -14.92% | -20.42% | 0.900 |
| Static Inverse Vol | -14.57% | -20.73% | 0.862 |

---

## 4. Rolling 3-Year Metrics (percentile summary)

| Metric | Pct | Momentum v1 | Static Equal Weight | Static Inverse Vol |
|--------|-----|---|---|---|
| Rolling CAGR | p25 | 1.82% | 1.99% | 1.90% |
| Rolling CAGR | median | 3.37% | 5.33% | 4.97% |
| Rolling CAGR | p75 | 4.90% | 7.76% | 7.19% |
| Rolling Sharpe | p25 | 0.205 | 0.244 | 0.227 |
| Rolling Sharpe | median | 0.329 | 0.535 | 0.529 |
| Rolling Sharpe | p75 | 0.465 | 0.733 | 0.718 |
| Rolling Max DD | p25 | -19.67% | -24.21% | -23.90% |
| Rolling Max DD | median | -17.49% | -18.96% | -18.25% |
| Rolling Max DD | p75 | -15.96% | -16.28% | -14.22% |

---

## 5. Observation

The question this comparison answers is not **did momentum beat static** but
**did momentum earn the complexity it introduced**. Refer to the full
decision rule in `docs/evaluation_spec.md` before drawing forward-looking
conclusions from these results.
