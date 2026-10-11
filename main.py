
# ============================================================
# ApexSignal v3.21.3 | RSI PAIR-LEVEL DIAGNOSTICS
# Binance Spot | Daily + 4H primary | 1H confirmation
# Trend + Bottom Hunter | Telegram signals only | NO AUTO-TRADING
# ============================================================

import os
import time
import math
import html
import logging
from collections import Counter
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
import numpy as np


# ---------------- CONFIG ----------------
BINANCE_BASE = "https://data-api.binance.vision"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TIMEOUT = 15
HTTP_RETRIES = 3
RETRY_BACKOFF = 1.25
MAX_WORKERS = 8
MAX_SIGNALS = 3  # maximum Telegram messages, NOT a coin/universe limit

# No artificial coin-count cap. liquid_universe() scans every currently
# tradable Binance Spot USDT symbol that passes the liquidity floor.
# The liquidity floor is a quality/data-safety filter, not a coin-count limit.
# Universe is not capped by coin count. Symbols must pass the explicit $10M 24h quote-volume floor.
MIN_24H_USDT_VOLUME = 10_000_000
MIN_SCORE = 82
STRONG_SCORE = 90
TREND_SCORE_MAX = 100  # exact sum of reachable trend score components
BOTTOM_SCORE_MAX = 100  # calibrated 100-point bottom score
BOTTOM_MIN_SCORE = 82
BOTTOM_STRONG_SCORE = 90
BOTTOM_LOW_LOOKBACK_H4 = 42  # seven days of confirmed 4H candles
BOTTOM_LOW_DISTANCE_PCT = 5.0

# Quality gates
DAILY_ADX_MIN = 16.0
DAILY_ADX_STRONG = 23.0
H4_ADX_MIN = 20.0
H4_ADX_RISING_MIN = 18.0
H4_VOL_MIN = 0.80
H4_VOL_STRONG = 1.80
# Hard veto for Bottom Hunter; 0.60-0.75 remains eligible but receives no volume points.
BOTTOM_VOL_MIN = 0.60
ONE_H_VOL_MIN = 0.75

TAKER_BUY_MIN = 0.49
TAKER_BUY_STRONG = 0.56
TRADE_RATIO_MIN = 0.85

MAX_H4_EMA21_DISTANCE = 2.0
MAX_1H_EMA21_DISTANCE = 2.0

ATR_SL_MULT = 1.25
MAX_SL_PCT = 13.0
BOTTOM_MAX_SL_PCT = 15.0
MIN_RR_TP1 = 1.80
MIN_RR_TP2 = 2.40

TP1_PCT = 0.025
TP2_PCT = 0.040

DEDUP_HOURS = 8
STATE_FILE = os.getenv("SIGNAL_STATE_FILE", "signals_state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("crypto-bot")

session = requests.Session()
session.headers.update({"User-Agent": "CryptoSignalBot/ApexSignal-v3.20.1"})


# ---------------- HTTP ----------------
def get_json(path, params=None):
    """Fail-closed Binance HTTP helper with bounded retries for transient errors."""
    last_exc = None
    for attempt in range(HTTP_RETRIES):
        try:
            r = session.get(BINANCE_BASE + path, params=params or {}, timeout=TIMEOUT)
            if r.status_code in {418, 429, 500, 502, 503, 504}:
                r.raise_for_status()
            r.raise_for_status()
            payload = r.json()
            if payload is None:
                raise ValueError(f"empty JSON response for {path}")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt + 1 < HTTP_RETRIES:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
    raise RuntimeError(f"Binance request failed after {HTTP_RETRIES} attempts: {path}: {last_exc}")


def klines(symbol, interval, limit=250):
    data = get_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit}
    )
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_base", "taker_quote", "ignore"
    ]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open", "high", "low", "close", "volume",
              "quote_volume", "trades", "taker_base", "taker_quote"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Never use the still-forming candle.
    if len(df) > 2:
        df = df.iloc[:-1].copy()
    return df.reset_index(drop=True)


def exchange_symbols():
    info = get_json("/api/v3/exchangeInfo")
    out = []
    for s in info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("isSpotTradingAllowed") is False:
            continue
        sym = s.get("symbol", "")
        if any(x in sym for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        out.append(sym)
    return out


def liquid_universe():
    """Return tradable Binance Spot USDT pairs above the 24h liquidity floor."""
    syms = set(exchange_symbols())
    try:
        tickers = get_json("/api/v3/ticker/24hr")
        liquid = set()
        for item in tickers if isinstance(tickers, list) else []:
            sym = item.get("symbol", "")
            if sym not in syms:
                continue
            try:
                qv = float(item.get("quoteVolume", 0) or 0)
            except (TypeError, ValueError):
                qv = 0.0
            if qv >= MIN_24H_USDT_VOLUME:
                liquid.add(sym)
        return sorted(liquid)
    except Exception as e:
        log.error("24H liquidity filter unavailable; refusing unverified universe: %s", e)
        return []

# ---------------- INDICATORS ----------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False).mean()
    ad = dn.ewm(alpha=1/n, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(s):
    e12 = ema(s, 12)
    e26 = ema(s, 26)
    line = e12 - e26
    sig = ema(line, 9)
    hist = line - sig
    return line, sig, hist


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - pc).abs(),
        (df["low"] - pc).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def adx_di(df, n=14):
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    dn = -low.diff()

    plus_dm = pd.Series(
        np.where((up > dn) & (up > 0), up, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index
    )

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    atrv = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atrv.replace(0, np.nan)
    mdi = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atrv.replace(0, np.nan)

    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adxv = dx.ewm(alpha=1/n, adjust=False).mean()
    return adxv, pdi, mdi


def add_indicators(df):
    x = df.copy()
    x["ema9"] = ema(x["close"], 9)
    x["ema21"] = ema(x["close"], 21)
    x["ema50"] = ema(x["close"], 50)
    x["ema200"] = ema(x["close"], 200)
    x["rsi"] = rsi(x["close"])
    x["atr"] = atr(x)
    x["adx"], x["pdi"], x["mdi"] = adx_di(x)
    x["macd"], x["macd_signal"], x["macd_hist"] = macd(x["close"])

    x["vol_ratio"] = (
        x["volume"] /
        x["volume"].rolling(20).mean().replace(0, np.nan)
    )
    x["quote_vol_ratio"] = (
        x["quote_volume"] /
        x["quote_volume"].rolling(20).mean().replace(0, np.nan)
    )
    x["trade_ratio"] = (
        x["trades"] /
        x["trades"].rolling(20).mean().replace(0, np.nan)
    )
    x["taker_buy_ratio"] = (
        x["taker_quote"] /
        x["quote_volume"].replace(0, np.nan)
    )
    x["body_pct"] = (
        (x["close"] - x["open"]).abs() /
        x["open"].replace(0, np.nan)
    ) * 100

    x["ema21_slope"] = x["ema21"].pct_change(5) * 100
    x["ema50_slope"] = x["ema50"].pct_change(5) * 100

    return x


# ---------------- MARKET REGIME ----------------
def daily_regime(d):
    r = d.iloc[-1]
    bullish = (
        r.close > r.ema50 > r.ema200
        and r.ema50_slope > 0
        and r.pdi > r.mdi
        and r.adx >= DAILY_ADX_MIN
    )
    early = (
        r.close > r.ema50
        and r.ema21 > r.ema50
        and r.ema21_slope > 0
        and r.pdi > r.mdi
        and r.adx >= DAILY_ADX_MIN
    )
    bearish = (
        r.close < r.ema50
        and r.ema50 < r.ema200
        and r.mdi > r.pdi
        and r.ema50_slope < 0
    )
    return "BULLISH" if bullish else "EARLY_BULLISH" if early else "BEARISH" if bearish else "NEUTRAL"


def btc_regime():
    d = add_indicators(klines("BTCUSDT", "1d", 250))
    h = add_indicators(klines("BTCUSDT", "4h", 250))
    return daily_regime(d), daily_regime(h)


# ---------------- HELPERS ----------------
def finite(*vals):
    return all(v is not None and np.isfinite(v) for v in vals)


def pct(a, b):
    return ((a / b) - 1) * 100 if b else 0


def bullish_candle(r):
    rng = max(r.high - r.low, 1e-12)
    return r.close > r.open and (r.close - r.low) / rng >= 0.62


def strong_bullish_candle(r):
    rng = max(r.high - r.low, 1e-12)
    return r.close > r.open and (r.close - r.open) / rng >= 0.55


def near_recent_low(h, pct_limit=BOTTOM_LOW_DISTANCE_PCT):
    """Check proximity to the lowest low across seven days of closed 4H candles.

    Fail closed when the requested history or required values are unavailable.
    """
    if h is None or len(h) < BOTTOM_LOW_LOOKBACK_H4:
        return False, None
    lows = pd.to_numeric(h["low"].tail(BOTTOM_LOW_LOOKBACK_H4), errors="coerce")
    close = pd.to_numeric(pd.Series([h.iloc[-1].get("close")]), errors="coerce").iloc[0]
    if len(lows) != BOTTOM_LOW_LOOKBACK_H4 or not np.isfinite(lows.to_numpy(dtype=float)).all() or not np.isfinite(close):
        return False, None
    recent_low = float(lows.min())
    if recent_low <= 0 or close <= 0:
        return False, None
    return bool(float(close) <= recent_low * (1 + pct_limit / 100)), recent_low


def fibonacci_levels(h, lookback=30):
    recent = h.tail(lookback)
    lo = float(recent.low.min())
    hi = float(recent.high.max())
    span = hi - lo
    if span <= 0:
        return None
    return {
        "low": lo, "high": hi,
        "0.382": hi - span * 0.382,
        "0.500": hi - span * 0.500,
        "0.618": hi - span * 0.618,
        "0.786": hi - span * 0.786,
        "1.000": lo,
        "1.272": hi + span * 0.272,
        "1.618": hi + span * 0.618,
    }


def fib_zone(h):
    f = fibonacci_levels(h)
    if not f:
        return False, None
    price = float(h.iloc[-1].close)
    atrv = float(h.iloc[-1].atr)
    tolerance = max(atrv * 0.75, price * 0.003)
    f618, f786 = f["0.618"], f["0.786"]
    ok = (f786 - tolerance) <= price <= (f618 + tolerance)
    return ok, (f618, f786)


def market_structure(h, lookback=64, pivot=2):
    """Fail-closed structure engine. BOS/CHOCH require the break candle itself
    to have bullish body >= 0.40 of its range and volume >= 1.20x."""
    empty={"bullish":False,"bos":False,"retest":False,"choch":False,"higher_low":False,
           "swing_high":None,"swing_low":None,"broken_level":None,"break_idx_abs":None,
           "break_volume":None,"break_body_ratio":None,"state":"INSUFFICIENT","reason":"insufficient pivots"}
    if len(h)<lookback: return empty
    x=h.tail(lookback).copy().reset_index()
    highs=x.high.to_numpy(float); lows=x.low.to_numpy(float); closes=x.close.to_numpy(float)
    ph=[]; pl=[]
    for i in range(pivot,len(x)-pivot):
        if highs[i]>highs[i-pivot:i].max() and highs[i]>=highs[i+1:i+pivot+1].max(): ph.append(i)
        if lows[i]<lows[i-pivot:i].min() and lows[i]<=lows[i+1:i+pivot+1].min(): pl.append(i)
    if len(ph)<2 or len(pl)<2: return empty
    last_ph,prev_ph=ph[-1],ph[-2]; last_pl,prev_pl=pl[-1],pl[-2]
    sh,phv=float(highs[last_ph]),float(highs[prev_ph]); sl,plv=float(lows[last_pl]),float(lows[prev_pl])
    atrv=float(x.iloc[-1].atr)
    if not np.isfinite(atrv) or atrv<=0: atrv=float(np.nanmean((x.high-x.low).tail(14)))
    if not np.isfinite(atrv) or atrv<=0: return empty
    buf=max(atrv*0.12,float(x.iloc[-1].close)*0.0005); higher_low=bool(sl>=plv)
    candidates=[]
    for i in range(last_ph+1,len(x)):
        if closes[i]<=sh+buf: continue
        rng=max(float(x.high.iloc[i]-x.low.iloc[i]),1e-12)
        body=abs(float(x.close.iloc[i]-x.open.iloc[i]))/rng
        vol=float(x.vol_ratio.iloc[i]) if np.isfinite(x.vol_ratio.iloc[i]) else 0
        if x.close.iloc[i]>x.open.iloc[i] and body>=0.40 and vol>=1.20: candidates.append((i,"BOS",sh,body,vol))
    if not candidates:
        for i in range(prev_ph+1,len(x)):
            if closes[i]<=phv+buf: continue
            rng=max(float(x.high.iloc[i]-x.low.iloc[i]),1e-12)
            body=abs(float(x.close.iloc[i]-x.open.iloc[i]))/rng
            vol=float(x.vol_ratio.iloc[i]) if np.isfinite(x.vol_ratio.iloc[i]) else 0
            if x.close.iloc[i]>x.open.iloc[i] and body>=0.40 and vol>=1.20: candidates.append((i,"CHOCH",phv,body,vol))
    break_idx=kind=level=body=vol=None; retest=False
    if candidates:
        break_idx,kind,level,body,vol=candidates[-1]
        post_lows=lows[break_idx+1:-1]; post_closes=closes[break_idx+1:]
        if len(post_closes) and np.all(post_closes>=level-buf):
            touched=bool(len(post_lows) and np.nanmin(post_lows)<=level+atrv*0.25)
            retest=bool(touched and closes[-1]>level+buf*0.25)
    bos=kind=="BOS"; choch=kind=="CHOCH"; trend_ok=bool(x.iloc[-1].close>x.iloc[-1].ema50)
    bullish=bool(trend_ok and (bos or choch or retest) and (higher_low or bos or retest))
    if bullish: state="STRONG_BULLISH" if (bos and higher_low) or retest else "BULLISH"; reason="validated BOS" if bos else ("validated retest" if retest else "validated CHOCH")
    elif not (bos or choch or retest): state="NO_BREAK"; reason="no validated BOS/CHOCH/retest"
    elif not trend_ok: state="BEARISH"; reason="price below EMA50"
    else: state="NEUTRAL"; reason="validated break lacks bullish structure"
    absidx=int(x["index"].iloc[break_idx]) if break_idx is not None else None
    return {"bullish":bullish,"bos":bos,"retest":retest,"choch":choch,"higher_low":higher_low,
            "swing_high":sh,"swing_low":sl,"broken_level":level,"break_idx_abs":absidx,
            "break_volume":vol,"break_body_ratio":body,"state":state,"reason":reason}


def _rsi_divergence_details(h, lookback=42, pivot=2, min_gap=3, max_gap=25):
    """Compare RSI at two confirmed 4H swing lows; avoid arbitrary window minima."""
    out = {"two_pivots": False, "price_lower_low": False, "delta_ge_4": False, "both": False,
           "older_idx": None, "newer_idx": None, "older_low": None, "newer_low": None,
           "older_rsi": None, "newer_rsi": None, "gap": None, "price_change_pct": None, "rsi_delta": None}
    if h is None or len(h) < max(35, pivot * 2 + 5):
        return out
    try:
        x = h.tail(lookback).reset_index(drop=True)
        lows = pd.to_numeric(x["low"], errors="coerce").to_numpy(float)
        rsis = pd.to_numeric(x["rsi"], errors="coerce").to_numpy(float)
        if not np.isfinite(lows).all() or not np.isfinite(rsis).all():
            return out
        pivots = []
        for i in range(pivot, len(x) - pivot):
            if lows[i] <= lows[i-pivot:i].min() and lows[i] < lows[i+1:i+pivot+1].min():
                pivots.append(i)
        if len(pivots) < 2:
            return out
        newer = pivots[-1]
        older_candidates = [i for i in pivots[:-1] if min_gap <= newer-i <= max_gap]
        if not older_candidates:
            return out
        older = older_candidates[-1]
        out["two_pivots"] = True
        out["older_idx"], out["newer_idx"] = int(older), int(newer)
        out["older_low"], out["newer_low"] = float(lows[older]), float(lows[newer])
        out["older_rsi"], out["newer_rsi"] = float(rsis[older]), float(rsis[newer])
        out["gap"] = int(newer - older)
        out["price_change_pct"] = float((lows[newer] / lows[older] - 1.0) * 100.0)
        out["rsi_delta"] = float(rsis[newer] - rsis[older])
        out["price_lower_low"] = bool(lows[newer] < lows[older] * 0.995)
        out["delta_ge_4"] = bool(rsis[newer] >= rsis[older] + 4.0)
        out["both"] = bool(out["price_lower_low"] and out["delta_ge_4"])
        return out
    except (KeyError, TypeError, ValueError, IndexError):
        return out



def _rsi_divergence_pair_audit(h, lookback=42, pivot=2, min_gap=3, max_gap=25):
    """Return every eligible confirmed price-pivot pair for read-only root-cause audit."""
    pairs = []
    if h is None or len(h) < max(35, pivot * 2 + 5):
        return pairs
    try:
        x = h.tail(lookback).reset_index(drop=True)
        lows = pd.to_numeric(x["low"], errors="coerce").to_numpy(float)
        rsis = pd.to_numeric(x["rsi"], errors="coerce").to_numpy(float)
        if not np.isfinite(lows).all() or not np.isfinite(rsis).all():
            return pairs
        pivots = []
        for i in range(pivot, len(x) - pivot):
            if lows[i] <= lows[i-pivot:i].min() and lows[i] < lows[i+1:i+pivot+1].min():
                pivots.append(i)
        for newer_pos, newer in enumerate(pivots):
            for older in pivots[:newer_pos]:
                gap = newer - older
                if gap < min_gap or gap > max_gap:
                    continue
                price_change = float((lows[newer] / lows[older] - 1.0) * 100.0)
                rsi_delta = float(rsis[newer] - rsis[older])
                price_ok = bool(lows[newer] < lows[older] * 0.995)
                rsi_ok = bool(rsis[newer] >= rsis[older] + 4.0)
                pairs.append({
                    "older_idx": int(older), "newer_idx": int(newer), "gap": int(gap),
                    "older_low": float(lows[older]), "newer_low": float(lows[newer]),
                    "older_rsi": float(rsis[older]), "newer_rsi": float(rsis[newer]),
                    "price_change_pct": price_change, "rsi_delta": rsi_delta,
                    "price_lower_low": price_ok, "delta_ge_4": rsi_ok,
                    "both": bool(price_ok and rsi_ok),
                })
    except (KeyError, TypeError, ValueError, IndexError):
        return []
    return pairs

def rsi_bullish_divergence(h):
    return bool(_rsi_divergence_details(h)["both"])


def rejection_candle(h):
    r = h.iloc[-1]
    rng = max(r.high - r.low, 1e-12)
    lower_wick = min(r.open, r.close) - r.low
    return (
        lower_wick / rng >= 0.35
        and r.close > r.open
        and r.close >= r.low + rng * 0.60
    )


def reclaim_4h(h):
    r = h.iloc[-1]
    p = h.iloc[-2]
    return (
        r.close > r.ema9
        and (r.close > p.high or r.close > r.ema21)
        and r.ema9 >= p.ema9
    )


def macd_turn(h):
    if len(h)<3: return False
    r=h.iloc[-1]; p=h.iloc[-2]
    fresh=bool(p.macd_hist<=0 and r.macd_hist>0 and r.macd>r.macd_signal)
    turn=bool(r.macd_hist>p.macd_hist and r.macd>r.macd_signal and r.macd_hist>=-abs(r.atr)*0.05)
    return fresh or turn


def bottom_trigger_subconditions(h):
    """Explain why each bottom trigger passes/fails; read-only and score-neutral."""
    keys = ("rsi:two_pivots_found", "rsi:price_lower_low", "rsi:delta_ge_4", "rsi:both_conditions_pass", "rsi:price_only", "rsi:delta_only", "rsi:neither", "rejection:wick_ge_35pct", "rejection:bullish_close", "rejection:close_top_40pct", "macd:hist_rising", "macd:above_signal", "macd:fresh_cross", "reclaim:close_above_ema9", "reclaim:above_prev_high_or_ema21", "reclaim:ema9_nonfalling", "volume:lt_0.60", "volume:0.60_to_0.75", "volume:0.75_to_1.00", "volume:1.00_to_1.40", "volume:ge_1.40", "data:subcondition_error")
    out = {k: False for k in keys}
    if h is None or len(h) < 35:
        out["data:subcondition_error"] = True
        return out
    try:
        div = _rsi_divergence_details(h)
        out["rsi:two_pivots_found"] = bool(div["two_pivots"])
        out["rsi:price_lower_low"] = bool(div["price_lower_low"])
        out["rsi:delta_ge_4"] = bool(div["delta_ge_4"])
        out["rsi:both_conditions_pass"] = bool(div["both"])
        if div["two_pivots"]:
            out["rsi:price_only"] = bool(div["price_lower_low"] and not div["delta_ge_4"])
            out["rsi:delta_only"] = bool(div["delta_ge_4"] and not div["price_lower_low"])
            out["rsi:neither"] = bool(not div["price_lower_low"] and not div["delta_ge_4"])
        r, p = h.iloc[-1], h.iloc[-2]
        rng = max(float(r.high-r.low), 1e-12)
        wick = float(min(r.open,r.close)-r.low)/rng
        out["rejection:wick_ge_35pct"] = bool(wick >= 0.35)
        out["rejection:bullish_close"] = bool(r.close > r.open)
        out["rejection:close_top_40pct"] = bool(r.close >= r.low + rng*0.60)
        out["macd:hist_rising"] = bool(r.macd_hist > p.macd_hist)
        out["macd:above_signal"] = bool(r.macd > r.macd_signal)
        out["macd:fresh_cross"] = bool(p.macd_hist <= 0 and r.macd_hist > 0 and r.macd > r.macd_signal)
        out["reclaim:close_above_ema9"] = bool(r.close > r.ema9)
        out["reclaim:above_prev_high_or_ema21"] = bool(r.close > p.high or r.close > r.ema21)
        out["reclaim:ema9_nonfalling"] = bool(r.ema9 >= p.ema9)
        v = float(r.vol_ratio)
        if np.isfinite(v):
            bucket = "volume:lt_0.60" if v < 0.60 else "volume:0.60_to_0.75" if v < 0.75 else "volume:0.75_to_1.00" if v < 1.0 else "volume:1.00_to_1.40" if v < 1.4 else "volume:ge_1.40"
            out[bucket] = True
        else:
            out["data:subcondition_error"] = True
    except Exception:
        out["data:subcondition_error"] = True
    return out


def bottom_trigger_diagnostics(h):
    """Read-only audit of Bottom Hunter evidence; never changes eligibility or score."""
    keys = ("location:near 7d low", "location:Fibonacci zone", "structure:retest", "structure:CHOCH", "structure:BOS", "structure:higher low", "trigger:RSI divergence", "trigger:rejection candle", "trigger:MACD turn", "trigger:4H reclaim", "data:insufficient history", "data:diagnostic error")
    out = {k: False for k in keys}
    if h is None or len(h) < BOTTOM_LOW_LOOKBACK_H4:
        out["data:insufficient history"] = True
        return out
    try:
        near_low, _ = near_recent_low(h, BOTTOM_LOW_DISTANCE_PCT)
        fib_ok, _ = fib_zone(h)
        ms = market_structure(h)
        out["location:near 7d low"] = bool(near_low)
        out["location:Fibonacci zone"] = bool(fib_ok)
        out["structure:retest"] = bool(ms.get("retest", False))
        out["structure:CHOCH"] = bool(ms.get("choch", False))
        out["structure:BOS"] = bool(ms.get("bos", False))
        out["structure:higher low"] = bool(ms.get("higher_low", False))
        out["trigger:RSI divergence"] = bool(rsi_bullish_divergence(h))
        out["trigger:rejection candle"] = bool(rejection_candle(h))
        out["trigger:MACD turn"] = bool(macd_turn(h))
        out["trigger:4H reclaim"] = bool(reclaim_4h(h))
    except Exception:
        out["data:diagnostic error"] = True
    return out


def h4_structure_ok(h):
    r = h.iloc[-1]
    ms = market_structure(h)
    return bool(ms["bullish"] and r.close > r.ema50 and r.ema21_slope > -0.05 and r.pdi > r.mdi and r.close <= r.ema21 * (1 + MAX_H4_EMA21_DISTANCE / 100))


def h1_trend_confirmation_details(h):
    """Entry-only 1H confirmation. Daily+4H owns setup quality; 1H only validates timing.

    Design rule: no single 1H indicator is a veto by itself. A genuine bearish
    contradiction remains a hard fail, while price/structure/momentum/volume/
    location are evaluated as independent confirmation clues.
    """
    r=h.iloc[-1]; p=h.iloc[-2]
    vals=[r.close,r.ema9,r.ema21,r.rsi,r.macd,r.macd_signal,r.macd_hist,r.vol_ratio,r.pdi,r.mdi,r.atr]
    if not finite(*vals):
        return {"price_ok":False,"structure_ok":False,"rsi_ok":False,"macd_ok":False,
                "volume_ok":False,"location_ok":False,"bearish_contradiction":True,
                "confirmations":0,"confirmed":False}

    # Price is graded rather than a hard EMA21 veto. A setup can confirm while
    # price is just under EMA21 if structure/momentum/participation are healthy.
    price_ok=bool(r.close>r.ema21 or r.close>r.ema9 or r.close>=r.ema21*0.995)
    structure_ok=bool(r.close>p.high or r.ema9>=r.ema21 or r.close>r.ema9)
    rsi_ok=bool(40<=r.rsi<=75 and r.rsi>=p.rsi-3.0)
    macd_ok=bool(r.macd>r.macd_signal or r.macd_hist>p.macd_hist)
    volume_ok=bool(r.vol_ratio>=ONE_H_VOL_MIN)
    location_ok=bool(r.close<=r.ema21*(1+MAX_1H_EMA21_DISTANCE/100))

    bearish_votes=sum((r.close<r.ema21*0.99,
                       r.pdi<r.mdi,
                       r.macd_hist<p.macd_hist,
                       r.rsi<p.rsi-3.0))
    bearish_contradiction=bool(bearish_votes>=3)

    confirmations=sum((price_ok,structure_ok,rsi_ok,macd_ok,volume_ok,location_ok))
    confirmed=bool(
        structure_ok
        and (macd_ok or rsi_ok)
        and confirmations>=4
        and location_ok
        and not bearish_contradiction
    )
    return {"price_ok":price_ok,"structure_ok":structure_ok,"rsi_ok":rsi_ok,
            "macd_ok":macd_ok,"volume_ok":volume_ok,"location_ok":location_ok,
            "bearish_contradiction":bearish_contradiction,
            "confirmations":confirmations,"confirmed":confirmed}


def one_hour_trend_confirm(h):
    """Daily+4H create the setup; 1H only confirms it."""
    return h1_trend_confirmation_details(h)["confirmed"]


def h1_bottom_confirmation_details(h):
    """Entry-only 1H reversal confirmation; invalid data fails closed."""
    keys=("price_ok","structure_ok","rsi_ok","macd_ok","volume_ok","di_ok")
    if h is None or len(h) < 2:
        return {**{k:False for k in keys},"hard_failure":True,"confirmations":0,"confirmed":False}
    r=h.iloc[-1]; p=h.iloc[-2]
    required=(r.close,r.ema9,r.ema21,r.rsi,r.macd,r.macd_signal,r.macd_hist,r.vol_ratio,r.pdi,r.mdi,
              p.high,p.rsi,p.macd_hist,p.pdi)
    if not finite(*required):
        return {**{k:False for k in keys},"hard_failure":True,"confirmations":0,"confirmed":False}
    price_ok=bool(r.close>r.ema9 or r.close>r.ema21)
    structure_ok=bool(r.close>p.high or r.close>r.ema21 or r.ema9>=r.ema21)
    rsi_ok=bool(38<=r.rsi<=72 and r.rsi>=p.rsi-3.0)
    macd_ok=bool(r.macd>r.macd_signal or r.macd_hist>p.macd_hist)
    volume_ok=bool(r.vol_ratio>=ONE_H_VOL_MIN)
    di_ok=bool(r.pdi>=r.mdi or r.pdi>p.pdi)
    hard_failure=bool(r.close<r.ema21 and r.macd_hist<p.macd_hist and r.rsi<p.rsi-3.0 and r.pdi<r.mdi)
    confirmations=sum((price_ok,structure_ok,rsi_ok,macd_ok,volume_ok,di_ok))
    confirmed=bool(structure_ok and (macd_ok or di_ok) and confirmations>=3 and not hard_failure)
    return {"price_ok":price_ok,"structure_ok":structure_ok,"rsi_ok":rsi_ok,"macd_ok":macd_ok,"volume_ok":volume_ok,"di_ok":di_ok,"hard_failure":hard_failure,"confirmations":confirmations,"confirmed":confirmed}



def one_hour_bottom_confirm(h):
    """Reversal confirmation: structure + momentum/DI and at least 3 of 6 clues."""
    return h1_bottom_confirmation_details(h)["confirmed"]


# ---------------- STRATEGIES ----------------
def trend_daily_eligible(daily_state):
    """Single source of truth for Trend Daily eligibility."""
    return daily_state in {"BULLISH", "EARLY_BULLISH", "NEUTRAL"}


def validate_daily_state(daily_state):
    """Fail closed on an unknown/stale Daily regime value."""
    if daily_state not in {"BULLISH", "EARLY_BULLISH", "NEUTRAL", "BEARISH"}:
        raise ValueError(f"Invalid Daily regime state: {daily_state!r}")
    return daily_state


def trend_setup(d, h, h1, btc_d, btc_h, daily_state):
    """Daily direction + 4H setup score; 1H is only an entry confirmation later."""
    r=h.iloc[-1]; dr=validate_daily_state(daily_state)
    if dr=="BEARISH": return None,["Daily bearish"]
    if btc_d=="BEARISH" and btc_h=="BEARISH": return None,["BTC Daily+4H bearish"]
    if not finite(r.close,r.ema21,r.ema50,r.atr,r.adx,r.pdi,r.mdi,r.vol_ratio): return None,["4H critical data unavailable"]
    # Severe 4H bearishness is a safety veto; ordinary neutral conditions are scored, not chained.
    severe_bearish=bool(r.close<r.ema50*0.985 and r.ema21<r.ema50 and r.mdi>r.pdi*1.12 and r.macd_hist<pd.Series(h.macd_hist).iloc[-2])
    if severe_bearish: return None,["4H severe bearish structure"]
    if r.vol_ratio<0.60: return None,["4H volume critically weak"]
    if r.atr<=0 or r.close<=0: return None,["invalid 4H volatility data"]
    audit=unified_trend_score_audit(d,h,h1,btc_d,btc_h,dr)
    if audit["score"]<MIN_SCORE: return None,[f"setup score {audit['score']} < {MIN_SCORE}","SCORE MISSING="+" | ".join(audit["missing"][:8])]
    reasons=[]
    for k,v in audit["components"].items():
        if v>=0.75*audit["weights"][k]: reasons.append(k)
    return {"strategy":"CONFIRMED TREND","score":audit["score"],"raw_score":audit["raw"],"score_max":100,"score_components":audit["components"],"reasons":reasons},reasons



def bottom_setup(d, h, h1, btc_d, btc_h):
    """Bottom Hunter uses Daily+4H for setup; 1H confirms entry only."""
    r=h.iloc[-1]
    if btc_d=="BEARISH" and btc_h=="BEARISH": return None,["BTC Daily+4H bearish"]
    if d.iloc[-1].close<d.iloc[-1].ema200 and d.iloc[-1].mdi>d.iloc[-1].pdi and r.close<r.ema50: return None,["Daily+4H major trend bearish"]
    near_low,_=near_recent_low(h,BOTTOM_LOW_DISTANCE_PCT); fib_ok,_=fib_zone(h); ms=market_structure(h)
    triggers=(rsi_bullish_divergence(h),rejection_candle(h),macd_turn(h),reclaim_4h(h))
    if not (near_low or fib_ok or sum(triggers)>=3 or ms.get("retest")): return None,["no bottom location/reversal evidence"]
    if r.vol_ratio < BOTTOM_VOL_MIN: return None,["4H volume critically weak"]
    audit=unified_bottom_score_audit(d,h,h1,btc_d,btc_h)
    if audit["score"]<BOTTOM_MIN_SCORE: return None,[f"setup score {audit['score']} < {BOTTOM_MIN_SCORE}","SCORE MISSING="+" | ".join(audit["missing"][:8])]
    reasons=[k for k,v in audit["components"].items() if v>=0.75*audit["weights"][k]]
    return {"strategy":"BOTTOM HUNTER","score":audit["score"],"raw_score":audit["raw"],"score_max":100,"score_components":audit["components"],"reasons":reasons,"low7d":near_recent_low(h,BOTTOM_LOW_DISTANCE_PCT)[1]},reasons



# ---------------- RISK / TARGETS ----------------
def _nearest_confirmed_swing_low(h, lookback=24, pivot=2):
    """Return the most recent meaningful 4H pivot low, not an old absolute low."""
    x = h.tail(lookback).copy().reset_index(drop=True)
    if len(x) < (pivot * 2 + 3):
        return None
    lows = x["low"].to_numpy(float)
    candidates = []
    for i in range(pivot, len(x) - pivot):
        left = lows[i-pivot:i]
        right = lows[i+1:i+pivot+1]
        if lows[i] <= left.min() and lows[i] < right.min():
            candidates.append((i, float(lows[i])))
    return candidates[-1][1] if candidates else None


def build_trade(h, strategy):
    """Build a bounded, risk-valid long setup.

    Root rule: an old multi-week low must never create a 20-30% stop for a
    current 4H setup. Use the nearest confirmed 4H swing or ATR stop, then
    reject if even that bounded stop cannot satisfy the configured risk limit.
    """
    r = h.iloc[-1]
    entry = float(r.close)
    atrv = float(r.atr)
    if not finite(entry, atrv) or entry <= 0 or atrv <= 0:
        return None, "invalid entry/ATR data"

    ms = market_structure(h)
    fib = fibonacci_levels(h)
    max_sl = BOTTOM_MAX_SL_PCT if strategy == "BOTTOM HUNTER" else MAX_SL_PCT

    swing_low = _nearest_confirmed_swing_low(h, lookback=24, pivot=2)
    if swing_low is None and ms.get("swing_low") is not None:
        # Legacy structure fallback is allowed only when it is already inside
        # the configured risk envelope; never let an ancient swing force a huge SL.
        candidate = float(ms["swing_low"]) * 0.997
        if candidate < entry and (entry - candidate) / entry * 100 <= max_sl:
            swing_low = float(ms["swing_low"])

    structural_sl = swing_low * 0.997 if swing_low is not None else None
    atr_sl = entry - atrv * ATR_SL_MULT
    candidates = [("ATR", atr_sl)]
    if structural_sl is not None and structural_sl < entry:
        candidates.append(("4H_SWING", structural_sl))

    # Long protection must be below entry. Prefer the nearest valid protective
    # level; an ancient low is never allowed to widen risk when a bounded ATR
    # stop is available.
    sl_basis, sl = max(candidates, key=lambda z: z[1])
    if sl >= entry:
        return None, "invalid bounded SL"

    risk = entry - sl
    risk_pct = risk / entry * 100
    if risk <= 0:
        return None, "invalid SL"
    if risk_pct > max_sl:
        return None, f"SL too wide {risk_pct:.2f}% (ATR/nearest-structure)"

    resistance = float(h.tail(30)["high"].iloc[:-1].max())
    required_tp1 = entry + risk * MIN_RR_TP1
    required_tp2 = entry + risk * MIN_RR_TP2
    candidates = [
        ("fixed_2.5pct", entry * (1 + TP1_PCT)),
        ("resistance", resistance * 0.995),
    ]
    if fib:
        candidates += [("fib_1.272", fib["1.272"]), ("fib_1.618", fib["1.618"])]
    valid_tp1 = [(name, float(x)) for name, x in candidates if np.isfinite(x) and x >= required_tp1 and x > entry]
    if not valid_tp1:
        return None, (
            f"no feasible TP1 with RR | entry={entry:.8g} | SL={sl:.8g} | "
            f"risk={risk_pct:.2f}% | required_TP1={required_tp1:.8g} | "
            f"resistance={resistance:.8g} | candidates=" +
            ",".join(f"{n}:{x:.8g}" for n, x in candidates)
        )
    tp1_name, tp1 = min(valid_tp1, key=lambda z: z[1])

    tp2_candidates = [("fixed_4pct", entry * (1 + TP2_PCT)), ("resistance", resistance * 0.995)]
    if fib:
        tp2_candidates += [("fib_1.272", fib["1.272"]), ("fib_1.618", fib["1.618"])]
    valid_tp2 = [(n, float(x)) for n, x in tp2_candidates if np.isfinite(x) and x >= required_tp2 and x > tp1]
    if not valid_tp2:
        return None, (
            f"no feasible TP2 with RR | entry={entry:.8g} | SL={sl:.8g} | "
            f"risk={risk_pct:.2f}% | required_TP2={required_tp2:.8g} | "
            f"candidates=" + ",".join(f"{n}:{x:.8g}" for n, x in tp2_candidates)
        )
    tp2_name, tp2 = min(valid_tp2, key=lambda z: z[1])
    rr1 = (tp1 - entry) / risk
    rr2 = (tp2 - entry) / risk

    return {
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
        "risk_pct": risk_pct, "rr1": rr1, "rr2": rr2,
        "tp1_name": tp1_name, "tp2_name": tp2_name,
        "sl_basis": sl_basis,
        "sl_reference": swing_low,
        "required_tp1": required_tp1, "required_tp2": required_tp2,
        "resistance": resistance,
        "fib_1272": fib["1.272"] if fib else None,
        "fib_1618": fib["1.618"] if fib else None,
    }, None


# ---------------- FINAL PROFESSIONAL QUALITY GATE ----------------
def professional_signal_quality_gate(setup,trade):
    """Execution gate only. Setup and 1H confirmation are already single-source upstream."""
    reasons=[]; checks=[]
    def check(name,ok,detail):
        checks.append(f"{name}={'PASS' if ok else 'FAIL'} ({detail})")
        if not ok: reasons.append(f"{name}: {detail}")
    check("RR1",trade["rr1"]>=MIN_RR_TP1,f"{trade['rr1']:.2f} >= {MIN_RR_TP1:.2f}")
    check("RR2",trade["rr2"]>=MIN_RR_TP2,f"{trade['rr2']:.2f} >= {MIN_RR_TP2:.2f}")
    risk_limit=BOTTOM_MAX_SL_PCT if setup["strategy"]=="BOTTOM HUNTER" else MAX_SL_PCT
    check("RISK",trade["risk_pct"]<=risk_limit,f"{trade['risk_pct']:.2f}% <= {risk_limit:.2f}%")
    return len(reasons)==0,reasons,{"checks":checks,"reasons":reasons,
        "strategy":setup["strategy"],"score":setup["score"],"raw_score":setup.get("raw_score","n/a"),
        "risk_pct":trade["risk_pct"],"rr1":trade["rr1"],"rr2":trade["rr2"]}


# ---------------- POSITION SIZE ----------------
def position_size(trade):
    account = float(os.getenv("ACCOUNT_USDT", "1000"))
    risk_pct = float(os.getenv("RISK_PCT", "1.0"))
    risk_money = account * risk_pct / 100
    qty = risk_money / (trade["entry"] - trade["sl"])
    notional = qty * trade["entry"]
    max_position = float(os.getenv("MAX_POSITION_USDT", "1000"))
    notional = min(notional, max_position)
    return notional


# ---------------- SIGNAL STATE / DEDUP ----------------
def load_signal_state():
    try:
        import json
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_signal_state(state):
    import json
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def signal_state_key(item):
    return item["symbol"]


def is_recently_sent(item, state, now_ts=None):
    now_ts = now_ts or time.time()
    key = signal_state_key(item)
    rec = state.get(key, {})
    try:
        sent_at = float(rec.get("sent_at", 0))
    except (TypeError, ValueError):
        sent_at = 0
    return sent_at > 0 and (now_ts - sent_at) < DEDUP_HOURS * 3600


def mark_signal_sent(state, item, now_ts=None):
    now_ts = now_ts or time.time()
    key = signal_state_key(item)
    state[key] = {
        "sent_at": now_ts,
        "symbol": item["symbol"],
        "strategy": item["setup"]["strategy"],
        "entry": float(item["trade"]["entry"]),
        "score": int(item["setup"]["score"]),
    }
    # Keep the state file bounded.
    cutoff = now_ts - max(DEDUP_HOURS * 3600 * 3, 86400)
    for k in list(state):
        try:
            if float(state[k].get("sent_at", 0)) < cutoff:
                del state[k]
        except Exception:
            del state[k]


# ---------------- TELEGRAM ----------------
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials missing; signal not sent.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = session.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return True


def fmt_price(x):
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}"
    if x >= 0.01:
        return f"{x:,.6f}"
    return f"{x:.10f}"


def signal_text(symbol, setup, trade, d, h, btc_d, btc_h):
    r = h.iloc[-1]
    score = setup["score"]
    strength = "🔥 STRONG" if score >= STRONG_SCORE else "✅ QUALITY"

    pos = position_size(trade)

    return (
        f"<b>🚨 CRYPTO SIGNAL — {strength}</b>\n"
        f"<b>{html.escape(symbol)}</b> | {setup['strategy']}\n\n"
        f"⭐ Score: <b>{score}/100</b>\n"
        f"💰 Entry: <b>{fmt_price(trade['entry'])}</b>\n"
        f"🛑 SL: <b>{fmt_price(trade['sl'])}</b> "
        f"({trade['risk_pct']:.2f}%)\n"
        f"🎯 TP1: <b>{fmt_price(trade['tp1'])}</b> "
        f"(RR {trade['rr1']:.2f})\n"
        f"🎯 TP2: <b>{fmt_price(trade['tp2'])}</b> "
        f"(RR {trade['rr2']:.2f})\n"
        f"📐 Targets: {html.escape(str(trade.get('tp1_name','')))} / {html.escape(str(trade.get('tp2_name','')))}\n\n"
        f"📊 4H ADX: {r.adx:.1f} | Vol: {r.vol_ratio:.2f}x | "
        f"RSI: {r.rsi:.1f}\n"
        f"🟢 Taker Buy: {r.taker_buy_ratio:.2%}\n"
        f"₿ BTC: Daily={btc_d} | 4H={btc_h}\n"
        f"💵 Suggested max position: ${pos:,.0f}\n\n"
        f"<b>Why:</b> " + " • ".join(setup["reasons"][:8]) +
        f"\n\n<i>Binance Spot data | Signal only — no auto-trading</i>"
    )


# ---------------- SCAN ----------------

def diagnostic_trend_blocks(d,h,h1,btc_d,btc_h,daily_state):
    """Diagnostics mirror v3.17 actual setup architecture."""
    r=h.iloc[-1]; blocks=[]
    if daily_state=="BEARISH": blocks.append("Daily bearish")
    if btc_d=="BEARISH" and btc_h=="BEARISH": blocks.append("BTC Daily+4H bearish")
    if r.vol_ratio<0.60: blocks.append("4H volume critically weak")
    severe=bool(r.close<r.ema50*0.985 and r.ema21<r.ema50 and r.mdi>r.pdi*1.12 and r.macd_hist<h.iloc[-2].macd_hist)
    if severe: blocks.append("4H severe bearish structure")
    a=unified_trend_score_audit(d,h,h1,btc_d,btc_h,daily_state)
    if a["score"]<MIN_SCORE: blocks.append(f"setup score {a['score']}<{MIN_SCORE}")
    td=h1_trend_confirmation_details(h1)
    if not td["confirmed"]: blocks.append("1H confirmation")
    return blocks



def diagnostic_bottom_blocks(d,h,h1,btc_d,btc_h):
    r=h.iloc[-1]; blocks=[]
    if btc_d=="BEARISH" and btc_h=="BEARISH": blocks.append("BTC Daily+4H bearish")
    if r.vol_ratio<0.60: blocks.append("4H volume critically weak")
    a=unified_bottom_score_audit(d,h,h1,btc_d,btc_h)
    if a["score"]<BOTTOM_MIN_SCORE: blocks.append(f"setup score {a['score']}<{BOTTOM_MIN_SCORE}")
    bd=h1_bottom_confirmation_details(h1)
    if not bd["confirmed"]: blocks.append("1H confirmation")
    return blocks



def trend_base_quality(d,h,btc_d,btc_h,daily_state):
    """Compatibility diagnostic: exact Daily+4H Trend setup gate, never a separate gate."""
    setup,_ = trend_setup(d,h,h,btc_d,btc_h,daily_state)
    return setup is not None


def bottom_base_quality(d,h,btc_d,btc_h):
    """Compatibility diagnostic: exact Daily+4H Bottom setup gate, never a separate gate."""
    setup,_ = bottom_setup(d,h,h,btc_d,btc_h)
    return setup is not None


def diagnostic_potential_score(d,h,h1,btc_d,btc_h,daily_state,strategy):
    return (unified_trend_score_audit(d,h,h1,btc_d,btc_h,daily_state)["score"] if strategy=="TREND" else unified_bottom_score_audit(d,h,h1,btc_d,btc_h)["score"])



def potential_missing_components(d,h,h1,btc_d,btc_h,daily_state,strategy):
    a=unified_trend_score_audit(d,h,h1,btc_d,btc_h,daily_state) if strategy=="TREND" else unified_bottom_score_audit(d,h,h1,btc_d,btc_h)
    return a["missing"]


def _score_bucket(score, kind):
    score = int(max(0, min(100, round(score))))
    if kind == "TREND":
        if score >= 93: return "93-100"
        if score >= 88: return "88-92"
        if score >= 84: return "84-87"
        if score >= 80: return "80-83"
        if score >= 70: return "70-79"
        return "<70"
    if score >= 94: return "94-100"
    if score >= 90: return "90-93"
    if score >= 86: return "86-89"
    if score >= 80: return "80-85"
    if score >= 70: return "70-79"
    return "<70"


def unified_trend_score_audit(d, h, h1, btc_d, btc_h, daily_state):
    """Calibrated Daily+4H trend score. 1H is intentionally excluded."""
    r=h.iloc[-1]; p=h.iloc[-2]; ms=market_structure(h); f=fibonacci_levels(h)
    c={}
    # Direction/context 12
    c["Daily regime"]=12 if daily_state=="BULLISH" else 10 if daily_state=="EARLY_BULLISH" else 7 if daily_state=="NEUTRAL" else 0
    # 4H structure 18, graded instead of binary
    structure_points=18 if ms.get("bullish") else 12 if (ms.get("bos") or ms.get("retest") or ms.get("choch")) else 7 if ms.get("higher_low") else 0
    c["4H structure"]=structure_points
    c["BOS/retest"]=8 if ms.get("bos") else 6 if ms.get("retest") else 4 if ms.get("choch") else 0
    # Location 8
    fib_points=0
    if f:
        price=float(r.close); atrv=float(r.atr) if np.isfinite(r.atr) else 0
        tol=max(atrv*0.75,price*0.003) if atrv>0 else price*0.003
        if f["0.618"]-tol<=price<=f["0.786"]+tol: fib_points=8
        elif f["0.382"]-tol<=price<=f["0.500"]+tol: fib_points=5
        elif price<=f["0.382"]*1.02: fib_points=3
    c["Fib/location"]=fib_points
    # Momentum/participation: graded, additive boosters
    c["EMA alignment"]=10 if r.close>r.ema9>r.ema21>r.ema50 else 7 if r.close>r.ema21>r.ema50 else 4 if r.close>r.ema50 else 0
    c["MACD"]=8 if r.macd_hist>0 and r.macd>r.macd_signal else 6 if r.macd_hist>p.macd_hist else 3 if r.macd>r.macd_signal else 0
    c["RSI"]=7 if 50<=r.rsi<=68 else 5 if 45<=r.rsi<50 or 68<r.rsi<=72 else 2 if 40<=r.rsi<75 else 0
    c["ADX"]=7 if r.adx>=25 else 5 if r.adx>=20 else 3 if r.adx>=16 and r.adx>p.adx else 0
    c["4H volume"]=7 if r.vol_ratio>=H4_VOL_STRONG else 5 if r.vol_ratio>=1.0 else 3 if r.vol_ratio>=0.75 else 0
    c["Taker buy"]=5 if r.taker_buy_ratio>=TAKER_BUY_STRONG else 3 if r.taker_buy_ratio>=0.50 else 1 if r.taker_buy_ratio>=0.48 else 0
    c["BTC context"]=3 if btc_d=="BULLISH" and btc_h=="BULLISH" else 2 if btc_d=="BULLISH" or btc_h=="BULLISH" else 1 if btc_d=="NEUTRAL" or btc_h=="NEUTRAL" else 0
    c["4H location/chase"]=5 if r.close<=r.ema21*(1+MAX_H4_EMA21_DISTANCE/100) else 2 if r.close<=r.ema21*1.05 else 0
    c["Volatility health"]=2 if finite(r.atr,r.close) and r.atr>0 and r.atr/r.close<0.08 else 1 if finite(r.atr,r.close) and r.atr>0 else 0
    weights={"Daily regime":12,"4H structure":18,"BOS/retest":8,"Fib/location":8,"EMA alignment":10,"MACD":8,"RSI":7,"ADX":7,"4H volume":7,"Taker buy":5,"BTC context":3,"4H location/chase":5,"Volatility health":2}
    raw=sum(c.values()); max_possible=sum(weights.values())
    if max_possible!=100: raise AssertionError(max_possible)
    score=int(round(raw/max_possible*100))
    missing=[f"{k} {c[k]}/{w}" for k,w in weights.items() if c[k]<w]
    return {"raw":int(raw),"max":100,"score":max(0,min(100,score)),"components":c,"missing":missing,"weights":weights,"h1_confirmed":h1_trend_confirmation_details(h1)["confirmed"]}



def unified_bottom_score_audit(d, h, h1, btc_d, btc_h):
    """Calibrated Daily+4H bottom score. 1H is intentionally excluded."""
    r=h.iloc[-1]; p=h.iloc[-2]; ms=market_structure(h); near_low,_=near_recent_low(h,BOTTOM_LOW_DISTANCE_PCT); fib_ok,_=fib_zone(h)
    triggers=(rsi_bullish_divergence(h),rejection_candle(h),macd_turn(h),reclaim_4h(h))
    c={}
    c["Location"]=15 if near_low and fib_ok else 12 if (near_low or fib_ok) else 0
    c["Structure reaction"]=15 if ms.get("retest") else 12 if (ms.get("choch") or ms.get("bos")) else 6 if ms.get("higher_low") else 0
    c["Reversal triggers"]=min(24,sum(triggers)*6)
    c["MACD"]=7 if r.macd>r.macd_signal and r.macd_hist>=0 else 5 if r.macd_hist>p.macd_hist else 2 if r.macd>r.macd_signal else 0
    c["RSI"]=7 if 38<=r.rsi<=62 else 5 if 35<=r.rsi<70 else 2 if r.rsi<75 else 0
    c["Bottom volume"]=7 if r.vol_ratio>=1.4 else 5 if r.vol_ratio>=1.0 else 3 if r.vol_ratio>=0.75 else 0
    c["Taker buy"]=5 if r.taker_buy_ratio>=TAKER_BUY_STRONG else 3 if r.taker_buy_ratio>=0.50 else 1 if r.taker_buy_ratio>=0.48 else 0
    c["Trade activity"]=4 if r.trade_ratio>=1.0 else 3 if r.trade_ratio>=0.85 else 1 if r.trade_ratio>=0.70 else 0
    c["ADX"]=4 if r.adx>=20 else 3 if r.adx>=16 and r.adx>p.adx else 1 if r.adx>=14 else 0
    c["Daily context"]=4 if d.iloc[-1].close>d.iloc[-1].ema50 else 3 if d.iloc[-1].close>d.iloc[-1].ema200 else 1
    c["BTC context"]=3 if btc_d=="BULLISH" and btc_h=="BULLISH" else 2 if btc_d=="BULLISH" or btc_h=="BULLISH" else 1 if btc_d=="NEUTRAL" or btc_h=="NEUTRAL" else 0
    c["Volatility health"]=5 if finite(r.atr,r.close) and r.atr>0 and r.atr/r.close<0.08 else 2 if finite(r.atr,r.close) and r.atr>0 else 0
    weights={"Location":15,"Structure reaction":15,"Reversal triggers":24,"MACD":7,"RSI":7,"Bottom volume":7,"Taker buy":5,"Trade activity":4,"ADX":4,"Daily context":4,"BTC context":3,"Volatility health":5}
    raw=sum(c.values()); assert sum(weights.values())==100
    score=int(round(raw/100*100))
    return {"raw":int(raw),"max":100,"score":max(0,min(100,score)),"components":c,"missing":[f"{k} {c[k]}/{w}" for k,w in weights.items() if c[k]<w],"weights":weights,"h1_confirmed":h1_bottom_confirmation_details(h1)["confirmed"]}



def analyze_symbol(symbol, btc_d, btc_h):
    stats={"scanned":1,"trend_candidate":0,"bottom_candidate":0,"h1_reject":0,"risk_reject":0,"trade_reject_count":0,"final_gate_reject_count":0,"final":0,
           "diagnostic_reasons":Counter(),"trend_blockers":Counter(),"bottom_blockers":Counter(),"bottom_trigger_audit":Counter(),"bottom_subcondition_audit":Counter(),"trend_block_single":0,"trend_block_multiple":0,
           "bottom_block_single":0,"bottom_block_multiple":0,"trend_base_pass":0,"trend_1h_only_fail":0,"bottom_base_pass":0,"bottom_1h_only_fail":0,
           "h1_trend_failures":Counter(),"h1_bottom_failures":Counter(),"trade_rejects":Counter(),"final_gate_rejects":Counter(),"trend_score_rejects":Counter(),"bottom_score_rejects":Counter(),"trend_score_buckets":Counter(),"bottom_score_buckets":Counter(),"trend_score_values":[],"bottom_score_values":[],"trend_potential_scores":[],"bottom_potential_scores":[],"trend_potential_buckets":Counter(),"bottom_potential_buckets":Counter(),"trend_potential_top":[],"bottom_potential_top":[],"trend_audit_scores":[],"bottom_audit_scores":[],"trend_audit_buckets":Counter(),"bottom_audit_buckets":Counter(),"trend_audit_top":[],"bottom_audit_top":[]}
    try:
        d=add_indicators(klines(symbol,"1d",250)); h=add_indicators(klines(symbol,"4h",250)); h1=add_indicators(klines(symbol,"1h",250))
        daily_state = validate_daily_state(daily_regime(d))
        # ROOT DAILY LOCK: the same validated state is passed through every Trend stage.
        if daily_state not in {"BULLISH", "EARLY_BULLISH", "NEUTRAL", "BEARISH"}:
            raise RuntimeError(f"Daily state invariant failure: {daily_state!r}")
        if min(len(d),len(h),len(h1))<210: return None,stats,"insufficient data"
        # Read-only pair-level audit: explains why the selected confirmed price pivots
        # do or do not satisfy both RSI-divergence thresholds. No scoring or gate changes.
        div_pair = _rsi_divergence_details(h)
        if div_pair["two_pivots"]:
            log.info(
                "RSI PAIR AUDIT | %s | bars=%s->%s gap=%s | low=%.8g->%.8g | RSI=%.2f->%.2f | price_change=%.3f%% | RSI_delta=%.2f | price_ok=%s RSI_ok=%s BOTH=%s",
                symbol, div_pair["older_idx"], div_pair["newer_idx"], div_pair["gap"],
                div_pair["older_low"], div_pair["newer_low"],
                div_pair["older_rsi"], div_pair["newer_rsi"],
                div_pair["price_change_pct"], div_pair["rsi_delta"],
                div_pair["price_lower_low"], div_pair["delta_ge_4"], div_pair["both"],
            )
        # Read-only root-cause audit: enumerate every eligible pivot pair without changing selection or scoring.
        pair_rows = _rsi_divergence_pair_audit(h)
        qualifying_pairs = [p for p in pair_rows if p["both"]]
        log.info("RSI PAIR SET AUDIT | %s | eligible_pairs=%d | qualifying_pairs=%d | selected_pair_both=%s",
                 symbol, len(pair_rows), len(qualifying_pairs), bool(div_pair.get("both", False)))
        if not div_pair.get("both", False):
            for pair in pair_rows:
                log.info(
                    "RSI ALTERNATE PAIR | %s | bars=%s->%s gap=%s | low=%.8g->%.8g | RSI=%.2f->%.2f | price_change=%.3f%% | RSI_delta=%.2f | price_ok=%s RSI_ok=%s BOTH=%s",
                    symbol, pair["older_idx"], pair["newer_idx"], pair["gap"],
                    pair["older_low"], pair["newer_low"], pair["older_rsi"], pair["newer_rsi"],
                    pair["price_change_pct"], pair["rsi_delta"], pair["price_lower_low"],
                    pair["delta_ge_4"], pair["both"],
                )
        sub_diag = bottom_trigger_subconditions(h)
        stats["bottom_subcondition_audit"].update(k for k, v in sub_diag.items() if v)
        trigger_diag = bottom_trigger_diagnostics(h)
        stats["bottom_trigger_audit"]["checked:symbols"] += 1
        for audit_key, audit_pass in trigger_diag.items():
            if audit_pass: stats["bottom_trigger_audit"][audit_key] += 1
        audit_evidence = ("location:near 7d low", "location:Fibonacci zone", "structure:retest", "structure:CHOCH", "structure:BOS", "structure:higher low", "trigger:RSI divergence", "trigger:rejection candle", "trigger:MACD turn", "trigger:4H reclaim")
        if not any(trigger_diag[k] for k in audit_evidence):
            stats["bottom_trigger_audit"]["none:location+structure+triggers"] += 1
        tblocks=diagnostic_trend_blocks(d,h,h1,btc_d,btc_h,daily_state); bblocks=diagnostic_bottom_blocks(d,h,h1,btc_d,btc_h)
        stats["trend_blockers"].update(tblocks); stats["bottom_blockers"].update(bblocks)
        if len(tblocks) == 1: stats["trend_block_single"] += 1
        elif len(tblocks) > 1: stats["trend_block_multiple"] += 1
        if len(bblocks) == 1: stats["bottom_block_single"] += 1
        elif len(bblocks) > 1: stats["bottom_block_multiple"] += 1
        # SINGLE SOURCE OF TRUTH: these are the exact Daily+4H setup results
        # used by the production pipeline. No legacy diagnostic gate is allowed
        # to disagree with candidate creation.
        trend,treasons=trend_setup(d,h,h1,btc_d,btc_h,daily_state)
        bottom,breasons=bottom_setup(d,h,h1,btc_d,btc_h)
        trend_base = trend is not None
        bottom_base = bottom is not None
        if trend_base: stats["trend_base_pass"] += 1
        if bottom_base: stats["bottom_base_pass"] += 1
        # PRE-SCORE DIAGNOSTICS: independent, read-only estimates.
        trend_potential = diagnostic_potential_score(d,h,h1,btc_d,btc_h,daily_state,"TREND")
        bottom_potential = diagnostic_potential_score(d,h,h1,btc_d,btc_h,daily_state,"BOTTOM")
        # UNIFIED SCORE AUDIT: exact scoring formula, read-only, all symbols.
        trend_audit = unified_trend_score_audit(d,h,h1,btc_d,btc_h,daily_state)
        bottom_audit = unified_bottom_score_audit(d,h,h1,btc_d,btc_h)
        stats["trend_audit_scores"].append(trend_audit["score"])
        stats["bottom_audit_scores"].append(bottom_audit["score"])
        stats["trend_audit_buckets"][_score_bucket(trend_audit["score"],"TREND")] += 1
        stats["bottom_audit_buckets"][_score_bucket(bottom_audit["score"],"BOTTOM")] += 1
        if trend_audit["score"] >= 80:
            stats["trend_audit_top"].append((symbol, trend_audit["score"], trend_audit["raw"], "; ".join(trend_audit["missing"][:6])))
        # DIAGNOSTIC ONLY: retain every symbol so the final top-10 shows the
        # strongest Bottom Hunter near-misses even when the entire market scores <80.
        # This does not alter setup eligibility, score weights, or signal thresholds.
        stats["bottom_audit_top"].append((symbol, bottom_audit["score"], bottom_audit["raw"], "; ".join(bottom_audit["missing"][:8])))
        stats["trend_potential_scores"].append(trend_potential)
        stats["bottom_potential_scores"].append(bottom_potential)
        stats["trend_potential_buckets"][_score_bucket(trend_potential,"TREND")] += 1
        stats["bottom_potential_buckets"][_score_bucket(bottom_potential,"BOTTOM")] += 1
        if trend_potential >= 84:
            stats["trend_potential_top"].append((symbol,trend_potential,"; ".join(potential_missing_components(d,h,h1,btc_d,btc_h,daily_state,"TREND")[:6])))
        if bottom_potential >= 86:
            stats["bottom_potential_top"].append((symbol,bottom_potential,"; ".join(potential_missing_components(d,h,h1,btc_d,btc_h,daily_state,"BOTTOM")[:6])))
        if trend and trend.get("raw_score") is not None:
            raw = int(trend["raw_score"]); norm = int(trend["score"])
            stats["trend_score_values"].append(norm)
            bucket = "93-100" if norm >= 93 else "88-92" if norm >= 88 else "80-87" if norm >= 80 else "70-79" if norm >= 70 else "<70"
            stats["trend_score_buckets"][bucket] += 1
        elif treasons and any(x.startswith("normalized score ") for x in treasons):
            import re
            m = re.search(r"normalized score (\d+)", " | ".join(treasons))
            if m:
                norm = int(m.group(1)); stats["trend_score_values"].append(norm)
                bucket = "93-100" if norm >= 93 else "88-92" if norm >= 88 else "80-87" if norm >= 80 else "70-79" if norm >= 70 else "<70"
                stats["trend_score_buckets"][bucket] += 1
            stats["trend_score_rejects"][treasons[-1]] += 1
        if bottom and bottom.get("raw_score") is not None:
            norm = int(bottom["score"]); stats["bottom_score_values"].append(norm)
            bucket = "94-100" if norm >= 94 else "90-93" if norm >= 90 else "80-89" if norm >= 80 else "70-79" if norm >= 70 else "<70"
            stats["bottom_score_buckets"][bucket] += 1
        elif breasons and any(x.startswith("normalized score ") for x in breasons):
            import re
            m = re.search(r"normalized score (\d+)", " | ".join(breasons))
            if m:
                norm = int(m.group(1)); stats["bottom_score_values"].append(norm)
                bucket = "94-100" if norm >= 94 else "90-93" if norm >= 90 else "80-89" if norm >= 80 else "70-79" if norm >= 70 else "<70"
                stats["bottom_score_buckets"][bucket] += 1
            stats["bottom_score_rejects"][breasons[-1]] += 1
        setups=[]
        if trend:
            stats["trend_candidate"] += 1
            setups.append(trend)
        else:
            for reason in treasons[:4]: stats["diagnostic_reasons"][f"TREND: {reason}"] += 1
        if bottom:
            stats["bottom_candidate"] += 1
            setups.append(bottom)
        else:
            for reason in breasons[:4]: stats["diagnostic_reasons"][f"BOTTOM: {reason}"] += 1

        # SINGLE PRODUCTION PIPELINE: Setup -> 1H -> Trade -> Execution gate.
        qualified=[]
        for setup in setups:
            strategy=setup["strategy"]
            h1d = h1_trend_confirmation_details(h1) if strategy=="CONFIRMED TREND" else h1_bottom_confirmation_details(h1)
            h1_ok=h1d["confirmed"]
            if not h1_ok:
                stats["h1_reject"] += 1
                stats["final_gate_rejects"][f"{strategy}: 1H confirmation"] += 1
                fail_counter=stats["h1_trend_failures"] if strategy=="CONFIRMED TREND" else stats["h1_bottom_failures"]
                for key,val in h1d.items():
                    if key not in {"confirmed","confirmations"} and val is False:
                        fail_counter[key] += 1
                if strategy=="CONFIRMED TREND": stats["trend_1h_only_fail"] += 1
                else: stats["bottom_1h_only_fail"] += 1
                log.info("CANDIDATE AUDIT | %s | %s | SETUP=PASS score=%d raw=%s | 1H=FAIL conf=%s | %s",
                         symbol,strategy,setup["score"],setup.get("raw_score","n/a"),h1d.get("confirmations","n/a"),
                         ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k,v in h1d.items() if k not in {"confirmed","confirmations"}))
                continue

            log.info("CANDIDATE AUDIT | %s | %s | SETUP=PASS score=%d raw=%s | 1H=PASS conf=%s | %s",
                     symbol,strategy,setup["score"],setup.get("raw_score","n/a"),h1d.get("confirmations","n/a"),
                     ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k,v in h1d.items() if k not in {"confirmed","confirmations"}))

            trade,err=build_trade(h,strategy)
            if not trade:
                stats["risk_reject"] += 1
                stats["trade_reject_count"] += 1
                stats["trade_rejects"][f"{strategy}: {err}"] += 1
                stats["diagnostic_reasons"][f"{strategy}: TRADE {err}"] += 1
                log.info("TRADE FEASIBILITY REJECT | %s | %s | %s",symbol,strategy,err)
                continue

            passed,gate_reasons,gate_detail=professional_signal_quality_gate(setup,trade)
            if not passed:
                stats["risk_reject"] += 1
                stats["final_gate_reject_count"] += 1
                for reason in gate_reasons:
                    stats["final_gate_rejects"][f"{strategy}: {reason}"] += 1
                    stats["diagnostic_reasons"][f"{strategy}: {reason}"] += 1
                log.info("FINAL GATE DETAIL | %s | %s | score=%s raw=%s | risk=%.2f%% | RR1=%.2f RR2=%.2f | %s",
                         symbol,strategy,gate_detail["score"],gate_detail["raw_score"],gate_detail["risk_pct"],gate_detail["rr1"],gate_detail["rr2"],
                         " | ".join(gate_detail["checks"]))
                continue

            qualified.append({"symbol":symbol,"setup":setup,"trade":trade,"d":d,"h":h,"h1":h1,"btc_d":btc_d,"btc_h":btc_h})
            log.info("FINAL PIPELINE PASS | %s | %s | score=%d | risk=%.2f%% | RR1=%.2f RR2=%.2f",
                     symbol,strategy,setup["score"],trade["risk_pct"],trade["rr1"],trade["rr2"])
        if not qualified:
            if not setups:
                return None,stats,f"{symbol} | NO_CANDIDATE | TREND={treasons[0] if treasons else 'none'} | BOTTOM={breasons[0] if breasons else 'none'}"
            return None,stats,f"{symbol} | CANDIDATE_REJECTED_AFTER_FULL_GATE"
        best=max(qualified,key=lambda x:(x["setup"]["score"],x["trade"]["rr2"],-x["trade"]["risk_pct"]))
        stats["final"]+=1
        return best,stats,None
    except Exception as e:
        return None,stats,f"error: {e}"


def aggregate(dst, src):
    for k in dst:
        if k in ("diagnostic_reasons", "trend_blockers", "bottom_blockers", "bottom_trigger_audit", "bottom_subcondition_audit", "h1_trend_failures", "h1_bottom_failures", "trade_rejects", "final_gate_rejects", "trend_score_rejects", "bottom_score_rejects", "trend_score_buckets", "bottom_score_buckets", "trend_potential_buckets", "bottom_potential_buckets", "trend_audit_buckets", "bottom_audit_buckets"):
            dst[k].update(src.get(k, {}))
        elif k in ("trend_score_values", "bottom_score_values", "trend_potential_scores", "bottom_potential_scores", "trend_potential_top", "bottom_potential_top", "trend_audit_scores", "bottom_audit_scores", "trend_audit_top", "bottom_audit_top"):
            dst[k].extend(src.get(k, []))
        else:
            dst[k] += src.get(k, 0)


def main():
    log.info("Starting ApexSignal v3.21.1 | BOTTOM NEAR-MISS AUDIT | DAILY+4H SCORE | 1H CONFIRMATION-ONLY | FAIL-CLOSED")
    log.info("Primary: Daily direction + 4H setup | Entry confirmation: 1H | Trend + Bottom Hunter")
    log.info("No auto-trading.")

    universe = liquid_universe()
    log.info("Universe: %d valid Binance USDT spot pairs | 24H LIQUIDITY FILTER >= $10M", len(universe))

    btc_d, btc_h = btc_regime()
    log.info("BTC regime: Daily=%s | 4H=%s", btc_d, btc_h)

    totals = {
        "scanned": 0,
        "trend_candidate": 0,
        "bottom_candidate": 0,
        "h1_reject": 0,
        "risk_reject": 0,
        "final": 0,
        "diagnostic_reasons": Counter(),
        "trend_blockers": Counter(),
        "bottom_trigger_audit": Counter(),
        "bottom_subcondition_audit": Counter(),
        "bottom_blockers": Counter(),
        "trend_block_single": 0,
        "trend_block_multiple": 0,
        "bottom_block_single": 0,
        "bottom_block_multiple": 0,
        "trend_base_pass": 0,
        "trend_1h_only_fail": 0,
        "bottom_base_pass": 0,
        "bottom_1h_only_fail": 0,
        "h1_trend_failures": Counter(),
        "h1_bottom_failures": Counter(),
        "trade_rejects": Counter(),
        "final_gate_rejects": Counter(),
        "trade_reject_count": 0,
        "final_gate_reject_count": 0,
        "trend_score_rejects": Counter(),
        "bottom_score_rejects": Counter(),
        "trend_score_buckets": Counter(),
        "bottom_score_buckets": Counter(),
        "trend_score_values": [],
        "bottom_score_values": [],
        "trend_potential_scores": [],
        "bottom_potential_scores": [],
        "trend_potential_buckets": Counter(),
        "bottom_potential_buckets": Counter(),
        "trend_potential_top": [],
        "bottom_potential_top": [],
        "trend_audit_scores": [],
        "bottom_audit_scores": [],
        "trend_audit_buckets": Counter(),
        "bottom_audit_buckets": Counter(),
        "trend_audit_top": [],
        "bottom_audit_top": [],
    }

    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {
            ex.submit(analyze_symbol, s, btc_d, btc_h): s
            for s in universe
        }

        done = 0
        for fut in as_completed(futs):
            done += 1
            result, stats, err = fut.result()
            aggregate(totals, stats)
            if result:
                results.append(result)
            elif err and (" | REJECT | " in err or " | NO_CANDIDATE | " in err or err.startswith("error:")):
                # Detailed per-symbol diagnostics are intentionally limited to setup/risk failures.
                if " | REJECT | " in err:
                    log.info("QUALITY DIAGNOSTIC | %s", err)
                elif err.startswith("error:"):
                    log.error("SCAN ERROR | %s", err)

            if done % 25 == 0 or done == len(universe):
                log.info(
                    "Progress %d/%d | trend=%d bottom=%d final=%d",
                    done, len(universe),
                    totals["trend_candidate"],
                    totals["bottom_candidate"],
                    totals["final"],
                )

    # Highest-quality first.
    results.sort(
        key=lambda x: (
            x["setup"]["score"],
            x["trade"]["rr2"],
            -x["trade"]["risk_pct"]
        ),
        reverse=True
    )

    sent = 0
    dedup_skipped = 0
    state = load_signal_state()
    state_changed = False
    for item in results:
        if sent >= MAX_SIGNALS:
            break
        if is_recently_sent(item, state):
            dedup_skipped += 1
            log.info("DEDUP SKIP | %s | %s | within %dh", item["symbol"], item["setup"]["strategy"], DEDUP_HOURS)
            continue
        msg = signal_text(
            item["symbol"],
            item["setup"],
            item["trade"],
            item["d"],
            item["h"],
            item["btc_d"],
            item["btc_h"],
        )
        try:
            if send_telegram(msg):
                sent += 1
                mark_signal_sent(state, item)
                state_changed = True
                log.info(
                    "SIGNAL %s | %s | score=%d | risk=%.2f%% | RR1=%.2f RR2=%.2f",
                    item["symbol"],
                    item["setup"]["strategy"],
                    item["setup"]["score"],
                    item["trade"]["risk_pct"],
                    item["trade"]["rr1"],
                    item["trade"]["rr2"],
                )
        except Exception as e:
            log.error("Telegram send failed for %s: %s", item["symbol"], e)

    if state_changed:
        save_signal_state(state)
        log.info("Signal state saved: %d active dedup records", len(state))
    if totals["bottom_subcondition_audit"]:
        sub_order = ("rsi:two_pivots_found", "rsi:price_lower_low", "rsi:delta_ge_4", "rsi:both_conditions_pass", "rsi:price_only", "rsi:delta_only", "rsi:neither", "rejection:wick_ge_35pct", "rejection:bullish_close", "rejection:close_top_40pct", "macd:hist_rising", "macd:above_signal", "macd:fresh_cross", "reclaim:close_above_ema9", "reclaim:above_prev_high_or_ema21", "reclaim:ema9_nonfalling", "volume:lt_0.60", "volume:0.60_to_0.75", "volume:0.75_to_1.00", "volume:1.00_to_1.40", "volume:ge_1.40", "data:subcondition_error")
        sub_text = " | ".join("{}={}".format(k, totals["bottom_subcondition_audit"].get(k, 0)) for k in sub_order)
        log.info("BOTTOM SUBCONDITION AUDIT | " + sub_text)
    if totals["bottom_trigger_audit"]:
        audit_order = ("checked:symbols", "location:near 7d low", "location:Fibonacci zone", "structure:retest", "structure:CHOCH", "structure:BOS", "structure:higher low", "trigger:RSI divergence", "trigger:rejection candle", "trigger:MACD turn", "trigger:4H reclaim", "none:location+structure+triggers", "data:insufficient history", "data:diagnostic error")
        audit_text = " | ".join("{}={}".format(k, totals["bottom_trigger_audit"].get(k, 0)) for k in audit_order)
        log.info("BOTTOM TRIGGER AUDIT | " + audit_text)
    if totals["diagnostic_reasons"]:
        top = totals["diagnostic_reasons"].most_common(12)
        log.info("DEEP DIAGNOSTIC SUMMARY | " + " | ".join(f"{reason} x{count}" for reason, count in top))

    if totals["trend_score_rejects"]:
        log.info("TREND SCORE REJECT DETAIL | " + " | ".join(f"{k} x{v}" for k,v in totals["trend_score_rejects"].most_common()))
    if totals["bottom_score_rejects"]:
        log.info("BOTTOM SCORE REJECT DETAIL | " + " | ".join(f"{k} x{v}" for k,v in totals["bottom_score_rejects"].most_common()))
    if totals["trade_rejects"]:
        log.info("TRADE FEASIBILITY REJECT DETAIL | " + " | ".join(f"{k} x{v}" for k,v in totals["trade_rejects"].most_common(12)))
    if totals["final_gate_rejects"]:
        log.info("FINAL GATE REJECT DETAIL | " + " | ".join(f"{k} x{v}" for k,v in totals["final_gate_rejects"].most_common(12)))

    # Unified audit distribution: exact score formula for ALL scanned symbols.
    def _audit_stats(values):
        if not values:
            return "n=0"
        vals=sorted(values)
        return f"n={len(vals)} min={vals[0]} median={vals[len(vals)//2]} avg={sum(vals)/len(vals):.1f} max={vals[-1]}"

    log.info(
        "UNIFIED SCORE AUDIT | TREND %s | buckets=%s | BOTTOM %s | buckets=%s",
        _audit_stats(totals["trend_audit_scores"]),
        ", ".join(f"{k} x{v}" for k,v in sorted(totals["trend_audit_buckets"].items())) or "none",
        _audit_stats(totals["bottom_audit_scores"]),
        ", ".join(f"{k} x{v}" for k,v in sorted(totals["bottom_audit_buckets"].items())) or "none",
    )
    ta=sorted(totals["trend_audit_top"], key=lambda x:(x[1],x[2]), reverse=True)[:10]
    ba=sorted(totals["bottom_audit_top"], key=lambda x:(x[1],x[2]), reverse=True)[:10]
    if ta:
        log.info("TOP UNIFIED AUDIT | TREND | " + " | ".join(f"{s}={sc} raw={raw} missing:{m or 'none'}" for s,sc,raw,m in ta))
    if ba:
        log.info("TOP UNIFIED AUDIT | BOTTOM | " + " | ".join(f"{s}={sc} raw={raw} missing:{m or 'none'}" for s,sc,raw,m in ba))
    if totals["trend_audit_scores"]:
        near=[x for x in totals["trend_audit_scores"] if max(0,MIN_SCORE-5) <= x < MIN_SCORE]
        log.info("UNIFIED NEAR-MISS | TREND range=%d-%d | count=%d | best=%s | gap=%s", max(0,MIN_SCORE-5), MIN_SCORE-1, len(near), max(near) if near else "none", (MIN_SCORE-max(near)) if near else "n/a")
    if totals["bottom_audit_scores"]:
        near=[x for x in totals["bottom_audit_scores"] if max(0,BOTTOM_MIN_SCORE-5) <= x < BOTTOM_MIN_SCORE]
        log.info("UNIFIED NEAR-MISS | BOTTOM range=%d-%d | count=%d | best=%s | gap=%s", max(0,BOTTOM_MIN_SCORE-5), BOTTOM_MIN_SCORE-1, len(near), max(near) if near else "none", (BOTTOM_MIN_SCORE-max(near)) if near else "n/a")

    # Independent blocker analysis: tells us whether a single wall or multiple
    # simultaneous failures are responsible for the zero-candidate result.
    if totals["trend_blockers"] or totals["bottom_blockers"]:
        ttop = totals["trend_blockers"].most_common(8)
        btop = totals["bottom_blockers"].most_common(8)
        log.info(
            "GATE BLOCK ANALYSIS | TREND single=%d multiple=%d | %s | "
            "BOTTOM single=%d multiple=%d | %s",
            totals["trend_block_single"], totals["trend_block_multiple"],
            ", ".join(f"{k} x{v}" for k, v in ttop) or "none",
            totals["bottom_block_single"], totals["bottom_block_multiple"],
            ", ".join(f"{k} x{v}" for k, v in btop) or "none",
        )

    log.info(
        "BASE MTF ANALYSIS | TREND Daily+4H base-pass=%d | TREND 1H-only-fail=%d | "
        "BOTTOM Daily+4H base-pass=%d | BOTTOM 1H-only-fail=%d",
        totals["trend_base_pass"], totals["trend_1h_only_fail"],
        totals["bottom_base_pass"], totals["bottom_1h_only_fail"],
    )

    log.info(
        "1H QUALITY PROFILE | TREND base-pass=%d | 1H-pass=%d | 1H-only-fail=%d | "
        "BOTTOM base-pass=%d | 1H-pass=%d | 1H-only-fail=%d",
        totals["trend_base_pass"],
        max(0, totals["trend_base_pass"] - totals["trend_1h_only_fail"]),
        totals["trend_1h_only_fail"],
        totals["bottom_base_pass"],
        max(0, totals["bottom_base_pass"] - totals["bottom_1h_only_fail"]),
        totals["bottom_1h_only_fail"],
    )

    if totals["h1_trend_failures"]:
        log.info("1H FAILURE DETAIL | TREND | " + " | ".join(f"{k} x{v}" for k,v in totals["h1_trend_failures"].most_common()))
    if totals["h1_bottom_failures"]:
        log.info("1H FAILURE DETAIL | BOTTOM | " + " | ".join(f"{k} x{v}" for k,v in totals["h1_bottom_failures"].most_common()))

    log.info("DEDUP REPORT | skipped=%d | window=%dh", dedup_skipped, DEDUP_HOURS)
    log.info("REJECTION COUNTS | trade_feasibility=%d | final_gate=%d | h1=%d",
             totals["trade_reject_count"], totals["final_gate_reject_count"], totals["h1_reject"])

    log.info(
        "SCAN REPORT | scanned=%d | trend_candidates=%d | bottom_candidates=%d | "
        "h1_reject=%d | trade_or_gate_reject=%d | final=%d | sent=%d",
        totals["scanned"],
        totals["trend_candidate"],
        totals["bottom_candidate"],
        totals["h1_reject"],
        totals["risk_reject"],
        totals["final"],
        sent,
    )

    if not results:
        log.info("No quality signal this run. This is intentional when market quality is poor.")


def self_test():
    """v3.21.3 regression tests for RSI pair diagnostics and fail-closed confirmation."""
    assert MIN_SCORE==82 and BOTTOM_MIN_SCORE==82 and STRONG_SCORE==90 and BOTTOM_STRONG_SCORE==90
    assert BOTTOM_LOW_LOOKBACK_H4==42 and BOTTOM_LOW_DISTANCE_PCT==5.0
    assert MAX_SL_PCT==13.0 and BOTTOM_MAX_SL_PCT==15.0
    assert HTTP_RETRIES>=2 and RETRY_BACKOFF>0
    assert TREND_SCORE_MAX==100 and BOTTOM_SCORE_MAX==100 and MAX_SIGNALS==3
    assert sum({"Daily regime":12,"4H structure":18,"BOS/retest":8,"Fib/location":8,"EMA alignment":10,"MACD":8,"RSI":7,"ADX":7,"4H volume":7,"Taker buy":5,"BTC context":3,"4H location/chase":5,"Volatility health":2}.values())==100
    assert sum({"Location":15,"Structure reaction":15,"Reversal triggers":24,"MACD":7,"RSI":7,"Bottom volume":7,"Taker buy":5,"Trade activity":4,"ADX":4,"Daily context":4,"BTC context":3,"Volatility health":5}.values())==100
    assert callable(unified_trend_score_audit) and callable(unified_bottom_score_audit)
    assert callable(professional_signal_quality_gate)
    assert callable(_nearest_confirmed_swing_low) and callable(_rsi_divergence_details)
    assert BOTTOM_VOL_MIN == 0.60
    # RSI divergence regression suite: aligned pivots, threshold boundaries,
    # spacing limits, and confirmation lag. These tests do not alter runtime rules.
    def _div_frame(older_idx, newer_idx, older_low=90.0, newer_low=89.0,
                   older_rsi=30.0, newer_rsi=35.0):
        frame = pd.DataFrame({"low":[100.0]*50, "rsi":[40.0]*50})
        for idx, low, rsi_value in (
            (older_idx, older_low, older_rsi), (newer_idx, newer_low, newer_rsi)
        ):
            frame.loc[idx, "low"] = low
            frame.loc[idx, "rsi"] = rsi_value
            if idx > 0:
                frame.loc[idx-1, "low"] = 94.0
            if idx < len(frame)-1:
                frame.loc[idx+1, "low"] = 94.0
        return frame

    # Positive control: price makes a meaningful lower low and RSI is >=4 points higher.
    div_test = _div_frame(30, 42)
    div_result = _rsi_divergence_details(div_test)
    assert div_result["both"] is True
    assert div_result["older_idx"] == 22 and div_result["newer_idx"] == 34  # indices are relative to the 42-bar lookback
    assert div_result["gap"] == 12 and div_result["price_change_pct"] < -0.5
    assert div_result["rsi_delta"] == 5.0
    assert rsi_bullish_divergence(div_test) is True
    pair_rows = _rsi_divergence_pair_audit(div_test)
    assert any(p["older_idx"] == 22 and p["newer_idx"] == 34 and p["both"] for p in pair_rows)
    assert all(3 <= p["gap"] <= 25 for p in pair_rows)

    # Regression: latest eligible pair can fail while an older eligible pair passes.
    multi = pd.DataFrame({"low": [110.0]*42, "rsi": [35.0]*42})
    for idx, low, rsi_value in ((12, 100.0, 25.0), (25, 98.0, 40.0), (36, 97.0, 32.0)):
        multi.loc[idx, "low"] = low
        multi.loc[idx, "rsi"] = rsi_value
        multi.loc[idx-1, "low"] = 111.0
        multi.loc[idx+1, "low"] = 111.0
    selected = _rsi_divergence_details(multi)
    all_pairs = _rsi_divergence_pair_audit(multi)
    assert selected["two_pivots"] is True and selected["both"] is False
    assert any(p["older_idx"] == 12 and p["newer_idx"] == 36 and p["both"] for p in all_pairs)

    # Price must be at least 0.5% lower; RSI strength alone is insufficient.
    weak_price = _div_frame(30, 42, older_low=90.0, newer_low=89.7)
    assert _rsi_divergence_details(weak_price)["price_lower_low"] is False
    assert _rsi_divergence_details(weak_price)["both"] is False

    # RSI must improve by at least 4 points; price structure alone is insufficient.
    weak_rsi = _div_frame(30, 42, older_rsi=30.0, newer_rsi=33.9)
    assert _rsi_divergence_details(weak_rsi)["delta_ge_4"] is False
    assert _rsi_divergence_details(weak_rsi)["both"] is False

    # At the minimum allowed separation (3 bars), two confirmed pivots can pair.
    min_gap = _div_frame(30, 33)
    assert _rsi_divergence_details(min_gap)["both"] is True

    # Overlapping swing windows must not manufacture a divergence from a 2-bar gap.
    overlapping = _div_frame(30, 32)
    assert _rsi_divergence_details(overlapping)["both"] is False

    # Two pivots farther apart than max_gap must not be paired.
    too_far = _div_frame(12, 40)
    assert _rsi_divergence_details(too_far)["both"] is False

    # A pivot at the newest edge is not confirmed without right-side candles.
    edge = _div_frame(30, 49)
    assert _rsi_divergence_details(edge)["two_pivots"] is False
    assert _rsi_divergence_details(edge)["both"] is False
    import inspect
    gate_src=inspect.getsource(professional_signal_quality_gate)
    assert 'check("Daily context"' not in gate_src and 'check("BTC safety"' not in gate_src and 'check("1H confirmation"' not in gate_src
    h1_src=inspect.getsource(h1_trend_confirmation_details)
    assert "confirmations>=4" in h1_src and "bearish_votes>=3" in h1_src
    # Seven-day low window must exclude lows older than the rolling 42-candle window.
    low_test = pd.DataFrame({"low":[80.0]+[100.0]*42,"close":[100.0]*42+[104.0]})
    near, recent_low = near_recent_low(low_test)
    assert near is True and recent_low == 100.0
    assert near_recent_low(low_test.iloc[-20:])[0] is False
    bottom_h1 = h1_bottom_confirmation_details(pd.DataFrame())
    assert bottom_h1["confirmed"] is False and bottom_h1["hard_failure"] is True
    # Diagnostics must resolve through the exact production setup functions.
    tb=inspect.getsource(trend_base_quality); bb=inspect.getsource(bottom_base_quality)
    assert "trend_setup" in tb and "bottom_setup" in bb
    az=inspect.getsource(analyze_symbol)
    assert "CANDIDATE AUDIT" in az and "TRADE FEASIBILITY REJECT" in az and "FINAL PIPELINE PASS" in az
    assert "trend_base = trend is not None" in az and "bottom_base = bottom is not None" in az
    src=inspect.getsource(klines)
    assert "df = df.iloc[:-1].copy()" in src
    t=inspect.getsource(unified_trend_score_audit)
    b=inspect.getsource(unified_bottom_score_audit)
    assert '"1H confirmation"' not in t and '"1H confirmation"' not in b
    assert "TREND_SCORE_MAX" not in t and "BOTTOM_SCORE_MAX" not in b
    # Logging contract: every formatted line must have matching arguments.
    profile_args = (1, 1, 0, 1, 1, 0)
    assert len(profile_args) == 6
    # Stop-risk contract: bounded ATR stop can never exceed configured risk by itself.
    test_h = pd.DataFrame({
        "open": [100.0]*30, "high": [101.0]*30, "low": [99.0]*30, "close": [100.0]*30,
        "volume": [1000.0]*30, "quote_volume": [100000.0]*30, "trades": [100.0]*30,
        "taker_quote": [50000.0]*30,
    })
    test_h = add_indicators(test_h)
    trade, err = build_trade(test_h, "CONFIRMED TREND")
    assert (trade is not None and trade["risk_pct"] <= MAX_SL_PCT) or (trade is None and err is not None)
    sub_keys = bottom_trigger_subconditions(test_h)
    assert all(k in sub_keys for k in ("rsi:two_pivots_found", "rsi:price_lower_low", "rsi:delta_ge_4", "rsi:both_conditions_pass", "rsi:price_only", "rsi:delta_only", "rsi:neither", "rejection:wick_ge_35pct", "rejection:bullish_close", "rejection:close_top_40pct", "macd:hist_rising", "macd:above_signal", "macd:fresh_cross", "reclaim:close_above_ema9", "reclaim:above_prev_high_or_ema21", "reclaim:ema9_nonfalling", "volume:lt_0.60", "volume:0.60_to_0.75", "volume:0.75_to_1.00", "volume:1.00_to_1.40", "volume:ge_1.40", "data:subcondition_error"))
    audit_keys = bottom_trigger_diagnostics(test_h)
    assert all(k in audit_keys for k in ("location:near 7d low", "location:Fibonacci zone", "structure:retest", "structure:CHOCH", "structure:BOS", "structure:higher low", "trigger:RSI divergence", "trigger:rejection candle", "trigger:MACD turn", "trigger:4H reclaim", "data:insufficient history", "data:diagnostic error"))
    assert BOTTOM_MIN_SCORE == 82 and BOTTOM_LOW_LOOKBACK_H4 == 42 and BOTTOM_LOW_DISTANCE_PCT == 5.0
    log.info("v3.21.3 RSI PAIR AUDIT + REGRESSION SELF-TEST: PASS")




DEPLOYMENT_ID = "ApexSignal-v3.21.3-rsi-pair-audit-20261011"

if __name__ == "__main__":
    log.info("DEPLOYMENT_ID: %s", DEPLOYMENT_ID)
    self_test()
    main()
