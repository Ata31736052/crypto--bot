
# ============================================================
# ApexSignal v3.8 | PROFESSIONAL 4H SETUP ENGINE + MARKET STRUCTURE + FIB
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
MIN_24H_USDT_VOLUME = 10_000_000  # institutional-style liquidity floor
MIN_SCORE = 90
STRONG_SCORE = 95
BOTTOM_MIN_SCORE = 92
BOTTOM_STRONG_SCORE = 96

# Quality gates
DAILY_ADX_MIN = 16.0
DAILY_ADX_STRONG = 23.0
H4_ADX_MIN = 20.0
H4_ADX_RISING_MIN = 18.0
H4_VOL_MIN = 1.50
H4_VOL_STRONG = 1.80
BOTTOM_VOL_MIN = 1.60
ONE_H_VOL_MIN = 1.00

TAKER_BUY_MIN = 0.53
TAKER_BUY_STRONG = 0.57
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
session.headers.update({"User-Agent": "CryptoSignalBot/ApexSignal-v3.8"})


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
    """Return tradable Binance Spot USDT pairs above the 24h liquidity floor.

    This is a pre-scan institutional-style liquidity gate. It is not a
    signal-quality substitute: 1H/4H volume and market-quality gates still
    have to pass later. If Binance ticker data is temporarily unavailable,
    fall back to the exchange-info universe rather than crashing the scan.
    """
    syms = set(exchange_symbols())
    if MIN_24H_USDT_VOLUME <= 0:
        return sorted(syms)
    try:
        tickers = get_json("/api/v3/ticker/24hr")
        liquid = set()
        for t in tickers if isinstance(tickers, list) else []:
            sym = t.get("symbol", "")
            if sym in syms:
                try:
                    qv = float(t.get("quoteVolume", 0.0))
                except (TypeError, ValueError):
                    qv = 0.0
                if qv >= MIN_24H_USDT_VOLUME:
                    liquid.add(sym)
        if liquid:
            return sorted(liquid)
    except Exception as exc:
        log.warning("24H liquidity filter unavailable; using full Spot universe | %s", exc)
    return sorted(syms)


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


def market_structure(h, lookback=48, pivot=2):
    """Conservative closed-candle market structure.

    A bullish BOS/CHOCH must break a meaningful pivot, not merely the highest
    value of a rolling window. A retest requires: prior break -> pullback into
    the broken level -> close back above it. This prevents ordinary candles
    from being mislabeled as a structural retest.
    """
    x = h.tail(lookback).reset_index(drop=True)
    if len(x) < max(20, pivot * 2 + 8):
        return {"bullish": False, "bos": False, "retest": False,
                "choch": False, "swing_high": None, "swing_low": None,
                "broken_level": None}

    highs = x["high"].to_numpy(float)
    lows = x["low"].to_numpy(float)
    closes = x["close"].to_numpy(float)

    ph, pl = [], []
    for i in range(pivot, len(x) - pivot):
        if highs[i] == max(highs[i-pivot:i+pivot+1]):
            ph.append(i)
        if lows[i] == min(lows[i-pivot:i+pivot+1]):
            pl.append(i)

    # Ignore the latest 2 candles as pivots so the current closed candle cannot
    # create a self-referential swing level.
    ph = [i for i in ph if i < len(x) - 2]
    pl = [i for i in pl if i < len(x) - 2]
    if len(ph) < 2 or len(pl) < 2:
        return {"bullish": False, "bos": False, "retest": False,
                "choch": False, "swing_high": None, "swing_low": None,
                "broken_level": None}

    last_ph, prev_ph = ph[-1], ph[-2]
    last_pl, prev_pl = pl[-1], pl[-2]
    swing_high = float(highs[last_ph])
    swing_low = float(lows[last_pl])
    prev_high = float(highs[prev_ph])
    prev_low = float(lows[prev_pl])

    # Require a real structural close break with a small ATR-scaled buffer.
    atrv = float(x.iloc[-1].get("atr", np.nan))
    if not np.isfinite(atrv) or atrv <= 0:
        atrv = float(np.mean(np.maximum(highs[-14:] - lows[-14:], 1e-12)))
    buffer = atrv * 0.08
    current = closes[-1]
    prior = closes[-2]

    bos = bool(current > swing_high + buffer)
    choch = bool(current > prev_high + buffer and prior <= prev_high + buffer)

    # Proper retest: a prior candle must have closed above the broken level,
    # a later candle must trade back to the level, and the current candle must
    # reclaim it with a close above the level and EMA21.
    retest = False
    broken_level = None
    if bos:
        broken_level = swing_high
    elif choch:
        broken_level = prev_high
    else:
        for i in range(max(0, len(x)-10), len(x)-2):
            level = swing_high
            if closes[i] > level + buffer:
                pullback_low = float(lows[i+1:-1].min()) if i+1 < len(x)-1 else float("inf")
                if pullback_low <= level + atrv * 0.35 and current > level + buffer * 0.25:
                    broken_level = level
                    retest = True
                    break

    # If the current candle itself is the reclaim after a pullback, accept it.
    if not retest and broken_level is not None:
        recent_low = float(lows[-3:-1].min()) if len(x) >= 3 else float(lows[-2])
        retest = bool(recent_low <= broken_level + atrv * 0.35 and current > broken_level + buffer * 0.25)

    higher_low = last_pl > prev_pl and lows[last_pl] >= prev_low * 0.985
    bullish = bool(current > x.iloc[-1].ema50 and (higher_low or bos or choch or retest))
    return {"bullish": bullish, "bos": bos, "retest": retest, "choch": choch,
            "swing_high": swing_high, "swing_low": swing_low,
            "broken_level": broken_level}


def rsi_bullish_divergence(h):
    if len(h) < 35:
        return False
    a = h.iloc[-25:-8]
    b = h.iloc[-8:]
    pa = a.low.min()
    pb = b.low.min()
    ra = a.loc[a.low.idxmin(), "rsi"]
    rb = b.loc[b.low.idxmin(), "rsi"]
    return pb < pa and rb > ra + 2.0


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
    r = h.iloc[-1]
    p = h.iloc[-2]
    return (
        r.macd_hist > p.macd_hist
        and r.macd_hist > -abs(r.atr) * 0.15
    )


def h4_structure_ok(h):
    r = h.iloc[-1]
    return (
        r.close > r.ema50
        and r.ema21_slope > -0.05
        and r.pdi > r.mdi
        and r.close <= r.ema21 * (1 + MAX_H4_EMA21_DISTANCE / 100)
    )


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
        r.macd_hist >= p.macd_hist
        or (r.macd_hist > 0 and r.macd >= r.macd_signal)
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
    details["confirmed"] = bool(details["confirmations"] >= 4 and not bearish_contradiction and location_ok)
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
        "macd_ok": bool(r.macd_hist >= p.macd_hist or (r.macd_hist > 0 and r.macd >= r.macd_signal)),
        "volume_ok": bool(r.vol_ratio >= ONE_H_VOL_MIN),
        "di_ok": bool(r.pdi >= r.mdi or r.pdi > p.pdi),
        "hard_failure": bool(r.close < r.ema21 and r.macd_hist < p.macd_hist and r.rsi < p.rsi - 1.0 and r.pdi < r.mdi),
    }
    details["confirmations"] = sum(details[k] for k in ("price_ok","structure_ok","rsi_ok","macd_ok","volume_ok","di_ok"))
    details["confirmed"] = bool(details["confirmations"] >= 4 and not details["hard_failure"])
    return details


def one_hour_bottom_confirm(h):
    """Reversal confirmation: 4 of 6 independent 1H clues, no hard failure."""
    return h1_bottom_confirmation_details(h)["confirmed"]


# ---------------- STRATEGIES ----------------
def trend_setup(d, h, h1, btc_d, btc_h):
    r = h.iloc[-1]
    reasons = []
    score = 0

    if btc_d == "BEARISH" or btc_h == "BEARISH":
        return None, ["BTC bearish"]

    if not h4_structure_ok(h):
        return None, ["4H structure not bullish enough"]

    ms = market_structure(h)
    if not ms["bullish"]:
        return None, ["4H market structure not bullish enough"]

    if r.adx < H4_ADX_MIN and not (r.adx >= H4_ADX_RISING_MIN and r.adx > h.iloc[-2].adx):
        return None, ["4H ADX weak"]

    if r.vol_ratio < H4_VOL_MIN:
        return None, ["4H volume weak"]

    if r.pdi <= r.mdi:
        return None, ["4H DI bearish"]

    if r.macd_hist < 0 and r.macd_hist <= h.iloc[-2].macd_hist:
        return None, ["4H MACD weakening"]

    if r.taker_buy_ratio < TAKER_BUY_MIN:
        return None, ["taker buy weak"]

    # Daily must be supportive, but full EMA stack is a score bonus, not a mandatory wall.
    dr = daily_regime(d)
    if dr == "BEARISH":
        return None, ["Daily bearish"]

    score += 20
    reasons.append(f"Daily={dr}")

    score += 20  # 4H structure
    reasons.append("4H structure")
    if ms["bos"]:
        score += 6
        reasons.append("4H BOS")
    elif ms["retest"]:
        score += 4
        reasons.append("4H retest")

    fib = fibonacci_levels(h)
    if fib:
        price = float(r.close)
        if fib["0.618"] <= price <= fib["0.786"]:
            score += 4
            reasons.append("Fib 0.618-0.786 location")
        elif price <= fib["0.500"] and price >= fib["0.382"]:
            score += 2
            reasons.append("Fib pullback")

    if r.adx >= 25:
        score += 10
        reasons.append("ADX strong")
    elif r.adx >= 20:
        score += 7
        reasons.append("ADX healthy")

    if r.vol_ratio >= H4_VOL_STRONG:
        score += 10
        reasons.append("volume expansion")
    else:
        score += 6

    if r.close > r.ema9 > r.ema21 > r.ema50:
        score += 8
        reasons.append("EMA alignment")

    if r.close > r.ema200:
        score += 5

    if r.macd_hist > 0:
        score += 8
        reasons.append("MACD positive")
    elif r.macd_hist > h.iloc[-2].macd_hist:
        score += 5
        reasons.append("MACD improving")

    if r.rsi >= 52 and r.rsi <= 68:
        score += 7
    elif 48 <= r.rsi < 52:
        score += 4

    if r.taker_buy_ratio >= TAKER_BUY_STRONG:
        score += 5

    if btc_d == "BULLISH":
        score += 4
    if btc_h == "BULLISH":
        score += 4

    ok1 = one_hour_trend_confirm(h1)
    if not ok1:
        return None, reasons + ["1H confirmation failed"]

    score += 12
    reasons.append("1H confirmed")

    if score < MIN_SCORE:
        return None, reasons + [f"score {score} < {MIN_SCORE}"]

    return {
        "strategy": "CONFIRMED TREND",
        "score": min(score, 100),
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
    score += 5
    reasons.append("market structure reaction")

    if trigger_count < 3:
        return None, reasons + [f"only {trigger_count}/4 reversal triggers"]

    score += trigger_count * 8
    reasons.extend([k for k, v in triggers.items() if v])

    if r.vol_ratio < BOTTOM_VOL_MIN:
        return None, reasons + ["bottom volume too weak"]

    score += 12
    reasons.append("volume expansion")

    if r.taker_buy_ratio < TAKER_BUY_MIN:
        return None, reasons + ["taker buy weak"]

    score += 6
    if r.taker_buy_ratio >= TAKER_BUY_STRONG:
        score += 3
        reasons.append("strong taker buy")

    if r.trade_ratio >= TRADE_RATIO_MIN:
        score += 5
        reasons.append("trade activity healthy")
    else:
        return None, reasons + ["trade activity weak"]

    if r.adx >= 20:
        score += 8
        reasons.append("ADX healthy")
    elif r.adx >= 16 and r.adx > h.iloc[-2].adx:
        score += 4
        reasons.append("ADX recovering")
    else:
        return None, reasons + ["ADX not recovering"]

    if r.rsi >= 38 and r.rsi <= 62:
        score += 5

    if btc_d == "BULLISH":
        score += 5
    if btc_h == "BULLISH":
        score += 5

    if not one_hour_bottom_confirm(h1):
        return None, reasons + ["1H bottom confirmation failed"]

    score += 12
    reasons.append("1H reversal confirmed")

    if score < BOTTOM_MIN_SCORE:
        return None, reasons + [f"score {score} < {BOTTOM_MIN_SCORE}"]

    return {
        "strategy": "BOTTOM HUNTER",
        "score": min(score, 100),
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
def professional_signal_quality_gate(setup, trade, d, h, h1, btc_d, btc_h):
    """Hard gate: raw score can never compensate for missing trade quality."""
    reasons = []
    r = h.iloc[-1]
    p = h.iloc[-2]
    r1 = h1.iloc[-1]
    strategy = setup["strategy"]

    # Trade geometry first: these are hard requirements for a Telegram signal.
    if trade["rr1"] < MIN_RR_TP1:
        reasons.append(f"TP1 RR {trade['rr1']:.2f} < {MIN_RR_TP1:.2f}")
    if trade["rr2"] < MIN_RR_TP2:
        reasons.append(f"TP2 RR {trade['rr2']:.2f} < {MIN_RR_TP2:.2f}")
    if trade["risk_pct"] > (BOTTOM_MAX_SL_PCT if strategy == "BOTTOM HUNTER" else MAX_SL_PCT):
        reasons.append("stop distance too wide")

    # 1H is confirmation, not a second full strategy. Require a coherent
    # confirmation cluster and reject only a genuine bearish contradiction.
    if r1.vol_ratio < ONE_H_VOL_MIN:
        reasons.append(f"1H volume {r1.vol_ratio:.2f}x < {ONE_H_VOL_MIN:.2f}x")
    if strategy == "CONFIRMED TREND":
        if not one_hour_trend_confirm(h1):
            reasons.append("1H confirmation cluster insufficient")
    else:
        if not one_hour_bottom_confirm(h1):
            reasons.append("1H reversal confirmation cluster insufficient")

    # Higher-timeframe alignment. Bottom Hunter may be counter-trend, but not against both HTFs.
    if btc_d == "BEARISH" and btc_h == "BEARISH":
        reasons.append("BTC Daily and 4H both bearish")
    if strategy == "CONFIRMED TREND":
        if r.adx < H4_ADX_MIN:
            reasons.append(f"4H ADX {r.adx:.1f} < {H4_ADX_MIN:.0f}")
        if r.vol_ratio < H4_VOL_MIN:
            reasons.append(f"4H volume {r.vol_ratio:.2f}x < {H4_VOL_MIN:.2f}x")
        if r.pdi <= r.mdi:
            reasons.append("4H DI direction not confirmed")
        if r.close <= r.ema21 or r.close > r.ema21 * (1 + MAX_H4_EMA21_DISTANCE/100):
            reasons.append("4H entry location invalid")
    else:
        if r.vol_ratio < BOTTOM_VOL_MIN:
            reasons.append(f"Bottom Hunter 4H volume {r.vol_ratio:.2f}x < {BOTTOM_VOL_MIN:.2f}x")
        if not (r.adx >= H4_ADX_MIN or (r.adx >= H4_ADX_RISING_MIN and r.adx > p.adx)):
            reasons.append("Bottom Hunter ADX not recovering")

    # Raw score is only a ticket, never the proof of quality.
    minimum = BOTTOM_MIN_SCORE if strategy == "BOTTOM HUNTER" else MIN_SCORE
    if setup["score"] < minimum:
        reasons.append(f"score {setup['score']} < {minimum}")

    # Bottom Hunter requires at least 3 independent reversal confirmations.
    if strategy == "BOTTOM HUNTER":
        trigger_words = ("RSI divergence", "bullish rejection", "MACD turn", "4H reclaim")
        trigger_count = sum(1 for x in trigger_words if x in setup.get("reasons", []))
        if trigger_count < 3:
            reasons.append(f"only {trigger_count}/4 independent reversal triggers")

    return (len(reasons) == 0), reasons


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
    return f"{item['symbol']}|{item['setup']['strategy']}"


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

def diagnostic_trend_blocks(d, h, h1, btc_d, btc_h):
    """Diagnostic-only: evaluate ALL trend blockers without changing the real gate."""
    r = h.iloc[-1]
    blocks = []
    if btc_d == "BEARISH" or btc_h == "BEARISH":
        blocks.append("BTC bearish")
    if not h4_structure_ok(h):
        blocks.append("4H structure")
    if r.adx < H4_ADX_MIN and not (r.adx >= H4_ADX_RISING_MIN and r.adx > h.iloc[-2].adx):
        blocks.append("4H ADX")
    if r.vol_ratio < H4_VOL_MIN:
        blocks.append("4H volume")
    if r.pdi <= r.mdi:
        blocks.append("4H DI")
    if r.macd_hist < 0 and r.macd_hist <= h.iloc[-2].macd_hist:
        blocks.append("4H MACD")
    if r.taker_buy_ratio < TAKER_BUY_MIN:
        blocks.append("taker buy")
    if daily_regime(d) == "BEARISH":
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
    if not (r.adx >= H4_ADX_MIN or (r.adx >= H4_ADX_RISING_MIN and r.adx > h.iloc[-2].adx)):
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


def trend_base_quality(d, h, btc_d, btc_h):
    """Daily+4H trend quality only; deliberately excludes every 1H rule."""
    r = h.iloc[-1]
    if btc_d == "BEARISH" or btc_h == "BEARISH":
        return False
    if not h4_structure_ok(h):
        return False
    if r.adx < H4_ADX_MIN and not (r.adx >= H4_ADX_RISING_MIN and r.adx > h.iloc[-2].adx):
        return False
    if r.vol_ratio < H4_VOL_MIN:
        return False
    if r.pdi <= r.mdi:
        return False
    if r.macd_hist < 0 and r.macd_hist <= h.iloc[-2].macd_hist:
        return False
    if r.taker_buy_ratio < TAKER_BUY_MIN:
        return False
    if daily_regime(d) == "BEARISH":
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
    if not (r.adx >= H4_ADX_MIN or (r.adx >= H4_ADX_RISING_MIN and r.adx > h.iloc[-2].adx)):
        return False
    return True


def analyze_symbol(symbol, btc_d, btc_h):
    stats = {
        "scanned": 1,
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
    }

    try:
        d = add_indicators(klines(symbol, "1d", 250))
        h = add_indicators(klines(symbol, "4h", 250))
        h1 = add_indicators(klines(symbol, "1h", 250))

        if min(len(d), len(h), len(h1)) < 210:
            return None, stats, "insufficient data"

        # Deep diagnostics: this observes every blocker independently.
        # It does NOT modify trend_setup/bottom_setup or the final quality gate.
        tblocks = diagnostic_trend_blocks(d, h, h1, btc_d, btc_h)
        bblocks = diagnostic_bottom_blocks(d, h, h1, btc_d, btc_h)
        stats["trend_blockers"].update(tblocks)
        stats["bottom_blockers"].update(bblocks)

        td = h1_trend_confirmation_details(h1)
        if not td["confirmed"]:
            if td["bearish_contradiction"]: stats["h1_trend_failures"]["bearish contradiction"] += 1
            if not td["price_ok"]: stats["h1_trend_failures"]["price/EMA21"] += 1
            if not td["structure_ok"]: stats["h1_trend_failures"]["structure"] += 1
            if not td["rsi_ok"]: stats["h1_trend_failures"]["RSI"] += 1
            if not td["macd_ok"]: stats["h1_trend_failures"]["MACD"] += 1
            if not td["volume_ok"]: stats["h1_trend_failures"]["volume"] += 1
            if not td["location_ok"]: stats["h1_trend_failures"]["location"] += 1

        bd = h1_bottom_confirmation_details(h1)
        if not bd["confirmed"]:
            if bd["hard_failure"]: stats["h1_bottom_failures"]["hard reversal failure"] += 1
            if not bd["price_ok"]: stats["h1_bottom_failures"]["price/EMA9"] += 1
            if not bd["structure_ok"]: stats["h1_bottom_failures"]["structure"] += 1
            if not bd["rsi_ok"]: stats["h1_bottom_failures"]["RSI"] += 1
            if not bd["macd_ok"]: stats["h1_bottom_failures"]["MACD"] += 1
            if not bd["volume_ok"]: stats["h1_bottom_failures"]["volume"] += 1
            if not bd["di_ok"]: stats["h1_bottom_failures"]["DI"] += 1
        if tblocks:
            if len(tblocks) == 1:
                stats["trend_block_single"] += 1
            else:
                stats["trend_block_multiple"] += 1
        if bblocks:
            if len(bblocks) == 1:
                stats["bottom_block_single"] += 1
            else:
                stats["bottom_block_multiple"] += 1

        # TRUE MTF diagnostic: evaluate the actual Daily+4H gate independently
        # of the 1H confirmation. This fixes the old diagnostic bug where
        # overlapping blocker labels could make a valid base setup disappear.
        trend_base_ok = trend_base_quality(d, h, btc_d, btc_h)
        bottom_base_ok = bottom_base_quality(d, h, btc_d, btc_h)
        trend_h1_ok = h1_trend_confirmation_details(h1)["confirmed"]
        bottom_h1_ok = h1_bottom_confirmation_details(h1)["confirmed"]

        if trend_base_ok:
            stats["trend_base_pass"] += 1
            if not trend_h1_ok:
                stats["trend_1h_only_fail"] += 1
                stats["h1_reject"] += 1
        if bottom_base_ok:
            stats["bottom_base_pass"] += 1
            if not bottom_h1_ok:
                stats["bottom_1h_only_fail"] += 1
                stats["h1_reject"] += 1

        setups = []

        trend, trend_reasons = trend_setup(d, h, h1, btc_d, btc_h)
        if trend:
            stats["trend_candidate"] += 1
            setups.append((trend, d, h, h1))
        else:
            for reason in trend_reasons[:4]:
                stats["diagnostic_reasons"][f"TREND: {reason}"] += 1

        bottom, bottom_reasons = bottom_setup(d, h, h1, btc_d, btc_h)
        if bottom:
            stats["bottom_candidate"] += 1
            setups.append((bottom, d, h, h1))
        else:
            for reason in bottom_reasons[:4]:
                stats["diagnostic_reasons"][f"BOTTOM: {reason}"] += 1

        if not setups:
            # Diagnostic only: both engines rejected this symbol before candidate stage.
            top_reasons = []
            if trend_reasons:
                top_reasons.append(f"TREND={trend_reasons[0]}")
            if bottom_reasons:
                top_reasons.append(f"BOTTOM={bottom_reasons[0]}")
            return None, stats, f"{symbol} | NO_CANDIDATE | " + " | ".join(top_reasons)

        best = max(setups, key=lambda x: x[0]["score"])
        setup, d, h, h1 = best

        trade, err = build_trade(h, setup["strategy"])
        if not trade:
            stats["risk_reject"] += 1
            return None, stats, f"{symbol} | {setup['strategy']} | REJECT | {err}"

        passed, gate_reasons = professional_signal_quality_gate(
            setup, trade, d, h, h1, btc_d, btc_h
        )
        if not passed:
            stats["risk_reject"] += 1
            # Diagnostic only: this does NOT alter the quality gate or scoring logic.
            detail = " | ".join(gate_reasons[:8])
            return None, stats, f"{symbol} | {setup['strategy']} | REJECT | {detail}"

        stats["final"] += 1
        return {
            "symbol": symbol,
            "setup": setup,
            "trade": trade,
            "d": d,
            "h": h,
            "h1": h1,
            "btc_d": btc_d,
            "btc_h": btc_h,
        }, stats, None

    except Exception as e:
        return None, stats, f"error: {e}"


def aggregate(dst, src):
    for k in dst:
        if k in ("diagnostic_reasons", "trend_blockers", "bottom_blockers", "h1_trend_failures", "h1_bottom_failures"):
            dst[k].update(src.get(k, {}))
        else:
            dst[k] += src.get(k, 0)


def main():
    log.info("Starting ApexSignal v3.8 | PROFESSIONAL 4H SETUP ENGINE + MARKET STRUCTURE + FIB | STRICT QUALITY | DIAGNOSTICS")
    log.info("Primary: Daily direction + 4H setup | Entry confirmation: 1H | Trend + Bottom Hunter")
    log.info("No auto-trading.")

    universe = liquid_universe()
    log.info("Universe: %d valid Binance USDT spot pairs | NO COUNT CAP | 24H LIQUIDITY FILTER", len(universe))

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
        totals["trend_base_pass"] , max(0, totals["trend_base_pass"] - totals["trend_1h_only_fail"]),
        totals["trend_1h_only_fail"], totals["bottom_base_pass"],
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
        "h1_reject=%d | risk_reject=%d | final=%d | sent=%d",
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


if __name__ == "__main__":
    main()
