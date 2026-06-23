# Report 01 — Window & Proxy

**Snapshot ID:** `53bf8444-e746-4d64-b99f-e6b5e8c17215`  
**Strategy Version:** `1.0.0`  
**Run ID:** `75d30465-b69b-499c-b204-e775f74b9199`

## 1. Tested Window

| | |
|---|---|
| Backtest start | 2007-01-03 |
| Backtest end | 2025-12-30 |
| Total trading days | 4,779 |
| First rebalance signal | 2007-01-31 |
| First execution fill | 2007-06-01 |

The first five months (2007-01-03 → 2007-05-29) hold 100% SGOV because the BIL
proxy for SGOV has no data before 2007-05-30. The momentum strategy's effective
start is **2007-06-01**.

## 2. Proxy Periods by Asset (tradable universe only)

| Asset | Proxy | Proxy Start | Proxy End | Splice Date | Proxy Rows |
|-------|-------|-------------|-----------|-------------|------------|
| QQQ | — | — | — | 2007-01-03 | 0 |
| IWM | — | — | — | 2007-01-03 | 0 |
| SCHD | VIG | 2007-01-03 | 2011-10-19 | 2011-10-20 | 1,210 |
| VEA | EFA | 2007-01-03 | 2007-07-25 | 2007-07-26 | 141 |
| VWO | — | — | — | 2007-01-03 | 0 |
| GLD | — | — | — | 2007-01-03 | 0 |
| DBC | — | — | — | 2007-01-03 | 0 |
| TLT | — | — | — | 2007-01-03 | 0 |
| SGOV | BIL | 2007-05-30 | 2020-05-29 | 2020-06-01 | 3,274 |

## 3. Proxy Coverage Summary

| Metric | Value |
|--------|-------|
| Total universe-asset-days | 43,011 |
| Total proxy-asset-days (tradable) | 4,625 |
| % proxy days across universe | 10.8% |
| Latest proxy end date | 2020-05-29 |
| NAV days with at least one proxy live | 3,375 |
| % NAV history under proxy | 70.6% |

## 4. Interpretation

The SGOV→BIL proxy runs 13.3 years (2007-05-30 → 2020-05-29). SGOV is the cash
fallback, not a momentum candidate, so BIL's T-bill return profile is appropriate.

SCHD→VIG runs 4.8 years (2007-01-03 → 2011-10-19). VIG is a reasonable dividend-
quality proxy. SCHD enters actual data on 2011-10-20.

VEA→EFA is 141 rows (Jan–Jul 2007) on the same MSCI EAFE index. Immaterial.

**Momentum signal quality depends on real data.** QQQ/IWM/VWO/GLD/DBC/TLT have
full real history from 2007. SCHD real signals begin 2011-10-20. The effective
full-universe window is **2011-10-20 → 2025-12-30** (14.2 years).
