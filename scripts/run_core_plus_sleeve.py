"""Core + Sleeve portfolio blending experiment.

Blends VOO (core) with Momentum v1 (sleeve) at various allocation ratios.
Uses daily-return blending (equivalent to continuous rebalancing).

Specification: docs/research/core_plus_sleeve.md

Usage:
    python scripts/run_core_plus_sleeve.py \
        --snapshot data/snapshots/<snap_dir> \
        --momentum-run data/runs/<run_id> \
        [--out docs/research]

Outputs:
    <out>/blend_nav.csv           daily NAV for all blends + benchmarks
    <out>/blend_metrics.json      all metrics per allocation
    <out>/blend_worst_years.csv   worst calendar year per allocation
    <out>/blend_rolling12.csv     worst rolling 12-month per allocation
    <out>/core_plus_sleeve_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from momentum_agent.data.loader import MarketDataLoader
from momentum_agent.reporting.metrics import (
    calculate_annualized_vol,
    calculate_cagr,
    calculate_max_drawdown,
    calculate_sharpe,
    calculate_sortino,
    daily_returns,
)

# ---------------------------------------------------------------------------
# Locked evaluation constants
# ---------------------------------------------------------------------------

EVAL_START = date(2011, 10, 20)
EVAL_END = date(2025, 12, 30)
INITIAL_CAPITAL = 100_000.0
VOO_TAIL_PCT = 0.05
ROLLING_12M = 252       # trading days ≈ 12 months
IMPROVEMENT_CAGR_THRESHOLD = 0.005        # 0.5pp
IMPROVEMENT_MDD_THRESHOLD = 0.010         # 1pp
IMPROVEMENT_TAIL_CORR_THRESHOLD = 0.03    # absolute

BLENDS = [
    ("100% VOO",       1.00, 0.00),
    ("95/5",           0.95, 0.05),
    ("90/10",          0.90, 0.10),
    ("80/20",          0.80, 0.20),
    ("70/30",          0.70, 0.30),
    ("100% Momentum",  0.00, 1.00),
]

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _load_momentum_nav(run_dir: Path) -> pd.Series:
    path = run_dir / "daily_nav.csv"
    df = pd.read_csv(path, usecols=["date", "nav"]).dropna(subset=["nav"])
    return pd.Series(
        df["nav"].values.astype(float),
        index=pd.to_datetime(df["date"]),
        name="momentum_v1",
    ).sort_index()


def _normalize_to_start(nav: pd.Series, start: date, capital: float) -> pd.Series:
    start_ts = pd.Timestamp(start)
    if start_ts not in nav.index:
        after = nav.index[nav.index >= start_ts]
        if len(after) == 0:
            raise ValueError(f"No data on or after {start}")
        start_ts = after[0]
    base = float(nav.loc[start_ts])
    if base <= 0:
        raise ValueError(f"NAV at {start_ts} is {base}")
    return (nav / base * capital).rename(nav.name)


def _build_blend(
    voo_rets: pd.Series, mom_rets: pd.Series, core_pct: float, sleeve_pct: float
) -> pd.Series:
    """Build a blended NAV series from daily returns using continuous rebalancing."""
    aligned = pd.concat([voo_rets.rename("voo"), mom_rets.rename("mom")], axis=1, sort=False).dropna()
    blend_rets = core_pct * aligned["voo"] + sleeve_pct * aligned["mom"]
    nav = INITIAL_CAPITAL * (1 + blend_rets).cumprod()
    return nav


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _tail_correlation(
    strat_rets: pd.Series, bench_rets: pd.Series, pct: float = VOO_TAIL_PCT
) -> tuple[float, float, float]:
    aligned = pd.concat([strat_rets, bench_rets], axis=1, sort=False).dropna()
    aligned.columns = ["strat", "bench"]
    n_tail = max(1, int(len(aligned) * pct))
    tail = aligned.nsmallest(n_tail, "bench")
    if len(tail) < 5:
        return float("nan"), float("nan"), float("nan")
    corr = float(tail["strat"].corr(tail["bench"]))
    return corr, float(tail["strat"].mean()), float(tail["bench"].mean())


def _worst_calendar_year(nav: pd.Series) -> tuple[int, float]:
    """Return (year, annual_return) for the worst calendar year."""
    rets = daily_returns(nav)
    annual = rets.groupby(rets.index.year).apply(lambda r: float((1 + r).prod() - 1))
    if annual.empty:
        return 0, float("nan")
    worst_year = int(annual.idxmin())
    return worst_year, float(annual.min())


def _worst_rolling_12m(nav: pd.Series, window: int = ROLLING_12M) -> float:
    """Lowest return in any rolling window-day period."""
    if len(nav) < window + 1:
        return float("nan")
    rolling_ret = nav.pct_change(window).dropna()
    return float(rolling_ret.min())


def _compute_blend_metrics(
    nav: pd.Series, voo_rets: pd.Series, label: str
) -> dict:
    rets = daily_returns(nav)
    tail_corr, avg_strat_tail, avg_voo_tail = _tail_correlation(rets, voo_rets)
    aligned = pd.concat([rets, voo_rets], axis=1, sort=False).dropna()
    full_corr = (
        float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
        if len(aligned) > 1 else float("nan")
    )
    worst_year, worst_year_ret = _worst_calendar_year(nav)
    worst_rolling = _worst_rolling_12m(nav)

    return {
        "label": label,
        "cagr": calculate_cagr(nav),
        "max_drawdown": calculate_max_drawdown(nav),
        "annualized_vol": calculate_annualized_vol(nav),
        "sharpe": calculate_sharpe(nav),
        "sortino": calculate_sortino(nav),
        "full_corr_voo": full_corr,
        "tail_corr_voo": tail_corr,
        "avg_ret_on_voo_worst5pct_days": avg_strat_tail,
        "avg_voo_ret_on_worst5pct_days": avg_voo_tail,
        "worst_calendar_year": worst_year,
        "worst_calendar_year_return": worst_year_ret,
        "worst_rolling_12m_return": worst_rolling,
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _pct(v: Optional[float], d: int = 2) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return f"{v * 100:.{d}f}%"


def _f(v: Optional[float], d: int = 3) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return f"{v:.{d}f}"


def _check_improvement(blend_m: dict, base_m: dict) -> str:
    """Check the three improvement thresholds against 100% VOO."""
    cagr_delta = (blend_m["cagr"] or 0) - (base_m["cagr"] or 0)
    mdd_delta = (blend_m["max_drawdown"] or 0) - (base_m["max_drawdown"] or 0)
    tail_delta = (base_m["tail_corr_voo"]) - (blend_m["tail_corr_voo"])

    c1 = cagr_delta >= -IMPROVEMENT_CAGR_THRESHOLD
    c2 = mdd_delta >= IMPROVEMENT_MDD_THRESHOLD
    c3 = tail_delta >= IMPROVEMENT_TAIL_CORR_THRESHOLD

    status = "USEFUL" if (c1 and c2 and c3) else "DRAG" if (cagr_delta < -IMPROVEMENT_CAGR_THRESHOLD * 3) else "MIXED"
    details = (
        f"CAGR delta {cagr_delta:+.2%} ({'ok' if c1 else 'fail'})  "
        f"DD delta {mdd_delta:+.2%} ({'ok' if c2 else 'fail'})  "
        f"Tail delta {tail_delta:+.3f} ({'ok' if c3 else 'fail'})"
    )
    return f"**{status}** — {details}"


def _write_report(
    report_path: Path,
    metrics: dict[str, dict],
    blend_order: list[str],
    benchmark_keys: list[str],
    snapshot_id: str,
    momentum_run_id: str,
) -> None:
    all_keys = blend_order + [k for k in benchmark_keys if k not in blend_order]
    all_labels = [metrics[k]["label"] for k in all_keys]

    lines = [
        "# Core + Sleeve Blending Report",
        "",
        f"**Snapshot ID:** `{snapshot_id}`  ",
        f"**Momentum run:** `{momentum_run_id}`  ",
        f"**Evaluation window:** {EVAL_START} → {EVAL_END}  ",
        "**Specification:** docs/research/core_plus_sleeve.md (locked before running)",
        "",
        "---",
        "",
        "## 1. Primary Metrics",
        "",
        "| Allocation | CAGR | Max DD | Vol | Sharpe | Sortino | Tail Corr | Avg worst-5% day |",
        "|------------|------|--------|-----|--------|---------|-----------|-----------------|",
    ]

    for k in all_keys:
        m = metrics[k]
        lines.append(
            f"| {m['label']} | "
            f"{_pct(m['cagr'])} | "
            f"{_pct(m['max_drawdown'])} | "
            f"{_pct(m['annualized_vol'])} | "
            f"{_f(m['sharpe'])} | "
            f"{_f(m['sortino'])} | "
            f"{_f(m['tail_corr_voo'])} | "
            f"{_pct(m['avg_ret_on_voo_worst5pct_days'])} |"
        )

    # Worst-year table
    lines += [
        "",
        "---",
        "",
        "## 2. Worst-Period Analysis",
        "",
        "| Allocation | Worst Calendar Year | Return | Worst Rolling 12M |",
        "|------------|---------------------|--------|-------------------|",
    ]
    for k in all_keys:
        m = metrics[k]
        wy = m["worst_calendar_year"]
        wyr = _pct(m["worst_calendar_year_return"])
        wr12 = _pct(m["worst_rolling_12m_return"])
        lines.append(f"| {m['label']} | {wy} | {wyr} | {wr12} |")

    # Improvement thresholds
    base_key = "100_pct_voo"
    if base_key in metrics:
        lines += [
            "",
            "---",
            "",
            "## 3. Improvement Threshold Check (vs 100% VOO)",
            "",
            "Thresholds from spec (written before running):  ",
            f"- CAGR reduction ≤ {IMPROVEMENT_CAGR_THRESHOLD:.1%}  ",
            f"- Max drawdown improvement ≥ {IMPROVEMENT_MDD_THRESHOLD:.1%}  ",
            f"- Tail correlation improvement ≥ {IMPROVEMENT_TAIL_CORR_THRESHOLD:.2f} (absolute)  ",
            "",
        ]
        for k in blend_order:
            if k == base_key:
                continue
            label = metrics[k]["label"]
            result = _check_improvement(metrics[k], metrics[base_key])
            lines.append(f"**{label}:** {result}  ")
        lines.append("")

    lines += [
        "---",
        "",
        "## 4. Observation",
        "",
        "This experiment answers one question: what does the Momentum v1 sleeve do to a",
        "VOO core portfolio over the backtested window? It does not imply an optimal",
        "allocation, predict forward returns, or account for tax or transaction costs.",
        "",
        "A result of USEFUL means the trade-off existed historically. It does not mean",
        "it will persist. Refer to `docs/research/core_plus_sleeve.md` for the full",
        "context and limitations before drawing forward-looking conclusions.",
    ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python scripts/run_core_plus_sleeve.py",
        description="Core + Sleeve blending experiment.",
    )
    p.add_argument("--snapshot", required=True, metavar="PATH",
                   help="Frozen snapshot directory.")
    p.add_argument("--momentum-run", required=True, metavar="PATH",
                   help="Momentum v1 run directory (contains daily_nav.csv).")
    p.add_argument("--out", default="docs/research", metavar="PATH",
                   help="Output directory. Default: docs/research/")
    p.add_argument("--no-verify-hash", action="store_true", default=False)
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Load snapshot ----
    print(f"Loading snapshot : {args.snapshot}")
    loader = MarketDataLoader.from_snapshot(
        Path(args.snapshot), verify_hash=not args.no_verify_hash
    )
    adj_close = loader.adj_close
    snap = loader.snapshot
    print(f"  snapshot_id    : {snap.snapshot_id}")

    # ---- Load momentum NAV ----
    momentum_run_dir = Path(args.momentum_run)
    print(f"Loading momentum v1 run : {momentum_run_dir}")
    mom_nav_full = _load_momentum_nav(momentum_run_dir)

    # ---- Build normalized series on evaluation window ----
    eval_start_ts = pd.Timestamp(EVAL_START)
    eval_end_ts = pd.Timestamp(EVAL_END)

    for ticker in ("VOO", "VTI"):
        if ticker not in adj_close.columns:
            sys.exit(f"Error: {ticker} not found in snapshot. Add it to --tickers when freezing.")

    voo_full = adj_close["VOO"].dropna()
    vti_full = adj_close["VTI"].dropna()

    voo_nav = _normalize_to_start(voo_full, EVAL_START, INITIAL_CAPITAL).loc[eval_start_ts:eval_end_ts]
    vti_nav = _normalize_to_start(vti_full, EVAL_START, INITIAL_CAPITAL).loc[eval_start_ts:eval_end_ts]
    mom_nav = _normalize_to_start(mom_nav_full, EVAL_START, INITIAL_CAPITAL).loc[eval_start_ts:eval_end_ts]

    voo_rets = daily_returns(voo_nav)
    mom_rets = daily_returns(mom_nav)

    print(f"  VOO eval rows     : {len(voo_nav)}")
    print(f"  VTI eval rows     : {len(vti_nav)}")
    print(f"  Momentum eval rows: {len(mom_nav)}")

    # ---- Build blend NAV series ----
    print("\nBuilding blended portfolios...")
    blend_navs: dict[str, pd.Series] = {}
    for label, core_pct, sleeve_pct in BLENDS:
        key = label.lower().replace("% ", "_pct_").replace("/", "_").replace(" ", "_")
        nav = _build_blend(voo_rets, mom_rets, core_pct, sleeve_pct)
        nav.name = label
        blend_navs[key] = nav
        print(f"  {label:<20} final NAV: ${float(nav.iloc[-1]):>10,.2f}")

    vti_key = "100_pct_vti"
    blend_navs[vti_key] = vti_nav
    blend_navs[vti_key].name = "100% VTI"
    print(f"  {'100% VTI':<20} final NAV: ${float(vti_nav.iloc[-1]):>10,.2f}")

    # ---- Write NAV CSV ----
    nav_df = pd.concat(
        [nav.rename(key) for key, nav in blend_navs.items()],
        axis=1, sort=False,
    )
    nav_df.index.name = "date"
    nav_csv = out_dir / "blend_nav.csv"
    nav_df.to_csv(nav_csv, float_format="%.4f")
    print(f"\nWrote : {nav_csv}")

    # ---- Compute metrics ----
    print("\nComputing metrics...")
    metrics: dict[str, dict] = {}
    blend_order = []
    for label, core_pct, sleeve_pct in BLENDS:
        key = label.lower().replace("% ", "_pct_").replace("/", "_").replace(" ", "_")
        m = _compute_blend_metrics(blend_navs[key], voo_rets, label)
        metrics[key] = m
        blend_order.append(key)

    metrics[vti_key] = _compute_blend_metrics(vti_nav, voo_rets, "100% VTI")

    metrics_path = out_dir / "blend_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(
            metrics, f, indent=2,
            default=lambda x: None if (isinstance(x, float) and np.isnan(x)) else x,
        )
    print(f"Wrote : {metrics_path}")

    # ---- Worst-year CSV ----
    wy_rows = []
    for k in blend_order + [vti_key]:
        m = metrics[k]
        wy_rows.append({
            "label": m["label"],
            "worst_calendar_year": m["worst_calendar_year"],
            "worst_calendar_year_return": m["worst_calendar_year_return"],
            "worst_rolling_12m_return": m["worst_rolling_12m_return"],
        })
    pd.DataFrame(wy_rows).to_csv(out_dir / "blend_worst_periods.csv", index=False)
    print(f"Wrote : {out_dir / 'blend_worst_periods.csv'}")

    # ---- Write report ----
    report_path = out_dir / "core_plus_sleeve_report.md"
    _write_report(
        report_path=report_path,
        metrics=metrics,
        blend_order=blend_order,
        benchmark_keys=[vti_key],
        snapshot_id=snap.snapshot_id,
        momentum_run_id=momentum_run_dir.name,
    )
    print(f"Wrote : {report_path}")

    # ---- Summary table ----
    print("\n" + "=" * 72)
    print("BLEND SUMMARY")
    print("=" * 72)
    fmt = "{:<20} {:>8} {:>10} {:>7} {:>10} {:>10}"
    print(fmt.format("Allocation", "CAGR", "Max DD", "Sharpe", "Tail Corr", "Worst Yr"))
    print("-" * 72)
    for k in blend_order + [vti_key]:
        m = metrics[k]
        print(fmt.format(
            m["label"],
            _pct(m["cagr"]),
            _pct(m["max_drawdown"]),
            _f(m["sharpe"]),
            _f(m["tail_corr_voo"]),
            _pct(m["worst_calendar_year_return"]),
        ))
    print("=" * 72)


if __name__ == "__main__":
    main()
