"""Kill-switch evaluation logic.

Trigger: NAV <= HWM * 0.85
Action:  Liquidate all, move to SGOV, emit KILL_SWITCH event.
Resume:  Next monthly rebalance; reset HWM = current NAV on resume date.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from momentum_agent.config import RiskParams


class KillSwitchEvaluator:
    def __init__(self, params: RiskParams) -> None:
        self.params = params

    def should_trigger(
        self,
        nav: Optional[float],
        high_water_mark: Optional[float],
    ) -> bool:
        """Return True if the kill switch should fire."""
        if nav is None or high_water_mark is None:
            # NAV invalid — kill switch cannot be evaluated (spec §PARTIAL NAV RULE)
            return False
        return nav <= high_water_mark * self.params.kill_switch_pct

    def update_hwm(
        self,
        nav: Optional[float],
        current_hwm: Optional[float],
    ) -> Optional[float]:
        """Return the new high-water mark (never decreases)."""
        if nav is None:
            return current_hwm
        if current_hwm is None:
            return nav
        return max(current_hwm, nav)

    def reset_hwm_on_resume(self, nav: float) -> float:
        """Reset HWM to current NAV when resuming after a kill switch."""
        return nav
