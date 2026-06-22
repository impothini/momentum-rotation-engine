"""Asset selection pipeline for Strategy v1.0.0.

Exact execution order (from spec §SELECTION PIPELINE):
  1. Exclude SGOV from candidates.
  2. Validate data (require lookback_days + 1 history).
  3. Apply trend filter.
  4. If zero assets qualify → 100% SGOV.
  5. Rank qualifying assets by momentum.
  6. Select highest-ranked asset.
  7. Search for second asset (different family, correlation <= 0.70).
  8. If second asset exists → bounded inverse-vol weighting.
  9. If only one asset qualifies → 70% selected + 30% SGOV.
 10. If second asset not found → 70% selected + 30% SGOV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

from momentum_agent.config import (
    MOMENTUM_CANDIDATES,
    TICKER_TO_FAMILY,
    MomentumParams,
)
from momentum_agent.strategies.momentum.signals import (
    apply_trend_filter,
    calculate_annualized_volatility,
    calculate_momentum_score,
    calculate_rolling_correlation,
)
from momentum_agent.strategies.momentum.weights import compute_inverse_vol_weights


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


@dataclass
class AllocationTarget:
    """The output of the selection pipeline: target weights for each ticker."""

    allocations: dict[str, float]
    """ticker → weight (all weights sum to 1.0)."""

    reason: str
    """Human-readable reason for this allocation."""

    proxy_used: bool = False
    data_status: str = "ok"
    selected_candidates: list[str] = field(default_factory=list)
    momentum_scores: dict[str, float] = field(default_factory=dict)
    trend_qualified: list[str] = field(default_factory=list)
    correlation_checked: dict[str, Optional[float]] = field(default_factory=dict)
    volatility_used: dict[str, Optional[float]] = field(default_factory=dict)

    def validate(self) -> None:
        """Assert weight sum is approximately 1.0."""
        total = sum(self.allocations.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Allocation weights do not sum to 1.0: {total:.8f}")


# ---------------------------------------------------------------------------
# Selection pipeline
# ---------------------------------------------------------------------------


class SelectionPipeline:
    """Deterministic asset selection for Strategy v1.0.0.

    All inputs are pure data (DataFrames, sets).  No IO, no randomness.
    """

    def __init__(self, params: MomentumParams) -> None:
        self.params = params

    def run(
        self,
        adj_prices: pd.DataFrame,
        signal_date: date,
        valid_tickers: set[str],
        lockout_set: set[str],
    ) -> AllocationTarget:
        """Execute the full selection pipeline and return target allocations.

        Args:
            adj_prices: Adjusted close prices, rows indexed by date, cols by ticker.
                        Must include all rows up to and including signal_date.
            signal_date: The last trading day of the month (signal date).
            valid_tickers: Tickers with valid price bars on signal_date.
            lockout_set: Tickers excluded from this rebalance (e.g. stop-triggered).
        """
        ts = pd.Timestamp(signal_date)
        # Get integer position of signal_date in the index
        if ts not in adj_prices.index:
            return AllocationTarget(
                allocations={"SGOV": 1.0},
                reason="signal_date_not_in_data",
                data_status="missing_signal_date",
            )

        t_idx = adj_prices.index.get_loc(ts)
        prices_to_t = adj_prices.iloc[: t_idx + 1]

        # ----------------------------------------------------------------
        # Step 1: Exclude SGOV and apply lockout
        # ----------------------------------------------------------------
        candidates = [
            t
            for t in MOMENTUM_CANDIDATES
            if t not in lockout_set and t in adj_prices.columns
        ]

        # ----------------------------------------------------------------
        # Step 2: Validate data
        # ----------------------------------------------------------------
        min_len = self.params.lookback_days + 1  # need t-273 to be valid
        eligible = []
        for ticker in candidates:
            if ticker not in valid_tickers:
                continue
            series = prices_to_t[ticker].dropna()
            if len(series) >= min_len:
                eligible.append(ticker)

        # ----------------------------------------------------------------
        # Step 3: Apply trend filter
        # ----------------------------------------------------------------
        trend_qualified: list[str] = []
        for ticker in eligible:
            series = prices_to_t[ticker].dropna()
            if apply_trend_filter(series, self.params.trend_filter_window):
                trend_qualified.append(ticker)

        # ----------------------------------------------------------------
        # Step 4: SGOV fallback if nothing qualifies
        # ----------------------------------------------------------------
        if not trend_qualified:
            return AllocationTarget(
                allocations={"SGOV": 1.0},
                reason="no_trend_qualified",
                trend_qualified=[],
            )

        # ----------------------------------------------------------------
        # Step 5: Rank by momentum
        # ----------------------------------------------------------------
        momentum_scores: dict[str, float] = {}
        for ticker in trend_qualified:
            series = prices_to_t[ticker].dropna()
            try:
                score = calculate_momentum_score(
                    series,
                    exclude_recent=self.params.exclude_recent_days,
                    lookback=self.params.lookback_days,
                )
                momentum_scores[ticker] = score
            except ValueError:
                pass  # Skip tickers with insufficient / invalid history

        if not momentum_scores:
            return AllocationTarget(
                allocations={"SGOV": 1.0},
                reason="no_valid_momentum_scores",
                trend_qualified=trend_qualified,
            )

        ranked = sorted(
            momentum_scores.keys(),
            key=lambda t: momentum_scores[t],
            reverse=True,
        )

        # ----------------------------------------------------------------
        # Step 6: Select highest-ranked asset
        # ----------------------------------------------------------------
        primary = ranked[0]
        primary_family = TICKER_TO_FAMILY.get(primary)

        # ----------------------------------------------------------------
        # Step 7: Search for second asset
        # ----------------------------------------------------------------
        secondary: Optional[str] = None
        correlation_checked: dict[str, Optional[float]] = {}
        primary_prices = prices_to_t[primary].dropna()

        for candidate in ranked[1:]:
            candidate_family = TICKER_TO_FAMILY.get(candidate)

            # Must be from a different family
            if candidate_family == primary_family:
                continue

            candidate_prices = prices_to_t[candidate].dropna()
            corr = calculate_rolling_correlation(
                primary_prices,
                candidate_prices,
                window=self.params.correlation_window,
            )
            correlation_checked[candidate] = corr

            if corr is not None and corr <= self.params.correlation_threshold:
                secondary = candidate
                break

        # ----------------------------------------------------------------
        # Step 8: Two assets → inverse-vol weighting
        # ----------------------------------------------------------------
        volatility_used: dict[str, Optional[float]] = {}

        if secondary is not None:
            primary_prices_v = prices_to_t[primary].dropna()
            secondary_prices_v = prices_to_t[secondary].dropna()

            vol_primary = calculate_annualized_volatility(
                primary_prices_v, window=self.params.volatility_window
            )
            vol_secondary = calculate_annualized_volatility(
                secondary_prices_v, window=self.params.volatility_window
            )
            volatility_used[primary] = vol_primary
            volatility_used[secondary] = vol_secondary

            if vol_primary and vol_secondary:
                w_primary, w_secondary = compute_inverse_vol_weights(
                    vol_primary,
                    vol_secondary,
                    min_weight=self.params.min_weight,
                    max_weight=self.params.max_weight,
                )
                return AllocationTarget(
                    allocations={primary: w_primary, secondary: w_secondary},
                    reason="two_assets_inverse_vol",
                    selected_candidates=[primary, secondary],
                    momentum_scores=momentum_scores,
                    trend_qualified=trend_qualified,
                    correlation_checked=correlation_checked,
                    volatility_used=volatility_used,
                )
            else:
                # Vol unavailable — fall through to single-asset allocation
                secondary = None

        # ----------------------------------------------------------------
        # Steps 9 / 10: Single asset → 70% asset + 30% SGOV
        # ----------------------------------------------------------------
        return AllocationTarget(
            allocations={
                primary: self.params.single_asset_weight,
                "SGOV": self.params.sgov_complement_weight,
            },
            reason=(
                "single_asset_no_second_qualifies"
                if len(trend_qualified) > 1 or len(ranked) > 1
                else "single_asset_only_one_qualifies"
            ),
            selected_candidates=[primary],
            momentum_scores=momentum_scores,
            trend_qualified=trend_qualified,
            correlation_checked=correlation_checked,
            volatility_used=volatility_used,
        )
