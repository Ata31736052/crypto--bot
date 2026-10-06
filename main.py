
# ============================================================
# ApexSignal v3.15.0 | UNIFIED SCORE AUDIT + ROOT FIX
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
MAX_WORKERS = 8
MAX_SIGNALS = 3  # maximum Telegram messages, NOT a coin/universe limit

# No artificial coin-count cap. liquid_universe() scans every currently
# tradable Binance Spot USDT symbol that passes the liquidity floor.
# The liquidity floor is a quality/data-safety filter, not a coin-count limit.
# No universe volume floor. Every valid Binance Spot USDT symbol is analyzed.
# Liquidity remains a quality factor inside the signal gates.
MIN_24H_USDT_VOLUME = 10_000_000
MIN_SCORE = 88
STRONG_SCORE = 93
TREND_SCORE_MAX = 123  # exact sum of reachable trend score components
BOTTOM_SCORE_MAX = 133  # exact sum of reachable bottom score components
BOTTOM_MIN_SCORE = 90
BOTTOM_STRONG_SCORE = 94

# Quality gates
DAILY_ADX_MIN = 16.0
DAILY_ADX_STRONG = 23.0
H4_ADX_MIN = 20.0
H4_ADX_RISING_MIN = 18.0
H4_VOL_MIN = 1.30
H4_VOL_STRONG = 1.80
BOTTOM_VOL_MIN = 1.40
ONE_H_VOL_MIN = 1.00

TAKER_BUY_MIN = 0.51
TAKER_BUY_STRONG = 0.56
TRADE_RATIO_MIN = 1.00

MAX_H4_EMA21_DISTANCE = 2.0
MAX_1H_EMA21_DISTANCE = 2.0

ATR_SL_MULT = 1.25
MAX_SL_PCT = 4.0
BOTTOM_MAX_SL_PCT = 4.0
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
session.headers.update({"User-Agent": "CryptoSignalBot/ApexSignal-v3.15.0"})


# ---------------- HTTP ----------------
def get_json(path, params=None):
    r = session.get(BINANCE_BASE + path, params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


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


def near_recent_low(h, pct_limit=5.0):
    low72 = h["low"].tail(18).min()  # 18 x 4h ~= 72h
    return h.iloc[-1].close <= low72 * (1 + pct_limit / 100), low72


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


def rsi_bullish_divergence(h):
    if len(h)<35: return False
    a=h.iloc[-25:-8]; b=h.iloc[-8:]
    pa=float(a.low.min()); pb=float(b.low.min())
    ra=float(a.loc[a.low.idxmin(),"rsi"]); rb=float(b.loc[b.low.idxmin(),"rsi"])
    return bool(pb<pa*0.995 and rb>=ra+4.0)


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


def h4_structure_ok(h):
    r = h.iloc[-1]
    ms = market_structure(h)
    return bool(ms["bullish"] and r.close > r.ema50 and r.ema21_slope > -0.05 and r.pdi > r.mdi and r.close <= r.ema21 * (1 + MAX_H4_EMA21_DISTANCE / 100))


def h1_trend_confirmation_details(h):
    """Return a conservative 1H confirmation profile.

    Daily+4H remain the setup engine. The 1H is allowed to be slightly noisy,
    but it may never override a genuine bearish contradiction. A mild MACD
    histogram pullback is acceptable when momentum is still positive or the
    MACD line remains above its signal.
    """
    r = h.iloc[-1]
    p = h.iloc[-2]
    price_ok = bool(r.close > r.ema21)
    structure_ok = bool((r.ema9 >= r.ema21) or (r.close > p.high))
    rsi_ok = bool(43 <= r.rsi <= 72 and r.rsi >= p.rsi - 2.0)
    macd_ok = bool(
        r.macd_hist > 0
        and r.macd > r.macd_signal
        and (r.macd_hist >= p.macd_hist or r.macd_hist > 0)
    )
    volume_ok = bool(r.vol_ratio >= ONE_H_VOL_MIN)
    location_ok = bool(r.close <= r.ema21 * (1 + MAX_1H_EMA21_DISTANCE / 100))
    bearish_contradiction = bool(
        r.close < r.ema21 * 0.995
        and r.pdi < r.mdi
        and r.macd_hist < p.macd_hist
        and r.rsi < p.rsi - 1.0
    )
    details = {
        "price_ok": price_ok,
        "structure_ok": structure_ok,
        "rsi_ok": rsi_ok,
        "macd_ok": macd_ok,
        "volume_ok": volume_ok,
        "location_ok": location_ok,
        "bearish_contradiction": bearish_contradiction,
    }
    details["confirmations"] = sum(details[k] for k in ("price_ok","structure_ok","rsi_ok","macd_ok","volume_ok"))
    details["confirmed"] = bool(details["structure_ok"] and details["macd_ok"] and details["confirmations"] >= 4 and not bearish_contradiction and location_ok)
    return details


def one_hour_trend_confirm(h):
    """Daily+4H create the setup; 1H only confirms it."""
    return h1_trend_confirmation_details(h)["confirmed"]


def h1_bottom_confirmation_details(h):
    """Return a conservative 1H reversal-confirmation profile."""
    r = h.iloc[-1]
    p = h.iloc[-2]
    details = {
        "price_ok": bool(r.close > r.ema9),
        "structure_ok": bool((r.close > p.high) or (r.close > r.ema21)),
        "rsi_ok": bool(41 <= r.rsi <= 70 and r.rsi >= p.rsi - 1.0),
        "macd_ok": bool(r.macd > r.macd_signal and (r.macd_hist > 0 or r.macd_hist > p.macd_hist)),
        "volume_ok": bool(r.vol_ratio >= ONE_H_VOL_MIN),
        "di_ok": bool(r.pdi >= r.mdi or r.pdi > p.pdi),
        "hard_failure": bool(r.close < r.ema21 and r.macd_hist < p.macd_hist and r.rsi < p.rsi - 1.0 and r.pdi < r.mdi),
    }
    details["confirmations"] = sum(details[k] for k in ("price_ok","structure_ok","rsi_ok","macd_ok","volume_ok","di_ok"))
    details["confirmed"] = bool(details["structure_ok"] and details["macd_ok"] and details["confirmations"] >= 4 and not details["hard_failure"])
    return details


def one_hour_bottom_confirm(h):
    """Reversal confirmation: 4 of 6 independent 1H clues, no hard failure."""
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
    r = h.iloc[-1]
    reasons = []
    score = 0
    score_components = {}

    if btc_d == "BEARISH" or btc_h == "BEARISH":
        return None, ["BTC bearish"]

    if not h4_structure_ok(h):
        ms = market_structure(h)
        return None, [f"4H structure not bullish enough: {ms.get('reason', 'unknown')}"]

    ms = market_structure(h)
    if not ms["bullish"]:
        return None, [f"4H market structure not bullish enough: {ms.get('reason', 'unknown')}"]

    if r.adx < 20.0:
        return None, ["4H ADX weak"]

    if r.vol_ratio < 1.30:
        return None, ["4H volume weak"]

    if r.pdi <= r.mdi:
        return None, ["4H DI bearish"]

    if r.macd_hist <= 0 or r.macd <= r.macd_signal:
        return None, ["4H MACD not bullish"]

    if r.taker_buy_ratio < TAKER_BUY_MIN:
        return None, ["taker buy weak"]

    # Daily regime is determined once upstream and reused consistently.
    dr = validate_daily_state(daily_state)
    # ROOT INVARIANT: Trend Daily eligibility is decided exactly once upstream.
    # NEUTRAL is valid for Trend; only BEARISH is ineligible. No later branch
    # may reinterpret NEUTRAL as a rejection.
    if dr == "BEARISH":
        return None, ["Daily bearish"]
    if not trend_daily_eligible(dr):
        return None, [f"Daily regime invalid: {dr}"]

    # Single scoring source: the exact same read-only calculator used by the
    # market-wide audit. Hard gates above remain unchanged.
    audit = unified_trend_score_audit(d, h, h1, btc_d, btc_h, dr)
    score = audit["raw"]
    score_components = dict(audit["components"])

    if score_components.get("4H BOS/retest") == 6:
        reasons.append("4H BOS")
    elif score_components.get("4H BOS/retest") == 4:
        reasons.append("4H retest")
    if score_components.get("Fib location") == 4:
        reasons.append("Fib 0.618-0.786 location")
    elif score_components.get("Fib location") == 2:
        reasons.append("Fib pullback")
    if score_components.get("ADX") == 10:
        reasons.append("ADX strong")
    elif score_components.get("ADX") == 7:
        reasons.append("ADX healthy")
    if score_components.get("4H volume") == 10:
        reasons.append("volume expansion")
    if score_components.get("EMA alignment") == 8:
        reasons.append("EMA alignment")
    if score_components.get("MACD") == 8:
        reasons.append("MACD positive")
    elif score_components.get("MACD") == 5:
        reasons.append("MACD improving")
    if score_components.get("1H confirmation") == 12:
        reasons.append("1H confirmed")

    normalized_score = audit["score"]
    if normalized_score < MIN_SCORE:
        detail = " | ".join(audit["missing"]) or "none"
        return None, reasons + [f"normalized score {normalized_score} < {MIN_SCORE} (raw={score}/{TREND_SCORE_MAX})", f"SCORE BREAKDOWN missing={detail}"]

    return {
        "strategy": "CONFIRMED TREND",
        "score": normalized_score,
        "raw_score": int(score),
        "score_max": TREND_SCORE_MAX,
        "score_components": score_components,
        "reasons": reasons,
    }, reasons


def bottom_setup(d, h, h1, btc_d, btc_h):
    r = h.iloc[-1]
    reasons = []
    score = 0

    if btc_d == "BEARISH" and btc_h == "BEARISH":
        return None, ["BTC Daily+4H bearish"]

    if d.iloc[-1].close < d.iloc[-1].ema200 and d.iloc[-1].mdi > d.iloc[-1].pdi:
        return None, ["Daily major trend bearish"]

    near_low, low72 = near_recent_low(h, 5.0)
    fib_ok, fibs = fib_zone(h)

    location_count = int(near_low) + int(fib_ok)
    if location_count == 0:
        return None, ["not at 72H low/Fib reversal zone"]

    if near_low:
        score += 20
        reasons.append("near 72H low")
    if fib_ok:
        score += 15
        reasons.append("Fib 0.618-0.786 zone")

    triggers = {
        "RSI divergence": rsi_bullish_divergence(h),
        "bullish rejection": rejection_candle(h),
        "MACD turn": macd_turn(h),
        "4H reclaim": reclaim_4h(h),
    }
    trigger_count = sum(triggers.values())
    ms = market_structure(h)
    if not (ms["choch"] or ms["bos"] or ms["retest"]):
        return None, reasons + ["no structural reversal/reclaim"]
    # Single scoring source: exact calculator shared with the unified audit.
    audit = unified_bottom_score_audit(d, h, h1, btc_d, btc_h)
    score = audit["raw"]
    normalized_score = audit["score"]
    reasons = []
    if near_low:
        reasons.append("near 72H low")
    if fib_ok:
        reasons.append("Fib 0.618-0.786 zone")
    if audit["components"].get("Structure reaction", 0):
        reasons.append("market structure reaction")
    reasons.extend([k for k, v in triggers.items() if v])
    if audit["components"].get("Bottom volume", 0):
        reasons.append("volume expansion")
    if audit["components"].get("Taker buy", 0):
        reasons.append("taker buy")
    if audit["components"].get("Taker buy strong", 0):
        reasons.append("strong taker buy")
    if audit["components"].get("Trade activity", 0):
        reasons.append("trade activity healthy")
    if audit["components"].get("ADX", 0) == 8:
        reasons.append("ADX healthy")
    elif audit["components"].get("ADX", 0) == 4:
        reasons.append("ADX recovering")
    if audit["components"].get("1H confirmation", 0):
        reasons.append("1H reversal confirmed")

    if normalized_score < BOTTOM_MIN_SCORE:
        detail = " | ".join(audit["missing"]) or "none"
        return None, reasons + [f"normalized score {normalized_score} < {BOTTOM_MIN_SCORE} (raw={score}/{BOTTOM_SCORE_MAX})", f"SCORE BREAKDOWN missing={detail}"]

    return {
        "strategy": "BOTTOM HUNTER",
        "score": normalized_score,
        "raw_score": int(score),
        "reasons": reasons,
        "low72": low72,
    }, reasons


# ---------------- RISK / TARGETS ----------------
def build_trade(h, strategy):
    r = h.iloc[-1]
    entry = float(r.close)
    atrv = float(r.atr)
    ms = market_structure(h)
    fib = fibonacci_levels(h)

    recent10 = h.tail(10)
    swing_candidates = [float(recent10.low.min())]
    if ms.get("swing_low") is not None:
        swing_candidates.append(float(ms["swing_low"]))
    structural_sl = min(swing_candidates) * 0.997
    atr_sl = entry - atrv * ATR_SL_MULT
    # Structure-first invalidation: never place the stop above the structural
    # swing just to make the percentage risk look smaller. ATR is a volatility
    # safety check and may widen the stop when structure is too tight.
    sl = min(structural_sl, atr_sl)
    if sl >= entry:
        return None, "invalid structural/ATR SL"

    risk = entry - sl
    risk_pct = risk / entry * 100 if entry else 999
    if risk <= 0:
        return None, "invalid SL"
    max_sl = BOTTOM_MAX_SL_PCT if strategy == "BOTTOM HUNTER" else MAX_SL_PCT
    if risk_pct > max_sl:
        return None, f"SL too wide {risk_pct:.2f}%"

    resistance = float(h.tail(30)["high"].iloc[:-1].max())
    required_tp1 = entry + risk * MIN_RR_TP1
    required_tp2 = entry + risk * MIN_RR_TP2
    candidates = [
        ("fixed_2.5pct", entry * (1 + TP1_PCT)),
        ("resistance", resistance * 0.995),
    ]
    if fib:
        candidates += [("fib_1.272", fib["1.272"]), ("fib_1.618", fib["1.618"]) ]

    valid_tp1 = [(name, float(x)) for name, x in candidates if x >= required_tp1 and x > entry]
    if not valid_tp1:
        detail = (
            f"no feasible TP1 with RR | entry={entry:.8g} | SL={sl:.8g} | "
            f"risk={risk_pct:.2f}% | required_TP1={required_tp1:.8g} | "
            f"resistance={resistance:.8g} | candidates=" +
            ",".join(f"{n}:{x:.8g}" for n,x in candidates)
        )
        return None, detail

    # TP1 is the nearest realistic target that actually clears the RR floor.
    tp1_name, tp1 = min(valid_tp1, key=lambda z: z[1])

    tp2_candidates = [("fixed_4pct", entry * (1 + TP2_PCT)), ("resistance", resistance * 0.995)]
    if fib:
        tp2_candidates += [("fib_1.272", fib["1.272"]), ("fib_1.618", fib["1.618"]) ]
    valid_tp2 = [(n, float(x)) for n,x in tp2_candidates if x >= required_tp2 and x > tp1]
    if not valid_tp2:
        detail = (
            f"no feasible TP2 with RR | entry={entry:.8g} | SL={sl:.8g} | "
            f"risk={risk_pct:.2f}% | required_TP2={required_tp2:.8g} | "
            f"candidates=" + ",".join(f"{n}:{x:.8g}" for n,x in tp2_candidates)
        )
        return None, detail

    # Prefer the nearest valid TP2, avoiding artificial distant targets.
    tp2_name, tp2 = min(valid_tp2, key=lambda z: z[1])
    rr1 = (tp1 - entry) / risk
    rr2 = (tp2 - entry) / risk

    return {
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
        "risk_pct": risk_pct, "rr1": rr1, "rr2": rr2,
        "tp1_name": tp1_name, "tp2_name": tp2_name,
        "required_tp1": required_tp1, "required_tp2": required_tp2,
        "resistance": resistance,
        "fib_1272": fib["1.272"] if fib else None,
        "fib_1618": fib["1.618"] if fib else None,
    }, None


# ---------------- FINAL PROFESSIONAL QUALITY GATE ----------------
def professional_signal_quality_gate(setup, trade, d, h, h1, btc_d, btc_h, daily_state):
    """Hard final gate. Returns (passed, reasons, detail) with every check visible."""
    reasons = []
    checks = []
    r = h.iloc[-1]
    p = h.iloc[-2]
    r1 = h1.iloc[-1]
    strategy = setup["strategy"]

    def check(name, passed, detail):
        checks.append(f"{name}={'PASS' if passed else 'FAIL'} ({detail})")
        if not passed:
            reasons.append(f"{name}: {detail}")

    rr1_ok = trade["rr1"] >= MIN_RR_TP1
    rr2_ok = trade["rr2"] >= MIN_RR_TP2
    risk_limit = BOTTOM_MAX_SL_PCT if strategy == "BOTTOM HUNTER" else MAX_SL_PCT
    risk_ok = trade["risk_pct"] <= risk_limit
    check("RR1", rr1_ok, f"{trade['rr1']:.2f} >= {MIN_RR_TP1:.2f}")
    check("RR2", rr2_ok, f"{trade['rr2']:.2f} >= {MIN_RR_TP2:.2f}")
    check("RISK", risk_ok, f"{trade['risk_pct']:.2f}% <= {risk_limit:.2f}%")

    vol1_ok = r1.vol_ratio >= ONE_H_VOL_MIN
    check("1H volume", vol1_ok, f"{r1.vol_ratio:.2f}x >= {ONE_H_VOL_MIN:.2f}x")

    if strategy == "CONFIRMED TREND":
        td = h1_trend_confirmation_details(h1)
        check("1H confirmation", td["confirmed"], f"{td['confirmations']}/5 confirmations; structure={'Y' if td['structure_ok'] else 'N'}; MACD={'Y' if td['macd_ok'] else 'N'}; bearish_contradiction={'Y' if td['bearish_contradiction'] else 'N'}")
    else:
        td = h1_bottom_confirmation_details(h1)
        check("1H confirmation", td["confirmed"], f"{td['confirmations']}/6 confirmations; structure={'Y' if td['structure_ok'] else 'N'}; MACD={'Y' if td['macd_ok'] else 'N'}; hard_failure={'Y' if td['hard_failure'] else 'N'}")

    btc_ok = not (btc_d == "BEARISH" and btc_h == "BEARISH")
    check("BTC context", btc_ok, f"Daily={btc_d}, 4H={btc_h}")

    if strategy == "CONFIRMED TREND":
        adx_ok = r.adx >= 20.0
        vol4_ok = r.vol_ratio >= 1.30
        di_ok = r.pdi > r.mdi
        location_ok = bool(r.close > r.ema21 and r.close <= r.ema21 * (1 + MAX_H4_EMA21_DISTANCE/100))
        macd4_ok = bool(r.macd_hist > 0 and r.macd > r.macd_signal)
        taker_ok = r.taker_buy_ratio >= TAKER_BUY_MIN
        daily_ok = trend_daily_eligible(daily_state)
        check("4H ADX", adx_ok, f"{r.adx:.1f} >= 20.0")
        check("4H volume", vol4_ok, f"{r.vol_ratio:.2f}x >= 1.30x")
        check("4H DI", di_ok, f"+DI={r.pdi:.1f} > -DI={r.mdi:.1f}")
        check("4H MACD", macd4_ok, f"hist={r.macd_hist:.6g}, line_vs_signal={'Y' if r.macd > r.macd_signal else 'N'}")
        check("4H location", location_ok, f"close/EMA21={(r.close/r.ema21-1)*100:.2f}%")
        check("Taker buy", taker_ok, f"{r.taker_buy_ratio:.2%} >= {TAKER_BUY_MIN:.2%}")
        check("Daily regime", daily_ok, daily_state)
    else:
        vol4_ok = r.vol_ratio >= 1.40
        adx_ok = bool(r.adx >= 20.0 or (r.adx >= 16.0 and r.adx > p.adx))
        taker_ok = r.taker_buy_ratio >= TAKER_BUY_MIN
        trade_ok = r.trade_ratio >= TRADE_RATIO_MIN
        check("Bottom 4H volume", vol4_ok, f"{r.vol_ratio:.2f}x >= 1.40x")
        check("Bottom ADX", adx_ok, f"ADX={r.adx:.1f}, prev={p.adx:.1f}")
        check("Taker buy", taker_ok, f"{r.taker_buy_ratio:.2%} >= {TAKER_BUY_MIN:.2%}")
        check("Trade activity", trade_ok, f"{r.trade_ratio:.2f}x >= {TRADE_RATIO_MIN:.2f}x")

    minimum = BOTTOM_MIN_SCORE if strategy == "BOTTOM HUNTER" else MIN_SCORE
    score_ok = setup["score"] >= minimum
    check("Score", score_ok, f"{setup['score']}/100 >= {minimum}")

    if strategy == "BOTTOM HUNTER":
        trigger_words = ("RSI divergence", "bullish rejection", "MACD turn", "4H reclaim")
        active = [x for x in trigger_words if x in setup.get("reasons", [])]
        trigger_count = len(active)
        structural = ("4H reclaim" in active) or ("bullish rejection" in active)
        triggers_ok = trigger_count >= 3 and structural
        check("Reversal triggers", triggers_ok, f"{trigger_count}/4; structural_trigger={'Y' if structural else 'N'}; active={','.join(active) or 'none'}")

    detail = {
        "checks": checks,
        "reasons": reasons,
        "strategy": strategy,
        "score": setup["score"],
        "raw_score": setup.get("raw_score", "n/a"),
        "risk_pct": trade["risk_pct"],
        "rr1": trade["rr1"],
        "rr2": trade["rr2"],
    }
    return (len(reasons) == 0), reasons, detail


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

def diagnostic_trend_blocks(d, h, h1, btc_d, btc_h, daily_state):
    """Diagnostic-only: evaluate ALL trend blockers without changing the real gate."""
    r = h.iloc[-1]
    blocks = []
    if btc_d == "BEARISH" or btc_h == "BEARISH":
        blocks.append("BTC bearish")
    if not h4_structure_ok(h):
        blocks.append("4H structure")
    if r.adx < 20.0 and not (r.adx >= H4_ADX_RISING_MIN and r.adx > h.iloc[-2].adx):
        blocks.append("4H ADX")
    if r.vol_ratio < H4_VOL_MIN:
        blocks.append("4H volume")
    if r.pdi <= r.mdi:
        blocks.append("4H DI")
    if r.macd_hist <= 0 or r.macd <= r.macd_signal:
        blocks.append("4H MACD")
    if r.taker_buy_ratio < TAKER_BUY_MIN:
        blocks.append("taker buy")
    if r.close <= r.ema21 or r.close > r.ema21 * (1 + MAX_H4_EMA21_DISTANCE/100):
        blocks.append("4H entry location")
    if daily_state == "BEARISH":
        blocks.append("Daily bearish")
    td = h1_trend_confirmation_details(h1)
    if not td["confirmed"]:
        if td["bearish_contradiction"]: blocks.append("1H bearish contradiction")
        if not td["price_ok"]: blocks.append("1H price/EMA21")
        if not td["structure_ok"]: blocks.append("1H structure")
        if not td["rsi_ok"]: blocks.append("1H RSI")
        if not td["macd_ok"]: blocks.append("1H MACD")
        if not td["volume_ok"]: blocks.append("1H volume")
        if not td["location_ok"]: blocks.append("1H location")
        blocks.append("1H confirmation")
    return blocks


def diagnostic_bottom_blocks(d, h, h1, btc_d, btc_h):
    """Diagnostic-only: evaluate ALL bottom-hunter blockers without changing the real gate."""
    r = h.iloc[-1]
    blocks = []
    if btc_d == "BEARISH" and btc_h == "BEARISH":
        blocks.append("BTC Daily+4H bearish")
    if d.iloc[-1].close < d.iloc[-1].ema200 and d.iloc[-1].mdi > d.iloc[-1].pdi:
        blocks.append("Daily major trend")
    near_low, _ = near_recent_low(h, 5.0)
    fib_ok, _ = fib_zone(h)
    if not (near_low or fib_ok):
        blocks.append("location")
    triggers = {
        "RSI divergence": rsi_bullish_divergence(h),
        "bullish rejection": rejection_candle(h),
        "MACD turn": macd_turn(h),
        "4H reclaim": reclaim_4h(h),
    }
    if sum(triggers.values()) < 3:
        blocks.append("reversal triggers")
    if r.vol_ratio < BOTTOM_VOL_MIN:
        blocks.append("bottom volume")
    if r.taker_buy_ratio < TAKER_BUY_MIN:
        blocks.append("taker buy")
    if r.trade_ratio < TRADE_RATIO_MIN:
        blocks.append("trade activity")
    if not (r.adx >= 20 or (r.adx >= 16 and r.adx > h.iloc[-2].adx)):
        blocks.append("ADX")
    bd = h1_bottom_confirmation_details(h1)
    if not bd["confirmed"]:
        if bd["hard_failure"]: blocks.append("1H hard reversal failure")
        if not bd["price_ok"]: blocks.append("1H price/EMA9")
        if not bd["structure_ok"]: blocks.append("1H structure")
        if not bd["rsi_ok"]: blocks.append("1H RSI")
        if not bd["macd_ok"]: blocks.append("1H MACD")
        if not bd["volume_ok"]: blocks.append("1H volume")
        if not bd["di_ok"]: blocks.append("1H DI")
        blocks.append("1H confirmation")
    return blocks


def trend_base_quality(d, h, btc_d, btc_h, daily_state):
    """Daily+4H trend quality only; deliberately excludes every 1H rule."""
    r = h.iloc[-1]
    if btc_d == "BEARISH" or btc_h == "BEARISH":
        return False
    if not h4_structure_ok(h):
        return False
    if r.adx < 20.0:
        return False
    if r.vol_ratio < H4_VOL_MIN:
        return False
    if r.pdi <= r.mdi:
        return False
    if r.macd_hist <= 0 or r.macd <= r.macd_signal:
        return False
    if r.close <= r.ema21 or r.close > r.ema21 * (1 + MAX_H4_EMA21_DISTANCE/100):
        return False
    if r.taker_buy_ratio < TAKER_BUY_MIN:
        return False
    if not trend_daily_eligible(daily_state):
        return False
    return True


def bottom_base_quality(d, h, btc_d, btc_h):
    """Daily+4H Bottom Hunter quality only; excludes 1H confirmation."""
    r = h.iloc[-1]
    if btc_d == "BEARISH" and btc_h == "BEARISH":
        return False
    if d.iloc[-1].close < d.iloc[-1].ema200 and d.iloc[-1].mdi > d.iloc[-1].pdi:
        return False
    near_low, _ = near_recent_low(h, 5.0)
    fib_ok, _ = fib_zone(h)
    if not (near_low or fib_ok):
        return False
    ms = market_structure(h)
    if not (ms["choch"] or ms["bos"] or ms["retest"]):
        return False
    triggers = (
        rsi_bullish_divergence(h),
        rejection_candle(h),
        macd_turn(h),
        reclaim_4h(h),
    )
    if sum(triggers) < 3:
        return False
    if r.vol_ratio < BOTTOM_VOL_MIN:
        return False
    if r.taker_buy_ratio < TAKER_BUY_MIN:
        return False
    if r.trade_ratio < TRADE_RATIO_MIN:
        return False
    if not (r.adx >= 20.0 or (r.adx >= 16.0 and r.adx > h.iloc[-2].adx)):
        return False
    return True



def _bucket_score(score, kind="trend"):
    score = int(max(0, min(100, round(score))))
    if kind == "trend":
        return "93-100" if score >= 93 else "88-92" if score >= 88 else "84-87" if score >= 84 else "80-83" if score >= 80 else "70-79" if score >= 70 else "<70"
    return "94-100" if score >= 94 else "90-93" if score >= 90 else "86-89" if score >= 86 else "80-85" if score >= 80 else "70-79" if score >= 70 else "<70"


def diagnostic_potential_score(d, h, h1, btc_d, btc_h, daily_state, strategy):
    """Diagnostic-only score estimate from ALL observable components.

    It intentionally does NOT reproduce setup eligibility and does NOT feed any
    trading decision. It answers: 'How high could this symbol score if all hard
    gates were allowed to reach the scoring stage?' This lets us distinguish
    an early hard-gate bottleneck from genuine low score quality.
    """
    r = h.iloc[-1]
    p = h.iloc[-2]
    score = 0
    if strategy == "TREND":
        # Same score weights as trend_setup, but score every component independently.
        if daily_state in {"BULLISH", "EARLY_BULLISH", "NEUTRAL"}:
            score += 20
        ms = market_structure(h)
        if ms.get("bullish"): score += 20
        if ms.get("bos"): score += 6
        elif ms.get("retest"): score += 4
        fib = fibonacci_levels(h)
        if fib:
            price=float(r.close)
            if fib["0.618"] <= price <= fib["0.786"]: score += 4
            elif fib["0.382"] <= price <= fib["0.500"]: score += 2
        if r.adx >= 25: score += 10
        elif r.adx >= 20: score += 7
        if r.vol_ratio >= H4_VOL_STRONG: score += 10
        elif r.vol_ratio >= H4_VOL_MIN: score += 6
        if r.close > r.ema9 > r.ema21 > r.ema50: score += 8
        if r.close > r.ema200: score += 5
        if r.macd_hist > 0: score += 8
        elif r.macd_hist > p.macd_hist: score += 5
        if 52 <= r.rsi <= 68: score += 7
        elif 48 <= r.rsi < 52: score += 4
        if r.taker_buy_ratio >= TAKER_BUY_STRONG: score += 5
        if btc_d == "BULLISH": score += 4
        if btc_h == "BULLISH": score += 4
        td=h1_trend_confirmation_details(h1)
        if td["confirmed"]: score += 12
        return min(100, int(round(score / TREND_SCORE_MAX * 100)))
    else:
        if not (btc_d == "BEARISH" and btc_h == "BEARISH"): score += 0
        if not (d.iloc[-1].close < d.iloc[-1].ema200 and d.iloc[-1].mdi > d.iloc[-1].pdi): score += 0
        near_low,_=near_recent_low(h,5.0); fib_ok,_=fib_zone(h)
        if near_low: score += 20
        if fib_ok: score += 15
        ms=market_structure(h)
        if ms.get("choch") or ms.get("bos") or ms.get("retest"): score += 5
        triggers=(rsi_bullish_divergence(h),rejection_candle(h),macd_turn(h),reclaim_4h(h))
        score += sum(triggers)*8
        if r.vol_ratio >= BOTTOM_VOL_MIN: score += 12
        if r.taker_buy_ratio >= TAKER_BUY_MIN: score += 6
        if r.taker_buy_ratio >= TAKER_BUY_STRONG: score += 3
        if r.trade_ratio >= TRADE_RATIO_MIN: score += 5
        if r.adx >= 20: score += 8
        elif r.adx >= 16 and r.adx > p.adx: score += 4
        if 38 <= r.rsi <= 62: score += 5
        if btc_d == "BULLISH": score += 5
        if btc_h == "BULLISH": score += 5
        if h1_bottom_confirmation_details(h1)["confirmed"]: score += 12
        return min(100, int(round(score / BOTTOM_SCORE_MAX * 100)))


def potential_missing_components(d, h, h1, btc_d, btc_h, daily_state, strategy):
    """Return human-readable hard/score blockers for the potential-score diagnostic."""
    r=h.iloc[-1]; p=h.iloc[-2]; missing=[]
    if strategy == "TREND":
        ms=market_structure(h)
        if not ms.get("bullish"): missing.append("4H structure")
        if not (ms.get("bos") or ms.get("retest")): missing.append("BOS/retest")
        fib=fibonacci_levels(h)
        if not fib or not (fib["0.618"] <= float(r.close) <= fib["0.786"]): missing.append("Fib 0.618-0.786")
        if r.adx < 20: missing.append("ADX")
        if r.vol_ratio < H4_VOL_MIN: missing.append("4H volume")
        if not (r.close > r.ema9 > r.ema21 > r.ema50): missing.append("EMA alignment")
        if r.close <= r.ema200: missing.append("EMA200")
        if not (r.macd_hist > 0): missing.append("4H MACD")
        if not (48 <= r.rsi <= 68): missing.append("RSI")
        if r.taker_buy_ratio < TAKER_BUY_MIN: missing.append("taker buy")
        td=h1_trend_confirmation_details(h1)
        if not td["confirmed"]: missing.append("1H confirmation")
    else:
        near_low,_=near_recent_low(h,5.0); fib_ok,_=fib_zone(h)
        if not (near_low or fib_ok): missing.append("location")
        ms=market_structure(h)
        if not (ms.get("choch") or ms.get("bos") or ms.get("retest")): missing.append("structure reaction")
        triggers=(rsi_bullish_divergence(h),rejection_candle(h),macd_turn(h),reclaim_4h(h))
        if sum(triggers)<3: missing.append("3/4 reversal triggers")
        if r.vol_ratio < BOTTOM_VOL_MIN: missing.append("bottom volume")
        if r.taker_buy_ratio < TAKER_BUY_MIN: missing.append("taker buy")
        if r.trade_ratio < TRADE_RATIO_MIN: missing.append("trade activity")
        if not (r.adx>=20 or (r.adx>=16 and r.adx>p.adx)): missing.append("ADX")
        if not h1_bottom_confirmation_details(h1)["confirmed"]: missing.append("1H confirmation")
    return missing

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
    """Exact read-only mirror of trend_setup scoring.

    This function NEVER decides eligibility. It only calculates the same score
    components used by trend_setup, for every symbol, including symbols that
    fail an earlier hard gate. The component weights are intentionally kept in
    one place and validated against TREND_SCORE_MAX.
    """
    r = h.iloc[-1]
    p = h.iloc[-2]
    c = {}
    reasons = []
    ms = market_structure(h)

    if daily_state in {"BULLISH", "EARLY_BULLISH", "NEUTRAL"}:
        c["Daily regime"] = 20
        reasons.append("Daily eligible")
    else:
        c["Daily regime"] = 0

    c["4H structure"] = 20 if ms.get("bullish") else 0
    c["4H BOS/retest"] = 6 if ms.get("bos") else (4 if ms.get("retest") else 0)

    fib = fibonacci_levels(h)
    if fib:
        price = float(r.close)
        if fib["0.618"] <= price <= fib["0.786"]:
            c["Fib location"] = 4
        elif fib["0.382"] <= price <= fib["0.500"]:
            c["Fib location"] = 2
        else:
            c["Fib location"] = 0
    else:
        c["Fib location"] = 0

    c["ADX"] = 10 if r.adx >= 25 else (7 if r.adx >= 20 else 0)
    c["4H volume"] = 10 if r.vol_ratio >= H4_VOL_STRONG else (6 if r.vol_ratio >= H4_VOL_MIN else 0)
    c["EMA alignment"] = 8 if r.close > r.ema9 > r.ema21 > r.ema50 else 0
    c["Above EMA200"] = 5 if r.close > r.ema200 else 0
    c["MACD"] = 8 if r.macd_hist > 0 else (5 if r.macd_hist > p.macd_hist else 0)
    c["RSI"] = 7 if 52 <= r.rsi <= 68 else (4 if 48 <= r.rsi < 52 else 0)
    c["Taker buy strong"] = 5 if r.taker_buy_ratio >= TAKER_BUY_STRONG else 0
    c["BTC Daily"] = 4 if btc_d == "BULLISH" else 0
    c["BTC 4H"] = 4 if btc_h == "BULLISH" else 0
    c["1H confirmation"] = 12 if h1_trend_confirmation_details(h1)["confirmed"] else 0

    raw = int(sum(c.values()))
    max_possible = int(TREND_SCORE_MAX)
    if raw > max_possible:
        raise AssertionError(f"Trend audit raw score {raw} exceeds max {max_possible}")
    normalized = min(100, int(round(raw / max_possible * 100)))
    maximums = {"Daily regime":20,"4H structure":20,"4H BOS/retest":6,"Fib location":4,"ADX":10,
                 "4H volume":10,"EMA alignment":8,"Above EMA200":5,"MACD":8,"RSI":7,
                 "Taker buy strong":5,"BTC Daily":4,"BTC 4H":4,"1H confirmation":12}
    missing = [f"{k} {c[k]}/{v}" for k,v in maximums.items() if c[k] < v]
    return {"raw":raw,"max":max_possible,"score":normalized,"components":c,"missing":missing,"reasons":reasons}


def unified_bottom_score_audit(d, h, h1, btc_d, btc_h):
    """Exact read-only mirror of bottom_setup scoring, without eligibility gates."""
    r = h.iloc[-1]
    p = h.iloc[-2]
    c = {}
    near_low, _ = near_recent_low(h, 5.0)
    fib_ok, _ = fib_zone(h)
    ms = market_structure(h)
    triggers = (rsi_bullish_divergence(h), rejection_candle(h), macd_turn(h), reclaim_4h(h))

    c["72H low"] = 20 if near_low else 0
    c["Fib 0.618-0.786"] = 15 if fib_ok else 0
    c["Structure reaction"] = 5 if (ms.get("choch") or ms.get("bos") or ms.get("retest")) else 0
    c["Reversal triggers"] = sum(triggers) * 8
    c["Bottom volume"] = 12 if r.vol_ratio >= BOTTOM_VOL_MIN else 0
    c["Taker buy"] = 6 if r.taker_buy_ratio >= TAKER_BUY_MIN else 0
    c["Taker buy strong"] = 3 if r.taker_buy_ratio >= TAKER_BUY_STRONG else 0
    c["Trade activity"] = 5 if r.trade_ratio >= TRADE_RATIO_MIN else 0
    c["ADX"] = 8 if r.adx >= 20 else (4 if r.adx >= 16 and r.adx > p.adx else 0)
    c["RSI"] = 5 if 38 <= r.rsi <= 62 else 0
    c["BTC Daily"] = 5 if btc_d == "BULLISH" else 0
    c["BTC 4H"] = 5 if btc_h == "BULLISH" else 0
    c["1H confirmation"] = 12 if h1_bottom_confirmation_details(h1)["confirmed"] else 0

    raw = int(sum(c.values()))
    max_possible = int(BOTTOM_SCORE_MAX)
    if raw > max_possible:
        raise AssertionError(f"Bottom audit raw score {raw} exceeds max {max_possible}")
    normalized = min(100, int(round(raw / max_possible * 100)))
    maximums = {"72H low":20,"Fib 0.618-0.786":15,"Structure reaction":5,"Reversal triggers":32,
                "Bottom volume":12,"Taker buy":6,"Taker buy strong":3,"Trade activity":5,"ADX":8,
                "RSI":5,"BTC Daily":5,"BTC 4H":5,"1H confirmation":12}
    missing = [f"{k} {c[k]}/{v}" for k,v in maximums.items() if c[k] < v]
    return {"raw":raw,"max":max_possible,"score":normalized,"components":c,"missing":missing}


def analyze_symbol(symbol, btc_d, btc_h):
    stats={"scanned":1,"trend_candidate":0,"bottom_candidate":0,"h1_reject":0,"risk_reject":0,"final":0,
           "diagnostic_reasons":Counter(),"trend_blockers":Counter(),"bottom_blockers":Counter(),"trend_block_single":0,"trend_block_multiple":0,
           "bottom_block_single":0,"bottom_block_multiple":0,"trend_base_pass":0,"trend_1h_only_fail":0,"bottom_base_pass":0,"bottom_1h_only_fail":0,
           "h1_trend_failures":Counter(),"h1_bottom_failures":Counter(),"trend_score_rejects":Counter(),"bottom_score_rejects":Counter(),"trend_score_buckets":Counter(),"bottom_score_buckets":Counter(),"trend_score_values":[],"bottom_score_values":[],"trend_potential_scores":[],"bottom_potential_scores":[],"trend_potential_buckets":Counter(),"bottom_potential_buckets":Counter(),"trend_potential_top":[],"bottom_potential_top":[],"trend_audit_scores":[],"bottom_audit_scores":[],"trend_audit_buckets":Counter(),"bottom_audit_buckets":Counter(),"trend_audit_top":[],"bottom_audit_top":[]}
    try:
        d=add_indicators(klines(symbol,"1d",250)); h=add_indicators(klines(symbol,"4h",250)); h1=add_indicators(klines(symbol,"1h",250))
        daily_state = validate_daily_state(daily_regime(d))
        # ROOT DAILY LOCK: the same validated state is passed through every Trend stage.
        if daily_state not in {"BULLISH", "EARLY_BULLISH", "NEUTRAL", "BEARISH"}:
            raise RuntimeError(f"Daily state invariant failure: {daily_state!r}")
        if min(len(d),len(h),len(h1))<210: return None,stats,"insufficient data"
        tblocks=diagnostic_trend_blocks(d,h,h1,btc_d,btc_h,daily_state); bblocks=diagnostic_bottom_blocks(d,h,h1,btc_d,btc_h)
        stats["trend_blockers"].update(tblocks); stats["bottom_blockers"].update(bblocks)
        if len(tblocks) == 1: stats["trend_block_single"] += 1
        elif len(tblocks) > 1: stats["trend_block_multiple"] += 1
        if len(bblocks) == 1: stats["bottom_block_single"] += 1
        elif len(bblocks) > 1: stats["bottom_block_multiple"] += 1
        trend_base = trend_base_quality(d,h,btc_d,btc_h,daily_state)
        bottom_base = bottom_base_quality(d,h,btc_d,btc_h)
        if trend_base: stats["trend_base_pass"] += 1
        if bottom_base: stats["bottom_base_pass"] += 1
        trend,treasons=trend_setup(d,h,h1,btc_d,btc_h,daily_state)
        bottom,breasons=bottom_setup(d,h,h1,btc_d,btc_h)
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
        if bottom_audit["score"] >= 80:
            stats["bottom_audit_top"].append((symbol, bottom_audit["score"], bottom_audit["raw"], "; ".join(bottom_audit["missing"][:6])))
        stats["trend_potential_scores"].append(trend_potential)
        stats["bottom_potential_scores"].append(bottom_potential)
        stats["trend_potential_buckets"][_bucket_score(trend_potential,"trend")] += 1
        stats["bottom_potential_buckets"][_bucket_score(bottom_potential,"bottom")] += 1
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
        if trend_base and not one_hour_trend_confirm(h1):
            stats["trend_1h_only_fail"] += 1
            td = h1_trend_confirmation_details(h1)
            for key in ("price_ok","structure_ok","rsi_ok","macd_ok","volume_ok","location_ok","bearish_contradiction"):
                if not td[key]: stats["h1_trend_failures"][key] += 1
        if bottom_base and not one_hour_bottom_confirm(h1):
            stats["bottom_1h_only_fail"] += 1
            bd = h1_bottom_confirmation_details(h1)
            for key in ("price_ok","structure_ok","rsi_ok","macd_ok","volume_ok","di_ok","hard_failure"):
                if not bd[key]: stats["h1_bottom_failures"][key] += 1
        setups=[]
        if trend: stats["trend_candidate"]+=1; setups.append(trend)
        elif trend_base and one_hour_trend_confirm(h1):
            stats["diagnostic_reasons"]["TREND: base+1H passed but setup rejected"] += 1
            # IMPORTANT: treasons contains BOTH positive score components and the
            # terminal rejection reason. treasons[0] is usually "Daily=...",
            # which is a reason/component, not a rejection. Never label it as
            # setup_reject. The actual terminal reason is the final item when
            # setup returns None.
            reject_reason = treasons[-1] if treasons else "unknown"
            is_daily_rejection = reject_reason in {"Daily bearish"} or reject_reason.startswith("Daily regime invalid:")
            if is_daily_rejection:
                log.error(
                    "PIPELINE INVARIANT VIOLATION | %s | Daily=%s | TREND base=PASS 1H=PASS -> setup_reject=%s | reasons=%s",
                    symbol, daily_state, reject_reason, " || ".join(treasons[:8]) or "none"
                )
            else:
                log.info(
                    "PIPELINE TRACE | %s | Daily=%s | TREND base=PASS 1H=PASS -> setup_reject=%s | reasons=%s",
                    symbol, daily_state, reject_reason, " || ".join(treasons[:10]) or "none"
                )
        else:
            for reason in treasons[:4]: stats["diagnostic_reasons"][f"TREND: {reason}"]+=1
        if bottom: stats["bottom_candidate"]+=1; setups.append(bottom)
        elif bottom_base and one_hour_bottom_confirm(h1):
            stats["diagnostic_reasons"]["BOTTOM: base+1H passed but setup rejected"] += 1
            log.info("PIPELINE TRACE | %s | BOTTOM base=PASS 1H=PASS -> setup_reject=%s", symbol, breasons[0] if breasons else "unknown")
        else:
            for reason in breasons[:4]: stats["diagnostic_reasons"][f"BOTTOM: {reason}"]+=1
        qualified=[]
        for setup in setups:
            trade,err=build_trade(h,setup["strategy"])
            if not trade:
                stats["risk_reject"]+=1
                stats["diagnostic_reasons"][f"{setup['strategy']}: {err}"]+=1
                continue
            passed,gate_reasons,gate_detail=professional_signal_quality_gate(setup,trade,d,h,h1,btc_d,btc_h,daily_state)
            if passed:
                qualified.append({"symbol":symbol,"setup":setup,"trade":trade,"d":d,"h":h,"h1":h1,"btc_d":btc_d,"btc_h":btc_h})
            else:
                stats["risk_reject"]+=1
                for reason in gate_reasons[:8]: stats["diagnostic_reasons"][f"{setup['strategy']}: {reason}"]+=1
                log.info(
                    "FINAL GATE DETAIL | %s | %s | score=%s raw=%s | risk=%.2f%% | RR1=%.2f RR2=%.2f | %s",
                    symbol, setup["strategy"], gate_detail["score"], gate_detail["raw_score"],
                    gate_detail["risk_pct"], gate_detail["rr1"], gate_detail["rr2"],
                    " | ".join(gate_detail["checks"])
                )
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
        if k in ("diagnostic_reasons", "trend_blockers", "bottom_blockers", "h1_trend_failures", "h1_bottom_failures", "trend_score_rejects", "bottom_score_rejects", "trend_score_buckets", "bottom_score_buckets", "trend_potential_buckets", "bottom_potential_buckets", "trend_audit_buckets", "bottom_audit_buckets"):
            dst[k].update(src.get(k, {}))
        elif k in ("trend_score_values", "bottom_score_values", "trend_potential_scores", "bottom_potential_scores", "trend_potential_top", "bottom_potential_top", "trend_audit_scores", "bottom_audit_scores", "trend_audit_top", "bottom_audit_top"):
            dst[k].extend(src.get(k, []))
        else:
            dst[k] += src.get(k, 0)


def main():
    log.info("Starting ApexSignal v3.15.0 | UNIFIED SCORE AUDIT + ROOT FIX | SCORE CALIBRATION | STRUCTURE-FIRST | FAIL-CLOSED")
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
    if totals["diagnostic_reasons"]:
        top = totals["diagnostic_reasons"].most_common(12)
        log.info("DEEP DIAGNOSTIC SUMMARY | " + " | ".join(f"{reason} x{count}" for reason, count in top))

    if totals["trend_score_rejects"]:
        log.info("TREND SCORE REJECT DETAIL | " + " | ".join(f"{k} x{v}" for k,v in totals["trend_score_rejects"].most_common()))
    if totals["bottom_score_rejects"]:
        log.info("BOTTOM SCORE REJECT DETAIL | " + " | ".join(f"{k} x{v}" for k,v in totals["bottom_score_rejects"].most_common()))

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
        log.info("UNIFIED NEAR-MISS | TREND 84-%d=%d | threshold=%d", MIN_SCORE-1, sum(1 for x in totals["trend_audit_scores"] if 84 <= x < MIN_SCORE), MIN_SCORE)
    if totals["bottom_audit_scores"]:
        log.info("UNIFIED NEAR-MISS | BOTTOM 86-%d=%d | threshold=%d", BOTTOM_MIN_SCORE-1, sum(1 for x in totals["bottom_audit_scores"] if 86 <= x < BOTTOM_MIN_SCORE), BOTTOM_MIN_SCORE)

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

    log.info(
        "SCAN REPORT | scanned=%d | trend_candidates=%d | bottom_candidates=%d | "
        "h1_reject=%d | final_gate_reject=%d | final=%d | sent=%d",
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


DEPLOYMENT_ID = "ApexSignal-v3.15.0-unified-score-audit-root-fix-20261006"

if __name__ == "__main__":
    log.info("DEPLOYMENT_ID: %s", DEPLOYMENT_ID)
    main()
