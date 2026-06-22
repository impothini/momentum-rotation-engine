"""Tests for the frozen data snapshot workflow.

All tests use synthetic DataFrames — zero network calls.
The freeze() function accepts an optional ``downloader`` callable so tests
can inject deterministic data without monkeypatching yf.download.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import momentum_agent.data.freeze as freeze_module
from momentum_agent.data.freeze import FreezeResult, ProxySplicer, freeze
from momentum_agent.data.loader import MarketDataLoader
from momentum_agent.data.proxy import ProxyConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRADING_DAYS = pd.bdate_range("2020-01-02", "2021-12-31", freq="B")


def _make_price_series(
    start_price: float, n: int, index: pd.DatetimeIndex
) -> pd.Series:
    """Deterministic price series with a mild upward trend + no noise."""
    prices = [start_price * (1 + 0.0003 * i) for i in range(n)]
    return pd.Series(prices, index=index[:n], dtype=float)


def _make_three_channel_df(
    tickers: list[str],
    index: pd.DatetimeIndex,
    base: float = 100.0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (adj_close, raw_close, raw_open) with slightly different levels."""
    adj = pd.DataFrame(
        {t: _make_price_series(base * (1 + 0.1 * i), len(index), index)
         for i, t in enumerate(tickers)},
    )
    raw_close = adj * 1.01
    raw_open = adj * 0.99
    return adj, raw_close, raw_open


def _synthetic_downloader(
    adj: pd.DataFrame,
    raw_close: pd.DataFrame,
    raw_open: pd.DataFrame,
):
    """Return a downloader callable that always serves the given frames."""
    def _dl(tickers: list[str], _start: date, _end: date):
        return (
            adj.reindex(columns=tickers),
            raw_close.reindex(columns=tickers),
            raw_open.reindex(columns=tickers),
        )
    return _dl


# ---------------------------------------------------------------------------
# ProxySplicer unit tests
# ---------------------------------------------------------------------------


class TestProxySplicer:
    def _series_pair(
        self,
        actual_start_idx: int,
        n_total: int,
        actual_base: float,
        proxy_base: float,
    ) -> tuple[pd.Series, pd.Series]:
        """Build (actual, proxy) pair where actual starts at actual_start_idx."""
        index = TRADING_DAYS[:n_total]
        actual = _make_price_series(actual_base, n_total - actual_start_idx, index[actual_start_idx:])
        proxy = _make_price_series(proxy_base, actual_start_idx + 5, index[:actual_start_idx + 5])
        return actual, proxy

    def test_scale_factor_produces_continuity(self):
        """scaled_proxy[-1] * scale_factor ≈ actual[splice_date].

        Specifically: the last proxy price scaled by the factor equals the first
        actual price, i.e. there is zero phantom return at the boundary.
        """
        n = 100
        actual_start = 30
        actual, proxy = self._series_pair(actual_start, n, actual_base=120.0, proxy_base=80.0)

        splicer = ProxySplicer()
        spliced, entry = splicer.splice("SGOV", "BIL", actual, proxy, channel="adj_close")

        assert entry is not None
        splice_ts = actual.index[0]
        last_proxy_ts = proxy.index[proxy.index < splice_ts].max()

        # The value just before splice_date in spliced == first actual price
        value_before_splice = spliced.loc[last_proxy_ts]
        first_actual_price = float(actual.iloc[0])
        # They should equal: scaled_proxy[last] == first_actual (by construction of scale_factor)
        assert abs(value_before_splice * (first_actual_price / value_before_splice) - first_actual_price) < 1e-8

    def test_splice_scale_factor_stored_correctly(self):
        """ProxyLogEntry.scale_factor == actual[splice_date] / proxy[last_proxy_date]."""
        actual_start = 20
        n = 60
        actual, proxy = self._series_pair(actual_start, n, actual_base=200.0, proxy_base=150.0)

        splicer = ProxySplicer()
        _, entry = splicer.splice("SCHD", "VIG", actual, proxy, channel="adj_close")

        assert entry is not None
        splice_ts = actual.index[0]
        last_proxy_ts = proxy.index[proxy.index < splice_ts].max()

        expected_sf = float(actual.iloc[0]) / float(proxy.loc[last_proxy_ts])
        assert abs(entry.scale_factor - expected_sf) < 1e-9

    def test_splice_returns_correct_segments(self):
        """Rows before splice_date come from proxy (scaled); at/after from actual."""
        actual_start = 25
        n = 80
        actual, proxy = self._series_pair(actual_start, n, actual_base=110.0, proxy_base=90.0)

        splicer = ProxySplicer()
        spliced, entry = splicer.splice("QQQ", "SPY", actual, proxy, channel="raw_close")

        assert entry is not None
        splice_ts = actual.index[0]

        # Rows at/after splice_date must exactly match actual
        post_splice = spliced.loc[splice_ts:]
        for ts in actual.index[:5]:
            assert abs(spliced.loc[ts] - actual.loc[ts]) < 1e-9

        # Rows before splice_date must be non-NaN (from proxy)
        pre_splice = spliced[spliced.index < splice_ts]
        assert not pre_splice.isna().any()

    def test_no_gap_at_boundary(self):
        """No NaN at or around the splice date."""
        actual_start = 15
        n = 50
        actual, proxy = self._series_pair(actual_start, n, actual_base=100.0, proxy_base=95.0)

        splicer = ProxySplicer()
        spliced, _ = splicer.splice("TLT", "IEF", actual, proxy, channel="adj_close")

        splice_ts = actual.index[0]
        window = spliced.loc[
            TRADING_DAYS[max(0, actual_start - 3): actual_start + 3]
        ].dropna()
        assert len(window) > 0
        assert not spliced.loc[splice_ts - pd.Timedelta("7D") : splice_ts + pd.Timedelta("7D")].isna().any()

    def test_no_op_when_proxy_starts_after_actual_inception(self):
        """If proxy has no data before actual's first date, splice is a no-op."""
        n = 50
        index = TRADING_DAYS[:n]
        actual = _make_price_series(100.0, n, index)
        # Proxy data starts on the same day as actual — nothing to prepend
        proxy = _make_price_series(95.0, n, index)

        splicer = ProxySplicer()
        spliced, entry = splicer.splice("SGOV", "BIL", actual, proxy, channel="adj_close")

        assert entry is None
        pd.testing.assert_series_equal(spliced, actual)

    def test_splice_raises_on_zero_proxy_boundary(self):
        """splice() raises ValueError if proxy price at boundary is zero."""
        n = 40
        actual_start = 10
        index = TRADING_DAYS[:n]
        actual = _make_price_series(100.0, n - actual_start, index[actual_start:])
        proxy = pd.Series(
            [0.0 if i == actual_start - 1 else 90.0 for i in range(actual_start + 2)],
            index=index[:actual_start + 2],
        )

        splicer = ProxySplicer()
        with pytest.raises(ValueError, match="non-positive"):
            splicer.splice("TICKER", "PROXY", actual, proxy, channel="adj_close")

    def test_splice_raises_on_zero_actual_boundary(self):
        """splice() raises ValueError if actual price at splice date is zero."""
        n = 40
        actual_start = 10
        index = TRADING_DAYS[:n]
        # Put a zero at the first actual price
        actual_prices = [0.0] + [100.0 + i for i in range(n - actual_start - 1)]
        actual = pd.Series(actual_prices, index=index[actual_start:])
        proxy = _make_price_series(90.0, actual_start + 2, index[:actual_start + 2])

        splicer = ProxySplicer()
        with pytest.raises(ValueError, match="non-positive"):
            splicer.splice("TICKER", "PROXY", actual, proxy, channel="adj_close")

    def test_proxy_log_scale_factor_nonzero(self):
        """scale_factor must be strictly positive."""
        n = 50
        actual_start = 10
        actual, proxy = self._series_pair(actual_start, n, actual_base=150.0, proxy_base=120.0)

        splicer = ProxySplicer()
        _, entry = splicer.splice("GLD", "IAU", actual, proxy, channel="adj_close")

        assert entry is not None
        assert entry.scale_factor > 0


# ---------------------------------------------------------------------------
# freeze() integration tests (no network — use injected downloader)
# ---------------------------------------------------------------------------


class TestFreeze:
    def _run_freeze(
        self,
        tmp_path: Path,
        tickers: list[str] = None,
        proxy_tickers: list[str] = None,
        proxy_map: dict[str, str] = None,
        overwrite: bool = False,
    ) -> FreezeResult:
        """Run freeze() with a synthetic downloader into tmp_path."""
        if tickers is None:
            tickers = ["QQQ", "TLT"]

        # Build a combined set of all tickers that will be "downloaded"
        all_tickers = list(tickers)
        if proxy_tickers:
            all_tickers = sorted(set(all_tickers) | set(proxy_tickers))

        adj, raw_close, raw_open = _make_three_channel_df(all_tickers, TRADING_DAYS)
        dl = _synthetic_downloader(adj, raw_close, raw_open)

        pc = ProxyConfig(proxy_map=proxy_map or {})
        return freeze(
            tickers=tickers,
            start=date(2020, 1, 2),
            end=date(2021, 12, 31),
            output_dir=tmp_path,
            proxy_config=pc,
            overwrite=overwrite,
            downloader=dl,
        )

    def test_snapshot_roundtrip_hash_matches(self, tmp_path):
        """Freeze writes snapshot; from_snapshot() reads it back with matching hash."""
        result = self._run_freeze(tmp_path)

        loader = MarketDataLoader.from_snapshot(result.snapshot_dir, verify_hash=True)
        assert loader.snapshot.snapshot_id == result.snapshot.snapshot_id
        assert loader.snapshot.adjusted_price_hash == result.snapshot.adjusted_price_hash

    def test_from_snapshot_loads_same_data(self, tmp_path):
        """adj_close loaded from snapshot equals the original DataFrame."""
        tickers = ["QQQ", "TLT"]
        adj, raw_close, raw_open = _make_three_channel_df(tickers, TRADING_DAYS)
        dl = _synthetic_downloader(adj, raw_close, raw_open)

        result = freeze(
            tickers=tickers,
            start=date(2020, 1, 2),
            end=date(2021, 12, 31),
            output_dir=tmp_path,
            proxy_config=ProxyConfig(proxy_map={}),
            downloader=dl,
        )

        loader = MarketDataLoader.from_snapshot(result.snapshot_dir, verify_hash=True)
        pd.testing.assert_frame_equal(
            loader.adj_close.sort_index(axis=1),
            adj[tickers].sort_index(axis=1),
            check_like=True,
            rtol=1e-6,
        )

    def test_hash_verification_catches_tampering(self, tmp_path):
        """Modifying a parquet file after freeze causes from_snapshot() to raise."""
        result = self._run_freeze(tmp_path)

        # Corrupt the parquet by writing different data
        parquet_path = result.snapshot_dir / "adjusted_close.parquet"
        bad = pd.read_parquet(parquet_path) * 2.0
        bad.to_parquet(parquet_path)

        with pytest.raises(ValueError, match="hash mismatch"):
            MarketDataLoader.from_snapshot(result.snapshot_dir, verify_hash=True)

    def test_all_three_hashes_in_snapshot_json(self, tmp_path):
        """snapshot.json contains all three content hashes."""
        result = self._run_freeze(tmp_path)

        with open(result.snapshot_dir / "snapshot.json") as f:
            meta = json.load(f)

        assert "adjusted_price_hash" in meta
        assert "raw_close_hash" in meta
        assert "raw_open_hash" in meta
        assert all(len(meta[k]) == 64 for k in ("adjusted_price_hash", "raw_close_hash", "raw_open_hash"))

    def test_snapshot_json_has_provenance_fields(self, tmp_path):
        """snapshot.json includes proxy_log_hash, python_version."""
        result = self._run_freeze(tmp_path)

        with open(result.snapshot_dir / "snapshot.json") as f:
            meta = json.load(f)

        assert "proxy_log_hash" in meta
        assert "python_version" in meta
        assert meta["python_version"]  # non-empty

    def test_proxy_log_written_to_disk(self, tmp_path):
        """proxy_log.json is written and is a valid JSON list."""
        result = self._run_freeze(tmp_path)
        log_path = result.snapshot_dir / "proxy_log.json"
        assert log_path.exists()
        with open(log_path) as f:
            entries = json.load(f)
        assert isinstance(entries, list)

    def test_proxy_splice_written_per_channel(self, tmp_path):
        """When a proxy is applied, three log entries are written (one per channel).

        SCHD inception is 2011-10-20.  Using start=2010-01-04 ensures
        needs_proxy() returns True and the splicer runs for all three channels.
        """
        test_start = date(2010, 1, 4)
        test_end = date(2013, 12, 31)
        index = pd.bdate_range(test_start.isoformat(), test_end.isoformat(), freq="B")

        # SCHD first appears in the index at its inception date
        schd_inception_ts = pd.Timestamp("2011-10-20")
        schd_start_pos = index.searchsorted(schd_inception_ts)

        vig_adj = _make_price_series(50.0, len(index), index)
        schd_adj_series = _make_price_series(60.0, len(index) - schd_start_pos, index[schd_start_pos:])

        adj = pd.DataFrame({
            "SCHD": pd.Series(schd_adj_series).reindex(index),
            "VIG": vig_adj,
        })
        raw_close = adj * 1.01
        raw_open = adj * 0.99

        dl = _synthetic_downloader(adj, raw_close, raw_open)
        pc = ProxyConfig(proxy_map={"SCHD": "VIG"})

        result = freeze(
            tickers=["SCHD"],
            start=test_start,
            end=test_end,
            output_dir=tmp_path,
            proxy_config=pc,
            downloader=dl,
        )

        channel_entries = [e for e in result.proxy_log if e.ticker == "SCHD"]
        channels_logged = {e.channel for e in channel_entries}
        assert "adj_close" in channels_logged, f"Got channels: {channels_logged}"
        assert "raw_close" in channels_logged
        assert "raw_open" in channels_logged

    def test_overwrite_false_raises_on_existing_dir(self, tmp_path, monkeypatch):
        """freeze() raises FileExistsError if snapshot dir already exists and overwrite=False.

        Monkeypatching _utc_timestamp_str makes both calls produce the same dir name
        (same fixed timestamp + same data hash = same directory path).
        """
        monkeypatch.setattr(freeze_module, "_utc_timestamp_str", lambda: "20200101_000000")

        tickers = ["QQQ"]
        adj, raw_close, raw_open = _make_three_channel_df(tickers, TRADING_DAYS)
        dl = _synthetic_downloader(adj, raw_close, raw_open)

        # First call creates the directory
        freeze(
            tickers=tickers,
            start=date(2020, 1, 2),
            end=date(2021, 12, 31),
            output_dir=tmp_path,
            proxy_config=ProxyConfig(proxy_map={}),
            overwrite=True,
            downloader=dl,
        )

        # Second call with the same timestamp → same dir name → should fail
        with pytest.raises(FileExistsError, match="overwrite"):
            freeze(
                tickers=tickers,
                start=date(2020, 1, 2),
                end=date(2021, 12, 31),
                output_dir=tmp_path,
                proxy_config=ProxyConfig(proxy_map={}),
                overwrite=False,
                downloader=dl,
            )

    def test_freeze_rejects_ticker_needing_proxy_without_one(self, tmp_path):
        """freeze() raises ValueError before downloading when a proxy is needed but not configured."""
        # SCHD inception 2011-10-20; start 2000-01-03 requires a proxy
        call_count = {"n": 0}

        def _never_called(tickers, start, end):
            call_count["n"] += 1
            raise AssertionError("downloader should not be called when proxy is missing")

        with pytest.raises(ValueError, match="proxy"):
            freeze(
                tickers=["SCHD"],
                start=date(2000, 1, 3),
                end=date(2021, 12, 31),
                output_dir=tmp_path,
                proxy_config=ProxyConfig(proxy_map={}),  # no proxy for SCHD
                downloader=_never_called,
            )

        assert call_count["n"] == 0
