"""Universe definition, strategy parameters, and configuration for v1.0.0."""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Strategy identity
# ---------------------------------------------------------------------------

STRATEGY_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

TRADABLE_UNIVERSE: list[str] = [
    "QQQ",   # US Growth
    "IWM",   # US Small Cap
    "SCHD",  # US Dividend / Quality
    "VEA",   # Developed International
    "VWO",   # Emerging Markets
    "GLD",   # Gold
    "DBC",   # Broad Commodities
    "TLT",   # Long Treasuries
    "SGOV",  # Cash / Treasury Bills
]

# Benchmarks are tracked but never traded.
BENCHMARKS: list[str] = ["VOO", "VTI", "QQQ", "SGOV", "AGG"]

# One asset per family — only one may be selected at a time.
ASSET_FAMILIES: dict[str, list[str]] = {
    "GROWTH": ["QQQ"],
    "SMALL_CAP": ["IWM"],
    "DIVIDEND": ["SCHD"],
    "DEVELOPED_INTL": ["VEA"],
    "EMERGING_MARKETS": ["VWO"],
    "GOLD": ["GLD"],
    "COMMODITIES": ["DBC"],
    "LONG_BOND": ["TLT"],
    "CASH": ["SGOV"],
}

# Reverse mapping: ticker → family name.
TICKER_TO_FAMILY: dict[str, str] = {
    ticker: family
    for family, tickers in ASSET_FAMILIES.items()
    for ticker in tickers
}

# SGOV is never a momentum candidate — it is always the safety asset.
MOMENTUM_CANDIDATES: list[str] = [t for t in TRADABLE_UNIVERSE if t != "SGOV"]

# 60/40 benchmark composition.
SIXTY_FORTY_WEIGHTS: dict[str, float] = {"VOO": 0.60, "AGG": 0.40}

# ---------------------------------------------------------------------------
# Strategy parameters (v1.0.0 — change requires governance review)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MomentumParams:
    """Parameters for the momentum selection pipeline.

    Any change to these values is a *parameter change*, not a bug fix, and
    requires governance review before merging.
    """

    lookback_days: int = 273
    """Far price lookback: adj_close[t - 273]."""

    exclude_recent_days: int = 21
    """Near price: adj_close[t - 21] (excludes most recent month)."""

    trend_filter_window: int = 200
    """SMA window for the trend filter (price must be above SMA)."""

    correlation_window: int = 90
    """Rolling window (trading days) for the correlation filter."""

    correlation_threshold: float = 0.70
    """Maximum allowed pairwise correlation for second asset selection."""

    volatility_window: int = 60
    """Rolling window (trading days) for inverse-vol weighting."""

    min_weight: float = 0.30
    """Minimum weight when two risk assets are selected."""

    max_weight: float = 0.70
    """Maximum weight when two risk assets are selected."""

    single_asset_weight: float = 0.70
    """Weight of the single selected asset when no second asset qualifies."""

    sgov_complement_weight: float = 0.30
    """SGOV complement when only one risk asset is selected."""

    strategy_version: str = STRATEGY_VERSION


@dataclass(frozen=True)
class RiskParams:
    """Risk-control thresholds."""

    stop_loss_pct: float = 0.90
    """Trigger stop if raw_close <= entry_vwap * stop_loss_pct."""

    kill_switch_pct: float = 0.85
    """Trigger kill switch if NAV <= high_water_mark * kill_switch_pct."""

    failure_count_threshold: int = 5
    """Consecutive data failures before fail-safe liquidation."""


@dataclass(frozen=True)
class ReconciliationParams:
    """Tolerances for post-execution reconciliation."""

    share_tolerance: float = 0.0001
    """Maximum acceptable share count difference per ticker."""

    weight_tolerance: float = 0.005
    """Maximum acceptable weight difference per ticker (as a fraction of NAV)."""

    cash_tolerance: float = 1.00
    """Maximum acceptable cash difference in dollars."""


@dataclass(frozen=True)
class EngineConfig:
    """Top-level configuration bundle passed to the backtest engine."""

    momentum_params: MomentumParams = field(default_factory=MomentumParams)
    risk_params: RiskParams = field(default_factory=RiskParams)
    recon_params: ReconciliationParams = field(default_factory=ReconciliationParams)
    initial_capital: float = 100_000.0
