"""Main backtest engine.

Backtest loop (exact order from spec):
  1.  Load trading calendar date.
  2.  Load market data.
  3.  Validate market data.
  4.  Handle data-integrity failures.
  5.  Calculate NAV if possible.
  6.  Update HWM if applicable.
  7.  Evaluate kill switch.
  8.  Evaluate daily stops.
  9.  If month-end, generate rebalance targets.
  10. Convert targets to exact share counts.
  11. Submit orders.
  12. Process fills.
  13. Read broker positions.
  14. Reconcile actual vs target.
  15. Emit event logs.
  16. Update portfolio state.

Timing:
  Signal date  = last trading day of month (close prices used for signals)
  Execution    = NEXT trading session open
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from momentum_agent.backtest.calendar import TradingCalendar
from momentum_agent.broker.base import BrokerInterface, Order
from momentum_agent.config import (
    ASSET_FAMILIES,
    TICKER_TO_FAMILY,
    TRADABLE_UNIVERSE,
    EngineConfig,
)
from momentum_agent.data.loader import MarketDataLoader
from momentum_agent.data.validator import validate_bar, validate_signal_inputs
from momentum_agent.events import Event, EventType
from momentum_agent.portfolio.reconciliation import Reconciler
from momentum_agent.portfolio.state import PortfolioState
from momentum_agent.reporting.daily_nav import DailyNavRow, DailyNavWriter
from momentum_agent.reporting.metrics import compute_metrics
from momentum_agent.reporting.trade_log import TradeLogWriter
from momentum_agent.risk.kill_switch import KillSwitchEvaluator
from momentum_agent.risk.state_machine import RiskStateMachine, StrategyState
from momentum_agent.risk.stops import StopLossEvaluator
from momentum_agent.strategies.momentum.selection import AllocationTarget, SelectionPipeline


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    run_id: str
    start_date: date
    end_date: date
    events: list[Event]
    daily_nav_writer: DailyNavWriter
    trade_log_writer: TradeLogWriter
    final_portfolio: PortfolioState
    metrics: dict

    def write_outputs(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.trade_log_writer.write_csv(output_dir / "trade_log.csv")
        self.daily_nav_writer.write_csv(output_dir / "daily_nav.csv")
        with open(output_dir / "metrics.json", "w") as f:
            json.dump(self.metrics, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Order generation helpers
# ---------------------------------------------------------------------------


def _compute_target_shares(
    allocations: dict[str, float],
    nav: float,
    open_prices: dict[str, Optional[float]],
) -> dict[str, float]:
    """Convert weight allocations to share counts using execution open prices.

    target_shares = target_dollar_value / open_price
    Rounded to 6 decimal places.  Residual cash remains cash.
    """
    shares: dict[str, float] = {}
    for ticker, weight in allocations.items():
        dollar_value = nav * weight
        price = open_prices.get(ticker)
        if price is None or price <= 0:
            shares[ticker] = 0.0
        else:
            raw_shares = dollar_value / price
            shares[ticker] = round(raw_shares, 6)
    return shares


def _generate_orders(
    target_shares: dict[str, float],
    current_shares: dict[str, float],
    order_date: date,
    rebalance_id: str,
) -> list[Order]:
    """Generate buy/sell market orders to move from current to target shares."""
    orders: list[Order] = []
    all_tickers = set(target_shares) | set(current_shares)
    for ticker in all_tickers:
        target = target_shares.get(ticker, 0.0)
        current = current_shares.get(ticker, 0.0)
        delta = round(target - current, 9)
        if abs(delta) < 1e-9:
            continue
        orders.append(
            Order(
                ticker=ticker,
                quantity=delta,
                order_type="market",
                order_date=order_date,
                notes=f"rebalance:{rebalance_id}",
            )
        )
    return orders


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class BacktestEngine:
    """Deterministic backtest engine implementing the full v1.0.0 strategy loop."""

    def __init__(
        self,
        config: EngineConfig,
        broker: BrokerInterface,
        data_loader: MarketDataLoader,
        calendar: Optional[TradingCalendar] = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.data_loader = data_loader
        self.calendar = calendar or TradingCalendar()

        self.selection_pipeline = SelectionPipeline(config.momentum_params)
        self.stop_evaluator = StopLossEvaluator(config.risk_params)
        self.kill_switch = KillSwitchEvaluator(config.risk_params)
        self.reconciler = Reconciler(config.recon_params)
        self.state_machine = RiskStateMachine()

        self._portfolio = PortfolioState(cash=config.initial_capital)
        self._nav_writer = DailyNavWriter()
        self._trade_log = TradeLogWriter()
        self._events: list[Event] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        start_date: date,
        end_date: date,
        tickers: Optional[list[str]] = None,
    ) -> BacktestResult:
        """Run the full backtest loop and return results."""
        if tickers is None:
            tickers = TRADABLE_UNIVERSE

        run_id = str(uuid.uuid4())
        trading_days = self.calendar.get_trading_days(start_date, end_date)
        signal_dates = self.calendar.get_month_end_signal_dates(start_date, end_date)

        # Pending execution from previous signal day
        pending_target: Optional[AllocationTarget] = None
        pending_target_date: Optional[date] = None
        pending_rebalance_id: Optional[str] = None
        kill_switch_resume_at: Optional[date] = None
        trade_count = 0

        # Initialise broker cash
        self.broker.set_cash(self.config.initial_capital)

        for day_idx, trading_day in enumerate(trading_days):
            # ============================================================
            # STEP 1: Trading day established (from calendar)
            # ============================================================

            # ============================================================
            # STEP 2-3: Load and validate market data
            # ============================================================
            raw_closes = self.data_loader.get_all_raw_closes(trading_day)
            raw_opens = self.data_loader.get_all_raw_opens(trading_day)

            valid_tickers: set[str] = set()
            any_held_invalid = False

            for ticker in tickers:
                close = raw_closes.get(ticker)
                is_valid = close is not None and close > 0
                if is_valid:
                    self._portfolio.record_valid_bar(ticker)
                    valid_tickers.add(ticker)
                else:
                    failure_count = self._portfolio.record_failure(ticker)
                    if ticker in self._portfolio.positions:
                        any_held_invalid = True
                    # --------------------------------------------------------
                    # STEP 4: Handle data-integrity failures
                    # --------------------------------------------------------
                    self._emit(
                        Event(
                            date=trading_day,
                            event_type=EventType.DATA_INTEGRITY_FAILURE,
                            ticker=ticker,
                            data_status=f"missing_or_invalid_price",
                            reason=f"failure_count={failure_count}",
                        )
                    )
                    # Fail-safe liquidation if failure_count >= threshold
                    if (
                        failure_count >= self.config.risk_params.failure_count_threshold
                        and ticker in self._portfolio.positions
                    ):
                        self._emit(
                            Event(
                                date=trading_day,
                                event_type=EventType.FAILSAFE_LIQUIDATION,
                                ticker=ticker,
                                reason=f"failure_count={failure_count}",
                            )
                        )
                        # Liquidate at next open — add to lockout and
                        # ensure pending_target includes selling this position.
                        self._portfolio.lockout_set.add(ticker)

            # ============================================================
            # STEP 4 (continued): Data integrity state
            # ============================================================
            if any_held_invalid:
                self.state_machine.set_data_integrity_failure(
                    "held_position_missing_price"
                )
            elif self.state_machine.is_data_integrity_failure:
                self.state_machine.clear_data_integrity_failure()

            # ============================================================
            # EXECUTE PENDING ORDERS (from previous signal day)
            # Executed at TODAY's open prices
            # ============================================================
            if pending_target is not None and day_idx > 0:
                # Check if kill switch already consumed this pending target
                if self.state_machine.is_killed:
                    # Execute kill switch liquidation
                    self._execute_kill_switch_liquidation(
                        trading_day, raw_opens, pending_rebalance_id or "kill_switch"
                    )
                    pending_target = None
                    pending_target_date = None
                    trade_count += 1
                else:
                    trade_count += self._execute_rebalance(
                        trading_day,
                        pending_target,
                        raw_opens,
                        pending_rebalance_id or run_id,
                    )
                    pending_target = None
                    pending_target_date = None

            # ============================================================
            # STEP 5: Calculate NAV
            # ============================================================
            nav = self._portfolio.nav(raw_closes)

            # ============================================================
            # STEP 6: Update HWM
            # ============================================================
            if nav is not None:
                self._portfolio.high_water_mark = self.kill_switch.update_hwm(
                    nav, self._portfolio.high_water_mark
                )

            # ============================================================
            # STEP 7: Evaluate kill switch
            # ============================================================
            kill_triggered = False
            if (
                not self.state_machine.is_data_integrity_failure
                and not self.state_machine.is_killed
            ):
                if self.kill_switch.should_trigger(
                    nav, self._portfolio.high_water_mark
                ):
                    self.state_machine.set_kill_switch(
                        f"nav={nav:.2f} hwm={self._portfolio.high_water_mark:.2f}"
                    )
                    kill_triggered = True
                    self._emit(
                        Event(
                            date=trading_day,
                            event_type=EventType.KILL_SWITCH,
                            cash=self._portfolio.cash,
                            reason=(
                                f"nav={nav:.2f} <= "
                                f"hwm={self._portfolio.high_water_mark:.2f} * "
                                f"{self.config.risk_params.kill_switch_pct}"
                            ),
                        )
                    )
                    # Prepare kill switch execution for next open
                    pending_target = AllocationTarget(
                        allocations={"SGOV": 1.0},
                        reason="kill_switch",
                    )
                    pending_rebalance_id = f"kill_switch_{trading_day}"

            # ============================================================
            # STEP 8: Evaluate daily stops
            # ============================================================
            if not kill_triggered and not self.state_machine.is_killed:
                stop_results = self.stop_evaluator.check_all_positions(
                    self._portfolio.positions, raw_closes
                )
                triggered_stops = self.stop_evaluator.get_triggered(stop_results)

                for sr in triggered_stops:
                    self._portfolio.lockout_set.add(sr.ticker)
                    self._emit(
                        Event(
                            date=trading_day,
                            event_type=EventType.STOP_TRIGGER,
                            ticker=sr.ticker,
                            asset_family=TICKER_TO_FAMILY.get(sr.ticker),
                            fill_price=sr.raw_close,
                            entry_vwap_fill_price=sr.entry_vwap,
                            reason=(
                                f"raw_close={sr.raw_close:.4f} <= "
                                f"entry_vwap={sr.entry_vwap:.4f} * "
                                f"{self.config.risk_params.stop_loss_pct}"
                            ),
                        )
                    )

            # ============================================================
            # STEP 9: If month-end, generate rebalance targets
            # ============================================================
            if (
                trading_day in signal_dates
                and not kill_triggered
                and not self.state_machine.is_killed
                and not self.state_machine.is_data_integrity_failure
            ):
                # Handle kill switch resume
                if kill_switch_resume_at == trading_day:
                    hwm_nav = nav if nav else self._portfolio.high_water_mark
                    self._portfolio.high_water_mark = (
                        self.kill_switch.reset_hwm_on_resume(hwm_nav or 0.0)
                    )
                    self.state_machine.resume_from_kill_switch(
                        "monthly_rebalance_resume"
                    )
                    self._portfolio.lockout_set.clear()
                    kill_switch_resume_at = None

                adj_prices = self.data_loader.adj_close
                valid_for_signals = validate_signal_inputs(
                    adj_prices,
                    tickers,
                    adj_prices.index.get_loc(pd.Timestamp(trading_day))
                    if pd.Timestamp(trading_day) in adj_prices.index
                    else -1,
                    self.config.momentum_params.lookback_days,
                )

                allocation = self.selection_pipeline.run(
                    adj_prices=adj_prices,
                    signal_date=trading_day,
                    valid_tickers={t for t, ok in valid_for_signals.items() if ok},
                    lockout_set=self._portfolio.lockout_set,
                )

                # Clear lockout at rebalance (will be repopulated by fresh stops)
                self._portfolio.lockout_set.clear()

                rebalance_id = f"reb_{trading_day.isoformat()}"
                pending_target = allocation
                pending_target_date = trading_day
                pending_rebalance_id = rebalance_id

                self._emit(
                    Event(
                        date=trading_day,
                        event_type=EventType.REBALANCE,
                        reason=allocation.reason,
                        notes=f"rebalance_id={rebalance_id}; "
                              f"allocations={allocation.allocations}",
                        data_status="ok",
                    )
                )

            elif trading_day in signal_dates and self.state_machine.is_killed:
                # Kill-switch resume happens NEXT month
                kill_switch_resume_at = trading_day  # mark this month's date
                self._emit(
                    Event(
                        date=trading_day,
                        event_type=EventType.NO_ACTION,
                        reason="kill_switch_active_skip_rebalance",
                    )
                )

            # ============================================================
            # STEP 15: Record daily NAV row
            # ============================================================
            positions_value = (
                sum(
                    p.shares * (raw_closes.get(t) or 0.0)
                    for t, p in self._portfolio.positions.items()
                )
            )
            drawdown_pct: Optional[float] = None
            if nav is not None and self._portfolio.high_water_mark:
                drawdown_pct = (nav / self._portfolio.high_water_mark) - 1.0

            self._nav_writer.record(
                DailyNavRow(
                    date=trading_day,
                    nav=nav,
                    cash=self._portfolio.cash,
                    positions_value=positions_value,
                    high_water_mark=self._portfolio.high_water_mark,
                    drawdown_pct=drawdown_pct,
                    strategy_state=self.state_machine.state.value,
                    data_status=(
                        "data_integrity_failure"
                        if any_held_invalid
                        else "ok"
                    ),
                    proxy_used=False,
                )
            )

        # ----------------------------------------------------------------
        # Final metrics
        # ----------------------------------------------------------------
        nav_series = self._nav_writer.get_nav_series()
        metrics = compute_metrics(
            strategy_nav=nav_series,
            trade_count=trade_count,
        )

        return BacktestResult(
            run_id=run_id,
            start_date=start_date,
            end_date=end_date,
            events=list(self._events),
            daily_nav_writer=self._nav_writer,
            trade_log_writer=self._trade_log,
            final_portfolio=self._portfolio,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Internal execution helpers
    # ------------------------------------------------------------------

    def _execute_rebalance(
        self,
        execution_date: date,
        allocation: AllocationTarget,
        raw_opens: dict[str, Optional[float]],
        rebalance_id: str,
    ) -> int:
        """Execute a rebalance and return the number of orders executed."""
        nav = self._portfolio.nav(self.data_loader.get_all_raw_closes(execution_date))
        if nav is None:
            self._emit(
                Event(
                    date=execution_date,
                    event_type=EventType.DATA_INTEGRITY_FAILURE,
                    reason="nav_invalid_on_execution_day",
                )
            )
            return 0

        # Convert weights → target shares
        target_shares = _compute_target_shares(allocation.allocations, nav, raw_opens)

        # Current positions known to engine
        prev_known = self._portfolio.snapshot_positions()

        # Generate orders
        current_broker_shares = self.broker.get_positions()
        orders = _generate_orders(target_shares, current_broker_shares, execution_date, rebalance_id)

        authorized_tickers: set[str] = set()

        for order in orders:
            open_price = raw_opens.get(order.ticker)
            if open_price is None or open_price <= 0:
                self._emit(
                    Event(
                        date=execution_date,
                        event_type=EventType.DATA_INTEGRITY_FAILURE,
                        ticker=order.ticker,
                        reason="missing_open_price_for_execution",
                    )
                )
                continue

            fill = self.broker.submit_order(order, open_price)
            authorized_tickers.add(order.ticker)

            if fill.is_rejected:
                self._emit(
                    Event(
                        date=execution_date,
                        event_type=EventType.ORDER_REJECTED,
                        ticker=order.ticker,
                        order_quantity=order.quantity,
                        reason=fill.rejection_reason,
                    )
                )
                continue

            if fill.is_partial:
                self._emit(
                    Event(
                        date=execution_date,
                        event_type=EventType.PARTIAL_FILL,
                        ticker=order.ticker,
                        order_quantity=order.quantity,
                        filled_quantity=fill.filled_quantity,
                        fill_price=fill.fill_price,
                        reason="partial_fill",
                    )
                )

            # Update portfolio state
            family = TICKER_TO_FAMILY.get(order.ticker, "UNKNOWN")
            if fill.filled_quantity > 0:
                self._portfolio.add_position(
                    order.ticker,
                    fill.filled_quantity,
                    fill.fill_price,
                    execution_date,
                    rebalance_id,
                    family,
                )
                # Emit STOP_EXIT if this is closing a stopped position
                if order.quantity < 0 and order.ticker in allocation.allocations.get(
                    "SGOV", {}
                ):
                    pass  # handled below
            elif fill.filled_quantity < 0:
                self._portfolio.reduce_position(order.ticker, abs(fill.filled_quantity))

            # Emit STOP_EXIT events for positions being fully closed
            if (
                order.quantity < 0
                and self._portfolio.position_shares(order.ticker) <= 1e-9
                and order.ticker != "SGOV"
            ):
                self._emit(
                    Event(
                        date=execution_date,
                        event_type=EventType.STOP_EXIT,
                        ticker=order.ticker,
                        asset_family=family,
                        fill_price=fill.fill_price,
                        filled_quantity=fill.filled_quantity,
                        reason="position_closed",
                    )
                )

            # Emit REBALANCE event for each position change
            pos_after = self._portfolio.positions.get(order.ticker)
            target_w = allocation.allocations.get(order.ticker, 0.0)
            self._emit(
                Event(
                    date=execution_date,
                    event_type=EventType.REBALANCE,
                    ticker=order.ticker,
                    asset_family=family,
                    execution_open_price=open_price,
                    fill_price=fill.fill_price,
                    entry_vwap_fill_price=(
                        pos_after.entry_vwap_fill_price if pos_after else None
                    ),
                    target_weight=target_w,
                    target_shares=target_shares.get(order.ticker, 0.0),
                    actual_shares=self._portfolio.position_shares(order.ticker),
                    order_quantity=order.quantity,
                    filled_quantity=fill.filled_quantity,
                    cash=self.broker.get_cash(),
                    reason=rebalance_id,
                )
            )

        # Update cash
        self._portfolio.cash = self.broker.get_cash()

        # ----------------------------------------------------------------
        # STEP 13-14: Read broker positions, reconcile
        # ----------------------------------------------------------------
        actual_broker_positions = self.broker.get_positions()
        actual_cash = self.broker.get_cash()

        recon_result = self.reconciler.reconcile(
            target_shares=target_shares,
            actual_shares=actual_broker_positions,
            target_cash=self._portfolio.cash,
            actual_cash=actual_cash,
            nav=nav,
            recon_date=execution_date,
        )
        for evt in recon_result.events:
            self._emit(evt)

        # Detect unauthorized changes
        unauth_events = self.reconciler.detect_unauthorized_changes(
            prev_known_shares=prev_known,
            current_broker_shares=actual_broker_positions,
            authorized_tickers=authorized_tickers,
            check_date=execution_date,
        )
        for evt in unauth_events:
            self._emit(evt)

        # Sync portfolio with broker (broker is source of truth)
        self._portfolio.cash = actual_cash
        for ticker, shares in actual_broker_positions.items():
            if ticker not in self._portfolio.positions:
                family = TICKER_TO_FAMILY.get(ticker, "UNKNOWN")
                self._portfolio.add_position(
                    ticker, shares, raw_opens.get(ticker) or 0.0,
                    execution_date, rebalance_id, family
                )
            else:
                self._portfolio.positions[ticker].shares = shares
        # Remove positions no longer held by broker
        for ticker in list(self._portfolio.positions.keys()):
            if ticker not in actual_broker_positions:
                del self._portfolio.positions[ticker]

        self._portfolio.last_known_positions = dict(actual_broker_positions)
        self._portfolio.last_rebalance_date = execution_date
        self._portfolio.last_rebalance_id = rebalance_id

        return len(orders)

    def _execute_kill_switch_liquidation(
        self,
        execution_date: date,
        raw_opens: dict[str, Optional[float]],
        rebalance_id: str,
    ) -> None:
        """Liquidate all positions and move to SGOV."""
        current = self.broker.get_positions()
        nav = self._portfolio.nav(self.data_loader.get_all_raw_closes(execution_date))

        # Liquidate everything
        for ticker, shares in current.items():
            if ticker == "SGOV":
                continue
            open_price = raw_opens.get(ticker)
            if open_price and open_price > 0:
                order = Order(
                    ticker=ticker,
                    quantity=-shares,
                    order_type="market",
                    order_date=execution_date,
                    notes="kill_switch_liquidation",
                )
                fill = self.broker.submit_order(order, open_price)
                if not fill.is_rejected:
                    self._portfolio.reduce_position(ticker, abs(fill.filled_quantity))

        # Buy SGOV with remaining cash
        self._portfolio.cash = self.broker.get_cash()
        sgov_price = raw_opens.get("SGOV")
        if sgov_price and sgov_price > 0 and self._portfolio.cash > 0:
            sgov_shares = round(self._portfolio.cash / sgov_price, 6)
            order = Order(
                ticker="SGOV",
                quantity=sgov_shares,
                order_type="market",
                order_date=execution_date,
                notes="kill_switch_sgov",
            )
            fill = self.broker.submit_order(order, sgov_price)
            if not fill.is_rejected:
                self._portfolio.add_position(
                    "SGOV", fill.filled_quantity, fill.fill_price,
                    execution_date, rebalance_id, "CASH"
                )

        self._portfolio.cash = self.broker.get_cash()

    def _emit(self, event: Event) -> None:
        event.strategy_version = self.config.momentum_params.strategy_version
        self._events.append(event)
        self._trade_log.record(event)
