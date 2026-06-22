"""Strategy-level state machine.

Priority order (highest first):
  1. DATA_INTEGRITY — data failure blocks all downstream evaluation
  2. KILLED         — kill switch has been pulled
  3. STOP_TRIGGERED — one or more positions have triggered the stop rule
  4. NORMAL         — standard operation
"""

from __future__ import annotations

from enum import Enum


class StrategyState(str, Enum):
    NORMAL = "NORMAL"
    """Standard operation."""

    STOP_TRIGGERED = "STOP_TRIGGERED"
    """One or more positions triggered the daily-stop rule; exit pending."""

    KILLED = "KILLED"
    """Kill switch activated; full liquidation pending."""

    DATA_INTEGRITY = "DATA_INTEGRITY"
    """Data integrity failure; allocation frozen until next valid bar."""


class RiskStateMachine:
    """Tracks and enforces the strategy state."""

    def __init__(self) -> None:
        self._state: StrategyState = StrategyState.NORMAL
        self._transition_log: list[tuple[StrategyState, StrategyState, str]] = []

    # ------------------------------------------------------------------
    # Read state
    # ------------------------------------------------------------------

    @property
    def state(self) -> StrategyState:
        return self._state

    @property
    def is_normal(self) -> bool:
        return self._state == StrategyState.NORMAL

    @property
    def is_killed(self) -> bool:
        return self._state == StrategyState.KILLED

    @property
    def is_data_integrity_failure(self) -> bool:
        return self._state == StrategyState.DATA_INTEGRITY

    def can_evaluate_kill_switch(self) -> bool:
        """Kill switch cannot be evaluated when NAV is invalid."""
        return self._state != StrategyState.DATA_INTEGRITY

    def can_rebalance(self) -> bool:
        """Only allow rebalance in NORMAL state."""
        return self._state == StrategyState.NORMAL

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition(self, new_state: StrategyState, reason: str) -> None:
        old = self._state
        self._state = new_state
        self._transition_log.append((old, new_state, reason))

    def set_data_integrity_failure(self, reason: str) -> None:
        self.transition(StrategyState.DATA_INTEGRITY, reason)

    def clear_data_integrity_failure(self) -> None:
        """Called when all tickers have valid bars on a new trading day."""
        if self._state == StrategyState.DATA_INTEGRITY:
            self.transition(StrategyState.NORMAL, "data_integrity_resolved")

    def set_kill_switch(self, reason: str) -> None:
        self.transition(StrategyState.KILLED, reason)

    def resume_from_kill_switch(self, reason: str) -> None:
        """Resume normal operation after kill switch (at next monthly rebalance)."""
        if self._state == StrategyState.KILLED:
            self.transition(StrategyState.NORMAL, reason)

    def set_stop_triggered(self, reason: str) -> None:
        if self._state == StrategyState.NORMAL:
            self.transition(StrategyState.STOP_TRIGGERED, reason)

    def clear_stop(self) -> None:
        if self._state == StrategyState.STOP_TRIGGERED:
            self.transition(StrategyState.NORMAL, "stop_exits_executed")

    @property
    def transition_log(self) -> list[tuple[StrategyState, StrategyState, str]]:
        return list(self._transition_log)
