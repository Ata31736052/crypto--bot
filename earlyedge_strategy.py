"""EarlyEdge Spot Bot v0.1 — isolated 15m/1h BUY-only strategy engine.

This module is deliberately independent from ApexSignal's live 4H engine.
It has no network, Telegram, filesystem, scheduler, or order-execution side effects.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ENGINE_NAMES = ("BOTTOM_REVERSAL", "BREAKOUT", "TREND_PULLBACK", "RANGE")


def indicators(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    if frame is None or not required.issubset(frame.columns):
        raise ValueError(f"OHLCV frame must contain {sorted(required)}")
    x = frame.copy().reset_index(drop=True)
    for col in required:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    close = x["close"]
    x["ema9"] = close.ewm(span=9, adjust=False).mean()
    x["ema21"] = close.ewm(span=21, adjust=False).mean()
    x["ema50"] = close.ewm(span=50, adjust=False).mean()
    x["ema200"] = close.ewm(span=200, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi"] = 100 - 100 / (1 + rs)
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    x["macd_hist"] = (fast - slow) - (fast - slow).ewm(span=9, adjust=False).mean()
    prev = close.shift()
    tr = pd.concat([(x.high-x.low), (x.high-prev).abs(), (x.low-prev).abs()], axis=1).max(axis=1)
    x["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    x["vol_ratio"] = x.volume / x.volume.rolling(20).mean().replace(0, np.nan)
    x["adx_proxy"] = (x.ema21.pct_change(5).abs() * 1000).clip(0, 60)
    return x


def _valid(row) -> bool:
    keys = ("open", "high", "low", "close", "ema9", "ema21", "ema50",
            "rsi", "macd_hist", "atr", "vol_ratio")
    return all(pd.notna(row.get(k)) and np.isfinite(float(row[k])) for k in keys) and row.close > 0 and row.atr > 0


def _evaluate_engine(name: str, q: pd.DataFrame, h: pd.DataFrame) -> dict:
    r, p = q.iloc[-1], q.iloc[-2]
    hr = h.iloc[-1]
    if name == "BOTTOM_REVERSAL":
        low = float(q.low.tail(32).min())
        near_low = r.close <= low * 1.035
        turn = (r.rsi >= p.rsi and r.macd_hist > p.macd_hist and
                (r.close > r.ema9 or r.close > p.high))
        warn = near_low and (r.rsi < 48 or r.macd_hist > p.macd_hist)
        entry = near_low and turn and r.rsi >= 35 and r.vol_ratio >= 0.75
        score = 45*near_low + 35*turn + 10*(r.vol_ratio >= 1.0) + 10*(hr.close >= hr.ema50)
    elif name == "BREAKOUT":
        ceiling = float(q.high.iloc[-21:-1].max())
        breakout = r.close > ceiling and r.close > r.open
        participation = r.vol_ratio >= 1.25
        warn = r.close >= ceiling * 0.995 and r.vol_ratio >= 0.9
        entry = breakout and participation and r.close > hr.ema21
        score = 55*breakout + 25*participation + 20*(hr.close > hr.ema50)
    elif name == "TREND_PULLBACK":
        trend = hr.close > hr.ema50 and hr.ema21 > hr.ema50
        pullback = abs(r.close-r.ema21) / r.close <= 0.012 or r.low <= r.ema21 * 1.008
        reclaim = r.close > r.ema9 and r.close > r.open and r.macd_hist >= p.macd_hist
        warn = trend and pullback
        entry = trend and pullback and reclaim and r.vol_ratio >= 0.75
        score = 40*trend + 30*pullback + 20*reclaim + 10*(r.vol_ratio >= 1.0)
    else:  # RANGE
        window = q.iloc[-41:-1]
        support, resistance = float(window.low.min()), float(window.high.max())
        width = (resistance-support) / max(support, 1e-12)
        at_support = r.close <= support * 1.025
        rejection = r.close > r.open and r.close > p.close and r.rsi >= p.rsi
        quiet = width >= 0.025 and hr.close <= hr.ema50 * 1.04
        warn = at_support and quiet
        entry = at_support and rejection and quiet and r.vol_ratio >= 0.65
        score = 40*at_support + 25*rejection + 20*quiet + 15*(r.vol_ratio >= 0.9)
    return {"engine": name, "early_warning": bool(warn), "entry": bool(entry),
            "score": int(round(float(score))), "close": float(r.close),
            "atr": float(r.atr), "rsi": float(r.rsi), "volume_ratio": float(r.vol_ratio)}


def evaluate(ohlcv_15m: pd.DataFrame, ohlcv_1h: pd.DataFrame) -> dict:
    """Evaluate closed-candle inputs and return engine candidates; never sends alerts."""
    if len(ohlcv_15m) < 220 or len(ohlcv_1h) < 220:
        return {"status": "INSUFFICIENT_HISTORY", "candidates": []}
    q, h = indicators(ohlcv_15m), indicators(ohlcv_1h)
    # Caller must supply closed candles only. Reject malformed latest rows.
    if not _valid(q.iloc[-1]) or not _valid(q.iloc[-2]) or not _valid(h.iloc[-1]):
        return {"status": "INVALID_DATA", "candidates": []}
    candidates = [_evaluate_engine(n, q, h) for n in ENGINE_NAMES]
    candidates = [c for c in candidates if c["early_warning"] or c["entry"]]
    candidates.sort(key=lambda c: (c["entry"], c["score"]), reverse=True)
    return {"status": "OK", "candidates": candidates}


def build_risk_levels(entry: float, atr: float, resistance: float | None = None) -> dict | None:
    """Long-only ATR stop and minimum 1.8R/2.4R targets; reject impossible geometry."""
    if not np.isfinite(entry) or not np.isfinite(atr) or entry <= 0 or atr <= 0:
        return None
    sl = entry - 1.5 * atr
    risk = entry - sl
    if sl <= 0 or risk / entry > 0.15:
        return None
    tp1, tp2 = entry + 1.8*risk, entry + 2.4*risk
    if resistance is not None and np.isfinite(resistance) and resistance < tp1:
        return None
    return {"entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
            "rr1": 1.8, "rr2": 2.4, "risk_pct": risk/entry*100}
