
# ============================================================
# Crypto Signal Bot - ROOT REBUILD v3.1
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
MAX_SIGNALS = 3

MIN_24H_USDT_VOLUME = 5_000_000
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
ONE_H_VOL_MIN = 1.00

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
session.headers.update({"User-Agent": "CryptoSignalBot/RootRebuild-v3.1"})


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
    syms = exchange_symbols()
    tickers = get_json("/api/v3/ticker/24hr")
    tv = {
        x["symbol"]: float(x.get("quoteVolume", 0) or 0)
        for x in tickers
    }
    result = [
        s for s in syms
        if tv.get(s, 0) >= MIN_24H_USDT_VOLUME
    ]
    return result


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
    r = h.iloc[-1]
    p = h.iloc[-2]
    return (
        r.close > r.ema21
        and (r.ema9 >= r.ema21 or r.close > p.high)
        and 45 <= r.rsi <= 70
        and r.rsi >= p.rsi - 0.5
        and r.macd_hist >= p.macd_hist
        and r.vol_ratio >= ONE_H_VOL_MIN
        and r.close <= r.ema21 * (1 + MAX_1H_EMA21_DISTANCE / 100)
    )


def one_hour_bottom_confirm(h):
    r = h.iloc[-1]
    p = h.iloc[-2]
    return (
        r.close > r.ema9
        and r.rsi >= 43
        and r.rsi <= 68
        and r.rsi >= p.rsi
        and r.macd_hist >= p.macd_hist
        and r.vol_ratio >= ONE_H_VOL_MIN
        and (r.close > p.high or r.close > r.ema21)
    )


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

    # 1H confirmation must have actual momentum and volume, not just price above EMA.
    if r1.vol_ratio < 1.00:
        reasons.append(f"1H volume {r1.vol_ratio:.2f}x < 1.00x")
    if r1.adx < 16.0:
        reasons.append(f"1H ADX {r1.adx:.1f} < 16")
    if strategy == "CONFIRMED TREND":
        if r1.pdi <= r1.mdi:
            reasons.append("1H DI direction not confirmed")
        if r1.macd_hist <= 0 or r1.macd_hist < h1.iloc[-2].macd_hist:
            reasons.append("1H MACD momentum not confirmed")
    else:
        if r1.rsi < 43 or r1.rsi > 68 or r1.rsi < h1.iloc[-2].rsi:
            reasons.append("1H reversal momentum not confirmed")
        if r1.macd_hist < h1.iloc[-2].macd_hist:
            reasons.append("1H MACD still weakening")

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
def analyze_symbol(symbol, btc_d, btc_h):
    stats = {
        "scanned": 1,
        "trend_candidate": 0,
        "bottom_candidate": 0,
        "h1_reject": 0,
        "risk_reject": 0,
        "final": 0,
        "diagnostic_reasons": Counter(),
    }

    try:
        d = add_indicators(klines(symbol, "1d", 250))
        h = add_indicators(klines(symbol, "4h", 250))
        h1 = add_indicators(klines(symbol, "1h", 250))

        if min(len(d), len(h), len(h1)) < 210:
            return None, stats, "insufficient data"

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
        if k == "diagnostic_reasons":
            dst[k].update(src.get(k, {}))
        else:
            dst[k] += src.get(k, 0)


def main():
    log.info("Starting Crypto Signal Bot ROOT REBUILD v3.1 | STRICT PROFESSIONAL QUALITY | DIAGNOSTICS")
    log.info("Primary: Daily + 4H | Confirmation: 1H | Trend + Bottom Hunter")
    log.info("No auto-trading.")

    universe = liquid_universe()
    log.info("Universe: %d liquid Binance USDT spot pairs", len(universe))

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
