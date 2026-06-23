"""Daily stop-loss evaluation.

Trigger:  raw_close[t] <= entry_vwap_fill_price * 0.90
Action:   Emit STOP_TRIGGER, exit next open, emit STOP_EXIT after fill.
Proceeds: Route to SGOV.
Lockout:  Asset excluded from current rebalance and cannot be repurchased
          until the following monthly rebalance.

SGOV is exempt from stop checks.

Stop / Rebalance Collision:
  If the stop triggers on a rebalance signal date:
  - Add asset to lockout set
  - Exclude from same rebalance selection
  - Generate ONE net order batch (no separate stop + rebalance orders)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from momentum_agent.config import RiskParams
from momentum_agent.portfolio.state import PositionState


@dataclass
class StopCheckResult:
    ticker: str
    triggered: bool
    raw_close: Optional[float]
    entry_vwap: float
    threshold_price: float
    reason: Optional[str] = None


class StopLossEvaluator:
    def __init__(self, params: RiskParams) -> None:
        self.params = params

    def check_position(
        self,
        position: PositionState,
        raw_close: Optional[float],
    ) -> StopCheckResult:
        """Evaluate the daily stop rule for one position."""
        threshold = position.entry_vwap_fill_price * self.params.stop_loss_pct

        if position.ticker == "SGOV":
            return StopCheckResult(
                ticker=position.ticker,
                triggered=False,
                raw_close=raw_close,
                entry_vwap=position.entry_vwap_fill_price,
                threshold_price=threshold,
                reason="sgov_exempt",
            )

        if raw_close is None:
            return StopCheckResult(
                ticker=position.ticker,
                triggered=False,
                raw_close=None,
                entry_vwap=position.entry_vwap_fill_price,
                threshold_price=threshold,
                reason="missing_price_no_trigger",
            )

        triggered = raw_close <= threshold
        return StopCheckResult(
            ticker=position.ticker,
            triggered=triggered,
            raw_close=raw_close,
            entry_vwap=position.entry_vwap_fill_price,
            threshold_price=threshold,
            reason="stop_triggered" if triggered else None,
        )

    def check_all_positions(
        self,
        positions: dict[str, PositionState],
        raw_closes: dict[str, Optional[float]],
    ) -> list[StopCheckResult]:
        """Evaluate stops for all held positions."""
        results = []
        for ticker, position in sorted(positions.items()):
            raw_close = raw_closes.get(ticker)
            results.append(self.check_position(position, raw_close))
        return results

    def get_triggered(
        self, results: list[StopCheckResult]
    ) -> list[StopCheckResult]:
        return [r for r in results if r.triggered]
