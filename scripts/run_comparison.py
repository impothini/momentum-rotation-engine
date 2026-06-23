"""Three-way strategy comparison: Momentum v1 vs Static benchmarks.

Evaluation framework: docs/evaluation_spec.md
Decision rule written before running — do not modify eval spec after this runs.

Usage:
    python scripts/run_comparison.py \
        --snapshot data/snapshots/<snap_dir> \
        --momentum-run data/runs/<run_id> \
        [--out docs/reports]

Outputs:
    <out>/comparison_nav.csv         normalized daily NAV for all strategies + VOO
    <out>/primary_metrics.json       CAGR, drawdown, tail-corr, Sharpe, Sortino, vol
    <out>/rolling_<strategy>.csv     3-year rolling CAGR/Sharpe/drawdown (monthly step)
    <out>/crash_periods.csv          per-crash return/drawdown/corr-to-VOO
    docs/reports/05_comparison_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from momentum_agent.backtest.calendar import TradingCalendar
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
# Locked evaluation constants — do not change after running
# ---------------------------------------------------------------------------

EVAL_START = date(2011, 10, 20)
EVAL_END = date(2025, 12, 30)
SIM_START = date(2010, 1, 1)       # warmup for vol lookback
INITIAL_CAPITAL = 100_000.0
RISK_TICKERS = ["QQQ", "IWM", "SCHD", "VEA", "VWO", "GLD", "DBC", "TLT"]
VOL_WINDOW = 60                    # trading days
ROLLING_WINDOW = 756               # ≈ 3 calendar years
ROLLING_STEP = 21                  # ≈ 1 month
VOO_TAIL_PCT = 0.05                # worst 5% of VOO days

CRASH_PERIODS = [
    ("2018_q4",         date(2018, 10, 1),  date(2018, 12, 31), "2018 Q4 (Fed tightening)"),
    ("2020_covid",      date(2020, 2, 19),  date(2020, 3, 23),  "2020 COVID crash"),
    ("2022_rate_shock", date(2022, 1, 3),   date(2022, 12, 31), "2022 Rate Shock"),
]

STRATEGY_LABELS = {
    "momentum_v1":         "Momentum v1",
    "static_equal_weight": "Static Equal Weight",
    "static_inverse_vol":  "Static Inverse Vol",
}

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _get_open(raw_open: pd.DataFrame, td: date, ticker: str) -> Optional[float]:
    ts = pd.Timestamp(td)
    if ts not in raw_open.index or ticker not in raw_open.columns:
        return None
    val = raw_open.loc[ts, ticker]
    return float(val) if pd.notna(val) and float(val) > 0 else None


def _get_close(raw_close: pd.DataFrame, td: date, ticker: str) -> Optional[float]:
    ts = pd.Timestamp(td)
    if ts not in raw_close.index or ticker not in raw_close.columns:
        return None
    val = raw_close.loc[ts, ticker]
    return float(val) if pd.notna(val) and float(val) > 0 else None


def _load_momentum_nav(run_dir: Path) -> pd.Series:
    path = run_dir / "daily_nav.csv"
    df = pd.read_csv(path, usecols=["date", "nav"])
    df = df.dropna(subset=["nav"])
    s = pd.Series(
        df["nav"].values.astype(float),
        index=pd.to_datetime(df["date"]),
        name="momentum_v1",
    )
    return s.sort_index()


# ---------------------------------------------------------------------------
# Weight functions
# ---------------------------------------------------------------------------


def _equal_weight_fn(
    _signal_date: date, _adj_close: pd.DataFrame, tickers: list[str]
) -> dict[str, float]:
    n = len(tickers)
    return {t: 1.0 / n for t in sorted(tickers)}


def _inverse_vol_fn(
    signal_date: date, adj_close: pd.DataFrame, tickers: list[str]
) -> dict[str, float]:
    end_ts = pd.Timestamp(signal_date)
    available = [t for t in tickers if t in adj_close.columns]
    hist = adj_close.loc[:end_ts, available]
    rets = hist.pct_change().dropna()

    inv_vols: dict[str, Optional[float]] = {}
    for t in sorted(tickers):
        if t not in rets.columns:
            inv_vols[t] = None
            continue
        col = rets[t].dropna()
        if len(col) >= VOL_WINDOW:
            vol = float(col.iloc[-VOL_WINDOW:].std())
            inv_vols[t] = 1.0 / vol if vol > 1e-10 else 0.0
        else:
            inv_vols[t] = None

    valid = {t: v for t, v in inv_vols.items() if v is not None}
    if not valid:
        return {t: 1.0 / len(tickers) for t in sorted(tickers)}

    avg_inv_vol = sum(valid.values()) / len(valid)
    filled: dict[str, float] = {
        t: v if v is not None else avg_inv_vol
        for t, v in inv_vols.items()
    }

    total = sum(filled.values())
    if total <= 0:
        return {t: 1.0 / len(tickers) for t in sorted(tickers)}

    return {t: filled[t] / total for t in sorted(tickers)}


# ---------------------------------------------------------------------------
# Static portfolio simulator
# ---------------------------------------------------------------------------


def _simulate_static(
    tickers: list[str],
    adj_close: pd.DataFrame,
    raw_open: pd.DataFrame,
    raw_close: pd.DataFrame,
    signal_dates: set[date],
    trading_days: list[date],
    weight_fn: Callable,
    initial_capital: float,
) -> pd.Series:
    """Simulate a static monthly-rebalance portfolio.

    Executes at the OPEN of the trading day AFTER each signal date.
    Marks to market at CLOSE each day.
    """
    # Derive next-trading-day from the precomputed list (avoids calendar edge cases)
    td_sorted = sorted(trading_days)
    _td_index = {d: i for i, d in enumerate(td_sorted)}

    def _next_trading_day(d: date) -> Optional[date]:
        idx = _td_index.get(d)
        if idx is not None and idx + 1 < len(td_sorted):
            return td_sorted[idx + 1]
        # d not in the list — find the first day after d
        for td in td_sorted:
            if td > d:
                return td
        return None

    # Build execution schedule: exec_date -> target_weights
    execution_schedule: dict[date, dict[str, float]] = {}
    for s in sorted(signal_dates):
        exec_date = _next_trading_day(s)
        if exec_date is None:
            continue
        weights = weight_fn(s, adj_close, tickers)
        total_w = sum(weights.values())
        if total_w > 0:
            execution_schedule[exec_date] = {t: w / total_w for t, w in weights.items()}

    cash = initial_capital
    shares: dict[str, float] = {}
    nav_records: list[tuple[date, float]] = []

    for td in trading_days:
        # Execute rebalance at today's open (if scheduled)
        if td in execution_schedule:
            target_weights = execution_schedule[td]

            # Mark existing portfolio at today's open
            portfolio_value = cash
            for t, qty in sorted(shares.items()):
                if qty != 0:
                    open_px = _get_open(raw_open, td, t)
                    if open_px is not None:
                        portfolio_value += qty * open_px

            # Buy target positions at today's open
            new_shares: dict[str, float] = {}
            total_allocated = 0.0
            for t in sorted(target_weights.keys()):
                w = target_weights[t]
                if w <= 0:
                    continue
                open_px = _get_open(raw_open, td, t)
                if open_px is not None:
                    target_value = portfolio_value * w
                    qty = target_value / open_px
                    new_shares[t] = qty
                    total_allocated += qty * open_px

            shares = new_shares
            cash = portfolio_value - total_allocated  # rounding residual

        # Mark-to-market at close
        positions_value = 0.0
        for t, qty in sorted(shares.items()):
            close_px = _get_close(raw_close, td, t)
            if close_px is not None:
                positions_value += qty * close_px

        nav_records.append((td, cash + positions_value))

    if not nav_records:
        return pd.Series(dtype=float)

    return pd.Series(
        [r[1] for r in nav_records],
        index=pd.DatetimeIndex([r[0] for r in nav_records]),
        name="nav",
    ).astype(float)


def _normalize_to_start(
    nav: pd.Series, start: date, capital: float
) -> pd.Series:
    """Normalize NAV so that the value on start = capital."""
    start_ts = pd.Timestamp(start)
    if start_ts not in nav.index:
        after = nav.index[nav.index >= start_ts]
        if len(after) == 0:
            raise ValueError(f"No NAV data on or after {start}")
        start_ts = after[0]
    base = float(nav.loc[start_ts])
    if base <= 0:
        raise ValueError(f"NAV at {start_ts} is {base}")
    return (nav / base * capital).rename(nav.name)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _tail_correlation(
    strat_rets: pd.Series, bench_rets: pd.Series, pct: float = VOO_TAIL_PCT
) -> tuple[float, float, float]:
    """(tail_corr, avg_strat_ret, avg_bench_ret) on bench's worst pct days."""
    aligned = pd.concat([strat_rets, bench_rets], axis=1).dropna()
    aligned.columns = ["strat", "bench"]
    n_tail = max(1, int(len(aligned) * pct))
    tail = aligned.nsmallest(n_tail, "bench")
    if len(tail) < 5:
        return float("nan"), float("nan"), float("nan")
    corr = float(tail["strat"].corr(tail["bench"]))
    return corr, float(tail["strat"].mean()), float(tail["bench"].mean())


def _compute_strategy_metrics(
    nav: pd.Series, voo_rets: pd.Series, label: str
) -> dict:
    rets = daily_returns(nav)
    aligned = pd.concat([rets, voo_rets], axis=1).dropna()
    full_corr = (
        float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
        if len(aligned) > 1
        else float("nan")
    )
    tail_corr, avg_strat_tail, avg_voo_tail = _tail_correlation(rets, voo_rets)

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
    }


def _rolling_metrics(nav: pd.Series, window: int, step: int) -> pd.DataFrame:
    """Compute rolling CAGR, Sharpe, max-drawdown (monthly step)."""
    records = []
    for i in range(window, len(nav) + 1, step):
        window_nav = nav.iloc[i - window:i]
        cagr = calculate_cagr(window_nav)
        mdd = calculate_max_drawdown(window_nav)
        sharpe = calculate_sharpe(window_nav)
        records.append({
            "window_end": window_nav.index[-1].date().isoformat(),
            "cagr": round(cagr, 6) if cagr is not None else None,
            "max_drawdown": round(mdd, 6) if mdd is not None else None,
            "sharpe": round(sharpe, 6) if sharpe is not None else None,
        })
    return pd.DataFrame(records)


def _crash_metrics_for_strategy(
    nav: pd.Series, voo_rets: pd.Series
) -> list[dict]:
    records = []
    for key, start, end, label in CRASH_PERIODS:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        seg = nav.loc[start_ts:end_ts]
        if len(seg) < 2:
            records.append({"period": label, "total_return": None, "max_drawdown": None, "corr_voo": None})
            continue
        total_ret = float(seg.iloc[-1] / seg.iloc[0] - 1)
        mdd = calculate_max_drawdown(seg)
        strat_rets = daily_returns(seg)
        aligned = pd.concat([strat_rets, voo_rets.loc[start_ts:end_ts]], axis=1, sort=False).dropna()
        corr = (
            float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            if len(aligned) > 2
            else None
        )
        records.append({
            "period": label,
            "total_return": round(total_ret, 6),
            "max_drawdown": round(mdd, 6) if mdd is not None else None,
            "corr_voo": round(corr, 4) if corr is not None else None,
        })
    return records


# ---------------------------------------------------------------------------
# Decision rule — encoded verbatim from evaluation_spec.md
# ---------------------------------------------------------------------------


def _apply_decision_rule(
    mom: dict, static_list: list[dict]
) -> tuple[bool, list[str]]:
    """
    Momentum v1 is dominated only if ALL three hold for the same benchmark:
    1. Static CAGR > Momentum CAGR
    2. Static tail_corr_voo <= Momentum tail_corr_voo (equal or better protection)
    3. Static max_drawdown >= Momentum max_drawdown (less negative = shallower)
    Returns (is_dominated, list_of_dominating_labels).
    """
    mom_cagr = mom["cagr"] or 0.0
    mom_mdd = mom["max_drawdown"] or 0.0
    mom_tail = mom["tail_corr_voo"]

    dominating = []
    for s in static_list:
        s_cagr = s["cagr"] or 0.0
        s_mdd = s["max_drawdown"] or 0.0
        s_tail = s["tail_corr_voo"]

        c1 = s_cagr > mom_cagr
        if np.isnan(s_tail) or np.isnan(mom_tail):
            c2 = False
        else:
            c2 = s_tail <= mom_tail
        c3 = s_mdd >= mom_mdd

        if c1 and c2 and c3:
            dominating.append(s["label"])

    return len(dominating) > 0, dominating


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _pct(v: Optional[float], decimals: int = 2) -> str:
    if v is None or np.isnan(v):
        return "n/a"
    return f"{v * 100:.{decimals}f}%"


def _f(v: Optional[float], decimals: int = 3) -> str:
    if v is None or np.isnan(v):
        return "n/a"
    return f"{v:.{decimals}f}"


def _write_report(
    report_path: Path,
    all_metrics: dict[str, dict],
    rolling: dict[str, pd.DataFrame],
    crash: dict[str, list[dict]],
    is_dominated: bool,
    dominating: list[str],
    snapshot_id: str,
    momentum_run_id: str,
) -> None:
    keys = ["momentum_v1", "static_equal_weight", "static_inverse_vol"]
    labels = [STRATEGY_LABELS[k] for k in keys]

    lines = [
        "# Report 05 — Strategy Comparison",
        "",
        f"**Snapshot ID:** `{snapshot_id}`  ",
        "**Strategy Version:** `1.0.0`  ",
        f"**Momentum run:** `{momentum_run_id}`  ",
        f"**Evaluation window:** {EVAL_START} → {EVAL_END}  ",
        "**Decision rule:** docs/evaluation_spec.md (locked before running)",
        "",
        "---",
        "",
        "## 1. Primary Metrics",
        "",
        "| Metric | " + " | ".join(labels) + " |",
        "|--------|" + "|".join(["---"] * len(keys)) + "|",
    ]

    metric_rows = [
        ("CAGR", "cagr", _pct),
        ("Max Drawdown", "max_drawdown", _pct),
        ("Annualized Vol", "annualized_vol", _pct),
        ("Sharpe Ratio", "sharpe", lambda v: _f(v, 3)),
        ("Sortino Ratio", "sortino", lambda v: _f(v, 3)),
        ("Full-period Corr (VOO)", "full_corr_voo", lambda v: _f(v, 3)),
        ("Tail Corr (VOO worst 5%)", "tail_corr_voo", lambda v: _f(v, 3)),
        ("Avg return on VOO worst-5% days", "avg_ret_on_voo_worst5pct_days", _pct),
    ]

    for row_label, field, fmt_fn in metric_rows:
        cells = [fmt_fn(all_metrics[k][field]) for k in keys]
        lines.append(f"| {row_label} | " + " | ".join(cells) + " |")

    # Decision rule
    lines += [
        "",
        "---",
        "",
        "## 2. Decision Rule Application",
        "",
        "> From `docs/evaluation_spec.md` (written before running):  ",
        "> Momentum v1 is considered dominated only if ALL three conditions hold:  ",
        "> 1. A static benchmark achieves strictly higher full-period CAGR  ",
        "> 2. The same benchmark achieves equal or better tail correlation  ",
        "> 3. The same benchmark achieves equal or better max drawdown  ",
        "",
    ]

    for k in ["static_equal_weight", "static_inverse_vol"]:
        s = all_metrics[k]
        m = all_metrics["momentum_v1"]
        sl = STRATEGY_LABELS[k]
        c1 = (s["cagr"] or 0) > (m["cagr"] or 0)
        c2 = (s["tail_corr_voo"] <= m["tail_corr_voo"]) if (
            not np.isnan(s["tail_corr_voo"]) and not np.isnan(m["tail_corr_voo"])
        ) else False
        c3 = (s["max_drawdown"] or 0) >= (m["max_drawdown"] or 0)

        def tick(b: bool) -> str:
            return "PASS" if b else "FAIL"

        lines += [
            f"**{sl} vs Momentum v1:**",
            "",
            f"| Condition | Result | Details |",
            f"|-----------|--------|---------|",
            f"| 1. Higher CAGR | {tick(c1)} | {sl}: {_pct(s['cagr'])} vs Momentum: {_pct(m['cagr'])} |",
            f"| 2. Equal/better tail corr | {tick(c2)} | {sl}: {_f(s['tail_corr_voo'])} vs Momentum: {_f(m['tail_corr_voo'])} |",
            f"| 3. Equal/better drawdown | {tick(c3)} | {sl}: {_pct(s['max_drawdown'])} vs Momentum: {_pct(m['max_drawdown'])} |",
            "",
        ]

    if is_dominated:
        verdict = (
            f"**VERDICT: Momentum v1 is DOMINATED by {', '.join(dominating)}.**  \n"
            f"All three conditions hold. Static allocation achieves the same risk profile at lower complexity.  \n"
            f"Recommendation: retire v1 or redesign the signal."
        )
    else:
        verdict = (
            "**VERDICT: Momentum v1 is NOT dominated.**  \n"
            "It provides differentiated value. The cost of complexity is justified by at least one dimension "
            "(tail protection or drawdown control)."
        )

    lines += [verdict, "", "---", "", "## 3. Crash-Period Analysis", ""]

    # Build crash table per period
    for key, start, end, label in CRASH_PERIODS:
        lines += [
            f"### {label}  `{start} → {end}`",
            "",
            f"| Strategy | Total Return | Max Drawdown | Corr to VOO |",
            f"|----------|-------------|--------------|-------------|",
        ]
        for k in keys:
            periods_data = crash[k]
            row = next((r for r in periods_data if r["period"] == label), None)
            if row:
                lines.append(
                    f"| {STRATEGY_LABELS[k]} | "
                    f"{_pct(row['total_return'])} | "
                    f"{_pct(row['max_drawdown'])} | "
                    f"{_f(row['corr_voo'], 3)} |"
                )
        lines.append("")

    # Rolling metrics summary
    lines += [
        "---",
        "",
        "## 4. Rolling 3-Year Metrics (percentile summary)",
        "",
        "| Metric | Pct | " + " | ".join(labels) + " |",
        "|--------|-----|" + "|".join(["---"] * len(keys)) + "|",
    ]

    pctile_rows = [
        ("Rolling CAGR", "cagr", _pct),
        ("Rolling Sharpe", "sharpe", lambda v: _f(v, 3)),
        ("Rolling Max DD", "max_drawdown", _pct),
    ]

    for row_label, col, fmt_fn in pctile_rows:
        for pct_label, pct_fn in [("p25", lambda s: s.quantile(0.25)), ("median", lambda s: s.median()), ("p75", lambda s: s.quantile(0.75))]:
            cells = []
            for k in keys:
                series = rolling[k][col].dropna()
                cells.append(fmt_fn(pct_fn(series)) if len(series) > 0 else "n/a")
            lines.append(f"| {row_label} | {pct_label} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "---",
        "",
        "## 5. Observation",
        "",
        "The question this comparison answers is not **did momentum beat static** but",
        "**did momentum earn the complexity it introduced**. Refer to the full",
        "decision rule in `docs/evaluation_spec.md` before drawing forward-looking",
        "conclusions from these results.",
    ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python scripts/run_comparison.py",
        description="Three-way strategy comparison using a frozen snapshot.",
    )
    p.add_argument("--snapshot", required=True, metavar="PATH",
                   help="Path to frozen snapshot directory.")
    p.add_argument("--momentum-run", required=True, metavar="PATH",
                   help="Path to existing momentum v1 run directory (contains daily_nav.csv).")
    p.add_argument("--out", default="docs/reports", metavar="PATH",
                   help="Output directory for comparison CSVs (default: docs/reports).")
    p.add_argument("--no-verify-hash", action="store_true", default=False,
                   help="Skip snapshot hash verification.")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    snapshot_dir = Path(args.snapshot)
    momentum_run_dir = Path(args.momentum_run)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Load snapshot ----
    print(f"Loading snapshot : {snapshot_dir}")
    loader = MarketDataLoader.from_snapshot(
        snapshot_dir, verify_hash=not args.no_verify_hash
    )
    adj_close = loader.adj_close
    raw_open = loader.raw_open
    raw_close = loader.raw_close
    snap = loader.snapshot
    print(f"  snapshot_id    : {snap.snapshot_id}")
    print(f"  adj_price_hash : {snap.adjusted_price_hash[:16]}...")

    # ---- Load momentum v1 NAV ----
    print(f"\nLoading momentum v1 run : {momentum_run_dir}")
    mom_nav_full = _load_momentum_nav(momentum_run_dir)
    print(f"  NAV rows       : {len(mom_nav_full)}")

    # ---- Build VOO daily return series for correlation ----
    if "VOO" not in adj_close.columns:
        sys.exit("Error: VOO not in snapshot adj_close. Add VOO to --tickers when freezing.")
    voo_price = adj_close["VOO"].dropna()

    # ---- Calendar setup ----
    print(f"\nBuilding trading calendar {SIM_START} → {EVAL_END}...")
    cal = TradingCalendar()
    sim_trading_days = cal.get_trading_days(SIM_START, EVAL_END)
    sim_signal_dates = cal.get_month_end_signal_dates(SIM_START, EVAL_END)
    print(f"  sim trading days : {len(sim_trading_days)}")
    print(f"  signal dates     : {len(sim_signal_dates)}")

    # Filter trading days to those available in price data
    available_close = set(raw_close.index.date)
    sim_trading_days = [d for d in sim_trading_days if d in available_close]

    # ---- Run static equal weight ----
    print("\nSimulating Static Equal Weight...")
    ew_nav_full = _simulate_static(
        tickers=sorted(RISK_TICKERS),
        adj_close=adj_close,
        raw_open=raw_open,
        raw_close=raw_close,
        signal_dates=sim_signal_dates,
        trading_days=sim_trading_days,
        weight_fn=_equal_weight_fn,
        initial_capital=INITIAL_CAPITAL,
    )
    ew_nav_full.name = "static_equal_weight"
    print(f"  NAV rows       : {len(ew_nav_full)}")

    # ---- Run static inverse vol ----
    print("\nSimulating Static Inverse Vol...")
    iv_nav_full = _simulate_static(
        tickers=sorted(RISK_TICKERS),
        adj_close=adj_close,
        raw_open=raw_open,
        raw_close=raw_close,
        signal_dates=sim_signal_dates,
        trading_days=sim_trading_days,
        weight_fn=_inverse_vol_fn,
        initial_capital=INITIAL_CAPITAL,
    )
    iv_nav_full.name = "static_inverse_vol"
    print(f"  NAV rows       : {len(iv_nav_full)}")

    # ---- Normalize all to $100k at EVAL_START ----
    print(f"\nNormalizing all strategies to ${INITIAL_CAPITAL:,.0f} at {EVAL_START}...")
    mom_nav = _normalize_to_start(mom_nav_full, EVAL_START, INITIAL_CAPITAL)
    ew_nav = _normalize_to_start(ew_nav_full, EVAL_START, INITIAL_CAPITAL)
    iv_nav = _normalize_to_start(iv_nav_full, EVAL_START, INITIAL_CAPITAL)

    # ---- Slice to evaluation window ----
    eval_start_ts = pd.Timestamp(EVAL_START)
    eval_end_ts = pd.Timestamp(EVAL_END)

    mom_nav = mom_nav.loc[eval_start_ts:eval_end_ts]
    ew_nav = ew_nav.loc[eval_start_ts:eval_end_ts]
    iv_nav = iv_nav.loc[eval_start_ts:eval_end_ts]
    voo_eval = voo_price.loc[eval_start_ts:eval_end_ts]
    voo_eval = _normalize_to_start(voo_eval, EVAL_START, INITIAL_CAPITAL)
    voo_rets_eval = daily_returns(voo_eval)

    print(f"  Momentum v1 eval rows    : {len(mom_nav)}")
    print(f"  Equal weight eval rows   : {len(ew_nav)}")
    print(f"  Inverse vol eval rows    : {len(iv_nav)}")

    # ---- Write comparison NAV CSV ----
    nav_df = pd.concat(
        [mom_nav.rename("momentum_v1"),
         ew_nav.rename("static_equal_weight"),
         iv_nav.rename("static_inverse_vol"),
         voo_eval.rename("voo")],
        axis=1,
    )
    nav_df.index.name = "date"
    nav_csv = out_dir / "comparison_nav.csv"
    nav_df.to_csv(nav_csv, float_format="%.4f")
    print(f"\nWrote : {nav_csv}")

    # ---- Primary metrics ----
    print("\nComputing primary metrics...")
    all_metrics: dict[str, dict] = {
        "momentum_v1":         _compute_strategy_metrics(mom_nav, voo_rets_eval, "Momentum v1"),
        "static_equal_weight": _compute_strategy_metrics(ew_nav, voo_rets_eval, "Static Equal Weight"),
        "static_inverse_vol":  _compute_strategy_metrics(iv_nav, voo_rets_eval, "Static Inverse Vol"),
    }

    metrics_path = out_dir / "primary_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(all_metrics, f, indent=2, default=lambda x: None if (isinstance(x, float) and np.isnan(x)) else x)
    print(f"Wrote : {metrics_path}")

    # ---- Rolling metrics ----
    print("\nComputing rolling metrics (3-year window, monthly step)...")
    rolling: dict[str, pd.DataFrame] = {}
    for k, nav in [("momentum_v1", mom_nav), ("static_equal_weight", ew_nav), ("static_inverse_vol", iv_nav)]:
        df = _rolling_metrics(nav, ROLLING_WINDOW, ROLLING_STEP)
        rolling[k] = df
        path = out_dir / f"rolling_{k}.csv"
        df.to_csv(path, index=False)
        print(f"  Wrote : {path}  ({len(df)} windows)")

    # ---- Crash period metrics ----
    print("\nComputing crash-period metrics...")
    crash: dict[str, list[dict]] = {}
    for k, nav in [("momentum_v1", mom_nav), ("static_equal_weight", ew_nav), ("static_inverse_vol", iv_nav)]:
        crash[k] = _crash_metrics_for_strategy(nav, voo_rets_eval)

    # Flatten to one CSV
    crash_rows = []
    for _, start, end, label in CRASH_PERIODS:
        for k in ["momentum_v1", "static_equal_weight", "static_inverse_vol"]:
            row = next((r for r in crash[k] if r["period"] == label), None)
            if row:
                crash_rows.append({"period": label, "strategy": STRATEGY_LABELS[k], **row})
    crash_df = pd.DataFrame(crash_rows)
    crash_path = out_dir / "crash_periods.csv"
    crash_df.to_csv(crash_path, index=False)
    print(f"Wrote : {crash_path}")

    # ---- Decision rule ----
    print("\nApplying decision rule...")
    is_dominated, dominating = _apply_decision_rule(
        all_metrics["momentum_v1"],
        [all_metrics["static_equal_weight"], all_metrics["static_inverse_vol"]],
    )
    verdict_str = "DOMINATED" if is_dominated else "NOT DOMINATED"
    print(f"  Result: Momentum v1 is {verdict_str}")
    if dominating:
        print(f"  Dominated by: {', '.join(dominating)}")

    # ---- Write report ----
    momentum_run_id = momentum_run_dir.name
    report_path = Path("docs/reports/05_comparison_report.md")
    _write_report(
        report_path=report_path,
        all_metrics=all_metrics,
        rolling=rolling,
        crash=crash,
        is_dominated=is_dominated,
        dominating=dominating,
        snapshot_id=snap.snapshot_id,
        momentum_run_id=momentum_run_id,
    )
    print(f"\nWrote : {report_path}")

    # ---- Summary ----
    print("\n" + "=" * 60)
    print("PRIMARY METRICS SUMMARY")
    print("=" * 60)
    hdrs = ["Metric", "Momentum v1", "Equal Weight", "Inverse Vol"]
    fmt = "{:<30} {:>14} {:>14} {:>14}"
    print(fmt.format(*hdrs))
    print("-" * 74)
    rows = [
        ("CAGR", "cagr", _pct),
        ("Max Drawdown", "max_drawdown", _pct),
        ("Sharpe Ratio", "sharpe", lambda v: _f(v, 3)),
        ("Tail Corr (VOO worst 5%)", "tail_corr_voo", lambda v: _f(v, 3)),
    ]
    for rlabel, field, fmt_fn in rows:
        vals = [fmt_fn(all_metrics[k][field]) for k in ["momentum_v1", "static_equal_weight", "static_inverse_vol"]]
        print(fmt.format(rlabel, *vals))
    print("=" * 60)
    print(f"\nVERDICT: {verdict_str}")
    print(f"Full report: {report_path}")


if __name__ == "__main__":
    main()
