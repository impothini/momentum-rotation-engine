# Report 04 — Exposure

**Snapshot ID:** `53bf8444-e746-4d64-b99f-e6b5e8c17215`  
**Strategy Version:** `1.0.0`

> Analysis window: 2008-03-03 → 2025-12-30  
> (4,487 trading days)

## 1. Average Weight by Asset

| Asset | Avg weight (all days) | Avg weight (days held) | Days held | % held |
|-------|----------------------|------------------------|-----------|--------|
| QQQ | 21.7% | 48.4% | 2,012 | 44.8% |
| GLD | 20.9% | 52.6% | 1,784 | 39.8% |
| TLT | 13.5% | 53.0% | 1,142 | 25.5% |
| SGOV | 9.4% | 62.5% | 672 | 15.0% |
| DBC | 8.5% | 50.7% | 752 | 16.8% |
| IWM | 7.7% | 49.6% | 697 | 15.5% |
| SCHD | 7.6% | 54.9% | 620 | 13.8% |
| VWO | 6.7% | 44.6% | 670 | 14.9% |
| VEA | 4.0% | 57.4% | 312 | 7.0% |
| [idle cash] | 0.3% | 0.5% | 2,457 | 54.8% |

## 2. Regime Days

| Regime | Days | % of Time |
|--------|------|-----------|
| Full risk-on (≥60% non-SGOV) | 4,174 | 93.0% |
| Partial risk-on (10–60% non-SGOV) | 0 | 0.0% |
| Full safety (SGOV ≥90%) | 313 | 7.0% |

## 3. Interpretation

The average weight table is the clearest single explanation of the return profile.
Any asset averaging >10% over 18 years is a material driver.

The regime table answers the deployment question directly:
- **Full risk-on**: strategy is fully deployed as designed
- **Partial risk-on**: one asset selected with SGOV complement, or a stop partially exited
- **Full safety**: momentum/trend/correlation filters found no qualifying asset

**Next step:** construct a blended passive benchmark:
```
  blended_cagr = (% risk-on × VOO_CAGR) + (% safe × T-bill_rate)
```
That delta — strategy CAGR minus blended benchmark — is the true alpha measure.
