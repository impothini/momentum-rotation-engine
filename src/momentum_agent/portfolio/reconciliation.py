"""Post-execution reconciliation.

Rules:
- Reconcile against post-rounding TARGET shares, NOT ideal continuous weights.
- Expected residual cash from rounding is valid.
- Tolerance: share_tolerance=0.0001, weight_tolerance=0.005, cash_tolerance=1.00
- Emit RECONCILIATION_FAILURE if any tolerance is exceeded.
- Emit UNAUTHORIZED_POSITION_CHANGE if broker positions changed without
  engine-generated orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from momentum_agent.config import ReconciliationParams
from momentum_agent.events import Event, EventType


@dataclass
class ReconciliationResult:
    passed: bool
    share_diffs: dict[str, float] = field(default_factory=dict)
    weight_diffs: dict[str, float] = field(default_factory=dict)
    cash_diff: float = 0.0
    events: list[Event] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"


class Reconciler:
    def __init__(self, params: ReconciliationParams) -> None:
        self.params = params

    def reconcile(
        self,
        target_shares: dict[str, float],       # ticker → rounded target shares
        actual_shares: dict[str, float],        # ticker → broker-reported shares
        target_cash: float,
        actual_cash: float,
        recon_date: date,
        strategy_version: str = "1.0.0",
    ) -> ReconciliationResult:
        """Compare actual broker state against target.

        target_shares: what the engine INTENDED after rounding
        actual_shares: what the broker REPORTS
        """
        events: list[Event] = []
        share_diffs: dict[str, float] = {}
        weight_diffs: dict[str, float] = {}
        failed = False
        notes: list[str] = []

        all_tickers = set(target_shares) | set(actual_shares)
        for ticker in all_tickers:
            target = target_shares.get(ticker, 0.0)
            actual = actual_shares.get(ticker, 0.0)
            diff = abs(actual - target)
            share_diffs[ticker] = actual - target

            if diff > self.params.share_tolerance:
                failed = True
                notes.append(
                    f"{ticker}: share_diff={actual - target:.6f} "
                    f"(tolerance={self.params.share_tolerance})"
                )

        cash_diff = abs(actual_cash - target_cash)
        if cash_diff > self.params.cash_tolerance:
            failed = True
            notes.append(
                f"cash_diff={actual_cash - target_cash:.2f} "
                f"(tolerance={self.params.cash_tolerance})"
            )

        if failed:
            events.append(
                Event(
                    date=recon_date,
                    event_type=EventType.RECONCILIATION_FAILURE,
                    strategy_version=strategy_version,
                    reconciliation_status="FAIL",
                    reason="; ".join(notes),
                )
            )

        return ReconciliationResult(
            passed=not failed,
            share_diffs=share_diffs,
            weight_diffs=weight_diffs,
            cash_diff=actual_cash - target_cash,
            events=events,
            notes=notes,
        )

    def detect_unauthorized_changes(
        self,
        prev_known_shares: dict[str, float],    # what engine expected before orders
        current_broker_shares: dict[str, float], # what broker reports now
        authorized_tickers: set[str],            # tickers that engine just traded
        check_date: date,
        strategy_version: str = "1.0.0",
    ) -> list[Event]:
        """Detect positions that changed without engine-generated orders."""
        events: list[Event] = []
        all_tickers = set(prev_known_shares) | set(current_broker_shares)

        for ticker in all_tickers:
            if ticker in authorized_tickers:
                continue  # This change was authorized
            prev = prev_known_shares.get(ticker, 0.0)
            current = current_broker_shares.get(ticker, 0.0)
            diff = abs(current - prev)
            if diff > self.params.share_tolerance:
                events.append(
                    Event(
                        date=check_date,
                        event_type=EventType.UNAUTHORIZED_POSITION_CHANGE,
                        strategy_version=strategy_version,
                        ticker=ticker,
                        actual_shares=current,
                        target_shares=prev,
                        reason=(
                            f"unauthorized_change: expected {prev:.6f} shares, "
                            f"broker reports {current:.6f}"
                        ),
                    )
                )
        return events
