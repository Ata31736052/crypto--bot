
# ============================================================
# Crypto Signal Bot - ROOT REBUILD v3.4
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
MIN_24H_USDT_VOLUME = 0
MIN_SCORE = 88
STRONG_SCORE = 94
BOTTOM_MIN_SCORE = 90
BOTTOM_STRONG_SCORE = 95

# Quality gates
DAILY_ADX_MIN = 16.0
DAILY_ADX_STRONG = 23.0
H4_ADX_MIN = 18.0
H4_ADX_RISING_MIN = 15.0
H4_VOL_MIN = 1.30
H4_VOL_STRONG = 1.60
BOTTOM_VOL_MIN = 1.40
ONE_H_VOL_MIN = 0.90

TAKER_BUY_MIN = 0.52
TAKER_BUY_STRONG = 0.56
TRADE_RATIO_MIN = 0.95

MAX_H4_EMA21_DISTANCE = 3.0
MAX_1H_EMA21_DISTANCE = 2.5

ATR_SL_MULT = 1.25
MAX_SL_PCT = 5.0
BOTTOM_MAX_SL_PCT = 5.0
MIN_RR_TP1 = 1.80
MIN_RR_TP2 = 2.40

TP1_PCT = 0.025
TP2_PCT = 0.040

DEDUP_HOURS = 8

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("crypto-bot")

session = requests.Session()
session.headers.update({"User-Agent": "CryptoSignalBot/RootRebuild-v3.4"})


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
    """Return every currently tradable Binance Spot USDT pair.

    IMPORTANT: this function intentionally has NO coin-count cap and NO
    volume-based universe truncation. Liquidity is evaluated later as part
    of signal quality, so a low-volume pair cannot become a signal merely
    because it was included in the universe.
    """
    syms = exchange_symbols()
    # Keep the 24h ticker request for diagnostics/quality features, but never
    # use it to cut the symbol universe.
    try:
        get_json("/api/v3/ticker/24hr")
    except Exception:
        # ExchangeInfo is enough to define the universe; analysis will decide
        # whether a symbol has enough usable market data.
        pass
    return sorted(set(syms))


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


def fib_zone(h):
    recent = h.tail(30)
    lo = recent.low.min()
    hi = recent.high.max()
    span = hi - lo
    if span <= 0:
        return False, None
    f618 = hi - span * 0.618
    f786 = hi - span * 0.786
    price = h.iloc[-1].close
    atrv = h.iloc[-1].atr
    tolerance = max(atrv * 0.75, price * 0.003)
    ok = (f786 - tolerance) <= price <= (f618 + tolerance)
    return ok, (f618, f786)


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


def one_hour_trend_confirm(h):
    """Flexible-but-professional 1H confirmation.

    Daily+4H remain the setup engine. 1H only needs a coherent confirmation
    cluster; it must not require every single 1H indicator to agree at once.
    A hard bearish contradiction still fails the confirmation.
    """
    r = h.iloc[-1]
    p = h.iloc[-2]

    price_ok = r.close > r.ema21
    structure_ok = (r.ema9 >= r.ema21) or (r.close > p.high)
    rsi_ok = 44 <= r.rsi <= 72 and r.rsi >= p.rsi - 1.0
    macd_ok = r.macd_hist >= p.macd_hist
    volume_ok = r.vol_ratio >= 0.90

    confirmations = sum((price_ok, structure_ok, rsi_ok, macd_ok, volume_ok))
    bearish_contradiction = (
        r.close < r.ema21 * 0.995
        and r.pdi < r.mdi
        and r.macd_hist < p.macd_hist
    )
    location_ok = r.close <= r.ema21 * (1 + MAX_1H_EMA21_DISTANCE / 100)
    return confirmations >= 4 and not bearish_contradiction and location_ok


def one_hour_bottom_confirm(h):
    """Reversal confirmation: 4 of 6 independent 1H clues, no hard failure."""
    r = h.iloc[-1]
    p = h.iloc[-2]

    price_ok = r.close > r.ema9
    structure_ok = (r.close > p.high) or (r.close > r.ema21)
    rsi_ok = 42 <= r.rsi <= 70 and r.rsi >= p.rsi
    macd_ok = r.macd_hist >= p.macd_hist
    volume_ok = r.vol_ratio >= 0.90
    di_ok = r.pdi >= r.mdi or r.pdi > p.pdi

    confirmations = sum((price_ok, structure_ok, rsi_ok, macd_ok, volume_ok, di_ok))
    hard_failure = r.close < r.ema21 and r.macd_hist < p.macd_hist and r.rsi < p.rsi
    return confirmations >= 4 and not hard_failure


# ---------------- STRATEGIES ----------------
def trend_setup(d, h, h1, btc_d, btc_h):
    r = h.iloc[-1]
    reasons = []
    score = 0

    if btc_d == "BEARISH" or btc_h == "BEARISH":
        return None, ["BTC bearish"]

    if not h4_structure_ok(h):
        return None, ["4H structure not bullish enough"]

    if r.adx < 20.0 and not (r.adx >= H4_ADX_RISING_MIN and r.adx > h.iloc[-2].adx):
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

    if trigger_count < 2:
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

    recent10 = h.tail(10)
    swing_low = float(recent10.low.min())

    # Stop must be below entry, but never unnecessarily far away.
    atr_sl = entry - atrv * ATR_SL_MULT
    structural_sl = swing_low * 0.997
    sl = max(structural_sl, atr_sl)

    if sl >= entry:
        sl = atr_sl

    risk_pct = (entry - sl) / entry * 100
    if risk_pct <= 0:
        return None, "invalid SL"
    max_sl = BOTTOM_MAX_SL_PCT if strategy == "BOTTOM HUNTER" else MAX_SL_PCT
    if risk_pct > max_sl:
        return None, f"SL too wide {risk_pct:.2f}%"

    # Nearby resistance from recent 30 bars.
    resistance = float(h.tail(30)["high"].iloc[:-1].max())
    candidates = [
        entry * (1 + TP1_PCT),
        entry * (1 + TP2_PCT),
        resistance * 0.995,
    ]
    candidates = sorted(set(float(x) for x in candidates if x > entry))

    tp1 = None
    for t in candidates:
        rr = (t - entry) / (entry - sl)
        if rr >= MIN_RR_TP1:
            tp1 = t
            break

    if tp1 is None:
        return None, "no feasible TP1 with RR"

    tp2_candidates = [
        entry * (1 + TP2_PCT),
        resistance * 0.995,
        entry + (entry - sl) * MIN_RR_TP2,
    ]
    tp2_candidates = [
        float(x) for x in tp2_candidates if x > tp1
    ]

    tp2 = max(tp2_candidates) if tp2_candidates else None
    if tp2 is None:
        return None, "no feasible TP2"

    rr1 = (tp1 - entry) / (entry - sl)
    rr2 = (tp2 - entry) / (entry - sl)

    if rr2 < MIN_RR_TP2:
        return None, "TP2 RR too low"

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "risk_pct": risk_pct,
        "rr1": rr1,
        "rr2": rr2,
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
    if r1.vol_ratio < 0.90:
        reasons.append(f"1H volume {r1.vol_ratio:.2f}x < 0.90x")
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
        if r.adx < 20.0:
            reasons.append(f"4H ADX {r.adx:.1f} < 20")
        if r.vol_ratio < 1.30:
            reasons.append(f"4H volume {r.vol_ratio:.2f}x < 1.30x")
        if r.pdi <= r.mdi:
            reasons.append("4H DI direction not confirmed")
        if r.close <= r.ema21 or r.close > r.ema21 * (1 + MAX_H4_EMA21_DISTANCE/100):
            reasons.append("4H entry location invalid")
    else:
        if r.vol_ratio < 1.40:
            reasons.append(f"Bottom Hunter 4H volume {r.vol_ratio:.2f}x < 1.40x")
        if not (r.adx >= 20.0 or (r.adx >= 16.0 and r.adx > p.adx)):
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
        f"(RR {trade['rr2']:.2f})\n\n"
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
    if r.adx < 20.0 and not (r.adx >= H4_ADX_RISING_MIN and r.adx > h.iloc[-2].adx):
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
    if not one_hour_trend_confirm(h1):
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
    if sum(triggers.values()) < 2:
        blocks.append("reversal triggers")
    if r.vol_ratio < BOTTOM_VOL_MIN:
        blocks.append("bottom volume")
    if r.taker_buy_ratio < TAKER_BUY_MIN:
        blocks.append("taker buy")
    if r.trade_ratio < TRADE_RATIO_MIN:
        blocks.append("trade activity")
    if not (r.adx >= 20 or (r.adx >= 16 and r.adx > h.iloc[-2].adx)):
        blocks.append("ADX")
    if not one_hour_bottom_confirm(h1):
        blocks.append("1H confirmation")
    return blocks


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

        # MTF-only diagnostic: separate Daily+4H quality from the 1H gate.
        # This is intentionally diagnostic-only and does not alter the gate.
        trend_base_blocks = [x for x in tblocks if x != "1H confirmation"]
        if not trend_base_blocks:
            stats["trend_base_pass"] += 1
            if "1H confirmation" in tblocks:
                stats["trend_1h_only_fail"] += 1

        bottom_base_blocks = [x for x in bblocks if x != "1H confirmation"]
        if not bottom_base_blocks:
            stats["bottom_base_pass"] += 1
            if "1H confirmation" in bblocks:
                stats["bottom_1h_only_fail"] += 1

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
        if k in ("diagnostic_reasons", "trend_blockers", "bottom_blockers"):
            dst[k].update(src.get(k, {}))
        else:
            dst[k] += src.get(k, 0)


def main():
    log.info("Starting Crypto Signal Bot ROOT REBUILD v3.4 | STRICT PROFESSIONAL QUALITY | DIAGNOSTICS")
    log.info("Primary: Daily + 4H | Confirmation: 1H | Trend + Bottom Hunter")
    log.info("No auto-trading.")

    universe = liquid_universe()
    log.info("Universe: %d valid Binance USDT spot pairs | NO COUNT CAP | NO UNIVERSE VOLUME FILTER", len(universe))

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
    for item in results[:MAX_SIGNALS]:
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
        "SCAN REPORT | scanned=%d | trend_candidates=%d | bottom_candidates=%d | "
        "risk_reject=%d | final=%d | sent=%d",
        totals["scanned"],
        totals["trend_candidate"],
        totals["bottom_candidate"],
        totals["risk_reject"],
        totals["final"],
        sent,
    )

    if not results:
        log.info("No quality signal this run. This is intentional when market quality is poor.")


if __name__ == "__main__":
    main()
