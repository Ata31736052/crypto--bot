"""Research-only adapter to replay the exact current-main ApexSignal logic.

The production analyze_symbol() API fetches its own candles. This adapter
temporarily injects preloaded, completed historical frames into main.klines()
without editing main.py. Calls are serialized because the injection is a
temporary module-global replacement; do not call concurrently.
"""
from __future__ import annotations

from threading import RLock

import main

_LOCK = RLock()
_INTERVALS = ("1d", "4h", "1h")


def closed_frame(frame, decision_ms: int, limit: int = 250):
    """Return only candles fully closed strictly before the decision instant."""
    if limit < 1:
        raise ValueError("limit must be positive")
    if "close_time" not in frame.columns:
        raise ValueError("historical frame requires close_time")
    out = frame.loc[frame["close_time"].astype("int64") < int(decision_ms)].tail(limit)
    return out.copy().reset_index(drop=True)


def analyze_current_main_from_history(symbol: str, symbol_frames: dict,
                                      btc_frames: dict, decision_ms: int):
    """Run current main.analyze_symbol() against preloaded historical frames.

    The same current-main add_indicators(), daily_regime(), setup, confirmation,
    trade, and quality-gate code executes. Only the candle source is injected.
    Returns the native (result, stats, error) tuple from main.analyze_symbol().
    """
    if not symbol or not isinstance(symbol_frames, dict) or not isinstance(btc_frames, dict):
        raise ValueError("symbol and historical frame mappings are required")
    if any(k not in symbol_frames for k in _INTERVALS):
        raise ValueError("symbol_frames must contain 1d, 4h, and 1h")
    if any(k not in btc_frames for k in ("1d", "4h")):
        raise ValueError("btc_frames must contain 1d and 4h")

    prepared = {interval: closed_frame(symbol_frames[interval], decision_ms)
                for interval in _INTERVALS}
    btc_d_frame = closed_frame(btc_frames["1d"], decision_ms)
    btc_h_frame = closed_frame(btc_frames["4h"], decision_ms)
    if min(len(prepared["1d"]), len(prepared["4h"]), len(prepared["1h"]),
           len(btc_d_frame), len(btc_h_frame)) < 210:
        return None, None, "insufficient historical data"

    btc_d = main.daily_regime(main.add_indicators(btc_d_frame))
    btc_h = main.daily_regime(main.add_indicators(btc_h_frame))

    def injected_klines(requested_symbol, interval, limit=250):
        if requested_symbol != symbol or interval not in prepared:
            raise RuntimeError(f"unexpected historical candle request: {requested_symbol} {interval}")
        return prepared[interval].tail(limit).copy()

    with _LOCK:
        original_klines = main.klines
        try:
            main.klines = injected_klines
            return main.analyze_symbol(symbol, btc_d, btc_h)
        finally:
            main.klines = original_klines
