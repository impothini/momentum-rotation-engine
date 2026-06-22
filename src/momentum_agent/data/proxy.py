"""Proxy mappings for assets whose inception dates precede the backtest start.

Every asset must have an inception_date.  If a backtest start date precedes
that date, an explicit proxy mapping must be provided or the engine will
refuse to run (no silent data gaps).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


# ---------------------------------------------------------------------------
# Inception dates for the v1.0.0 universe
# ---------------------------------------------------------------------------

ASSET_INCEPTION_DATES: dict[str, date] = {
    "QQQ": date(1999, 3, 10),
    "IWM": date(2000, 5, 22),
    "SCHD": date(2011, 10, 20),
    "VEA": date(2007, 7, 26),
    "VWO": date(2005, 3, 10),
    "GLD": date(2004, 11, 18),
    "DBC": date(2006, 2, 3),
    "TLT": date(2002, 7, 30),
    "SGOV": date(2020, 5, 26),
    # Benchmarks
    "VOO": date(2010, 9, 9),
    "VTI": date(2001, 5, 31),
    "AGG": date(2003, 9, 29),
}

# ---------------------------------------------------------------------------
# Default proxy mappings
# ---------------------------------------------------------------------------

# Maps a ticker to the proxy ticker to use before its inception date.
# The engine will substitute proxy prices for the pre-inception period and
# mark affected rows as proxy_used=True.
DEFAULT_PROXY_MAP: dict[str, str] = {
    # SGOV (inception 2020-05-26) → use BIL as the nearest T-bill ETF proxy.
    # BIL launched 2007-05-25, so it covers backtests back to at least 2007.
    # For history before BIL, use ^IRX (13-week T-bill rate) converted to a price series.
    "SGOV": "BIL",
    # SCHD (inception 2011-10-20) → use VIG as dividend-quality proxy.
    "SCHD": "VIG",
    # DBC (inception 2006-02-03) → no liquid proxy for 2005 and earlier.
    # Leave unmapped; engine will refuse backtests before DBC inception
    # unless the caller provides a proxy.
}


# ---------------------------------------------------------------------------
# Proxy configuration
# ---------------------------------------------------------------------------


@dataclass
class ProxyConfig:
    """Proxy substitutions for pre-inception periods.

    proxy_map: ticker → proxy_ticker
    Any ticker not in proxy_map will cause the engine to refuse a backtest
    that starts before the asset's inception date.
    """

    proxy_map: dict[str, str]

    @classmethod
    def default(cls) -> "ProxyConfig":
        return cls(proxy_map=dict(DEFAULT_PROXY_MAP))

    def needs_proxy(self, ticker: str, start_date: date) -> bool:
        inception = ASSET_INCEPTION_DATES.get(ticker)
        if inception is None:
            return False
        return start_date < inception

    def get_proxy(self, ticker: str) -> str | None:
        return self.proxy_map.get(ticker)

    def validate(self, tickers: list[str], start_date: date) -> list[str]:
        """Return list of tickers that need a proxy but have none configured."""
        missing: list[str] = []
        for ticker in tickers:
            if self.needs_proxy(ticker, start_date) and self.get_proxy(ticker) is None:
                missing.append(ticker)
        return missing
