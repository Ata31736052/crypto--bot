# ============================================================
# Crypto Signal Bot - PRO MAX
# Binance Spot Data Only
# Primary: Daily (1D) + 4H
# Confirmation: 1H
# Strategies:
#   1) Confirmed Trend
#   2) Strict Bottom Hunter (Daily / 4H)
# No auto-trading
# ============================================================

import os
import json
import time
import logging
import html
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
import numpy as np


# =========================
# CONFIG
# =========================

class Config:
    BINANCE_BASE = "https://data-api.binance.vision"
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    DAILY_INTERVAL = "1d"
    PRIMARY_INTERVAL = "4h"
    CONFIRM_INTERVAL = "1h"

    KLINE_LIMIT_DAILY = 260
    KLINE_LIMIT_4H = 320
    KLINE_LIMIT_1H = 240

    MIN_SCORE = 84
    STRONG_SCORE = 91
    BOTTOM_MIN_SCORE = 86
    BOTTOM_STRONG_SCORE = 92

    MIN_24H_USDT_VOLUME = 5_000_000

    MIN_ADX_4H = 20
    STRONG_ADX_4H = 25
    MIN_ADX_DAILY = 18
    STRONG_ADX_DAILY = 23
    MIN_ADX_1H = 16

    MIN_VOLUME_RATIO_4H = 1.20
    STRONG_VOLUME_RATIO_4H = 1.50
    BOTTOM_VOLUME_RATIO_4H = 1.40
    MIN_VOLUME_RATIO_1H = 0.90

    MAX_DISTANCE_EMA21_4H = 3.5
    MAX_DISTANCE_EMA21_1H = 3.0

    TAKER_LONG = 0.53
    TAKER_STRONG_LONG = 0.58
    TAKER_SHORT = 0.47
    TAKER_STRONG_SHORT = 0.42

    ACCOUNT_USDT = 1000.0
    RISK_PCT = 1.0
    MAX_POSITION_USDT = 1000.0

    ATR_SL_MULTIPLIER = 1.35
    MAX_SL_PCT = 5.5
    BOTTOM_MAX_SL_PCT = 5.0

    TP1_PCT_TARGET = 2.5
    TP2_PCT_TARGET = 4.0
    MIN_RR_TP1 = 1.50
    MIN_RR_TP2 = 2.20

    BOTTOM_NEAR_LOW_PCT = 4.0
    BOTTOM_NEAR_LOW_4H_BARS = 18       # 72 hours
    BOTTOM_SWING_BARS = 30
    BOTTOM_FIB_TOLERANCE_ATR = 0.80

    MAX_CANDIDATES_1H = 25
    MAX_SIGNALS_PER_RUN = 3
    DEDUP_HOURS = 8

    WORKERS = 8
    HTTP_TIMEOUT = 20
    HTTP_RETRIES = 4

    SEND_SCAN_REPORT = True
    STATE_FILE = "signals_state.json"

    TZ = timezone(timedelta(hours=3, minutes=30))


CFG = Config()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("crypto-signal-bot")


# =========================
# HTTP
# =========================

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "CryptoSignalBot/ProMax"})


def http_get(path, params=None):
    url = CFG.BINANCE_BASE + path

    for attempt in range(CFG.HTTP_RETRIES):
        try:
            r = SESSION.get(url, params=params, timeout=CFG.HTTP_TIMEOUT)

            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "2"))
                time.sleep(max(wait, 2))
                continue

            r.raise_for_status()
            return r.json()

        except Exception as exc:
            if attempt == CFG.HTTP_RETRIES - 1:
                log.warning("HTTP failed %s: %s", path, exc)
                return None
            time.sleep(1.5 * (attempt + 1))

    return None


# =========================
# TELEGRAM
# =========================

def send_telegram(text):
    if not CFG.TELEGRAM_TOKEN or not CFG.TELEGRAM_CHAT_ID:
        log.warning("Telegram secrets are missing.")
        return False

    url = f"https://api.telegram.org/bot{CFG.TELEGRAM_TOKEN}/sendMessage"

    try:
        r = SESSION.post(
            url,
            data={
                "chat_id": CFG.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=CFG.HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)
        return False


# =========================
# HELPERS
# =========================

def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        x = float(v)
        if not np.isfinite(x):
            return default
        return x
    except Exception:
        return default


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def pct(a, b):
    if not b:
        return 0.0
    return (a / b - 1.0) * 100.0


def load_state():
    try:
        with open(CFG.STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(CFG.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        log.warning("State save failed: %s", exc)


def is_duplicate(state, key):
    ts = safe_float(state.get(key, 0), 0)
    return time.time() - ts < CFG.DEDUP_HOURS * 3600


def mark_signal(state, key):
    state[key] = time.time()


# =========================
# BINANCE UNIVERSE
# =========================

EXCLUDED_QUOTES = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "EUR", "TRY"}
EXCLUDED_ASSETS = {
    "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "EUR", "TRY",
}
EXCLUDED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "2L", "2S", "3L", "3S", "5L", "5S")


def get_universe():
    info = http_get("/api/v3/exchangeInfo")
    tickers = http_get("/api/v3/ticker/24hr")

    if not info or not tickers:
        return []

    ticker_map = {x.get("symbol"): x for x in tickers}
    result = []

    for s in info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("isSpotTradingAllowed") is False:
            continue

        symbol = s.get("symbol", "")
        base = s.get("baseAsset", "")

        if base in EXCLUDED_ASSETS:
            continue
        if any(base.endswith(x) for x in EXCLUDED_SUFFIXES):
            continue

        t = ticker_map.get(symbol)
        if not t:
            continue

        qv = safe_float(t.get("quoteVolume"))
        if qv < CFG.MIN_24H_USDT_VOLUME:
            continue

        result.append({
            "symbol": symbol,
            "quote_volume": qv,
            "last_price": safe_float(t.get("lastPrice")),
            "price_change_pct": safe_float(t.get("priceChangePercent")),
        })

    result.sort(key=lambda x: x["quote_volume"], reverse=True)
    log.info("Universe: %d liquid Binance USDT spot pairs", len(result))
    return result


# =========================
# KLINES
# =========================

def get_klines(symbol, interval, limit):
    data = http_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit}
    )

    if not data or len(data) < 50:
        return None

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_base", "taker_quote", "ignore"
    ]

    df = pd.DataFrame(data, columns=cols)

    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "trades", "taker_base", "taker_quote"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)

    return df


def drop_forming_candle(df):
    if df is None or df.empty:
        return df

    now = pd.Timestamp.now(tz="UTC")
    if df.iloc[-1]["close_time"] > now:
        return df.iloc[:-1].copy()

    return df.copy()


# =========================
# INDICATORS
# =========================

def add_indicators(df):
    x = df.copy()

    x["ema9"] = x["close"].ewm(span=9, adjust=False).mean()
    x["ema21"] = x["close"].ewm(span=21, adjust=False).mean()
    x["ema50"] = x["close"].ewm(span=50, adjust=False).mean()
    x["ema200"] = x["close"].ewm(span=200, adjust=False).mean()

    delta = x["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    x["rsi"] = (100 - (100 / (1 + rs))).fillna(50)

    ema12 = x["close"].ewm(span=12, adjust=False).mean()
    ema26 = x["close"].ewm(span=26, adjust=False).mean()
    x["macd"] = ema12 - ema26
    x["macd_signal"] = x["macd"].ewm(span=9, adjust=False).mean()
    x["macd_hist"] = x["macd"] - x["macd_signal"]
    x["macd_hist_delta"] = x["macd_hist"].diff()

    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    x["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    up = x["high"].diff()
    down = -x["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)

    atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr14.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr14.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)

    x["plus_di"] = plus_di.fillna(0)
    x["minus_di"] = minus_di.fillna(0)
    x["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0)

    x["vol_ma20"] = x["volume"].rolling(20).mean()
    x["volume_ratio"] = x["volume"] / x["vol_ma20"].replace(0, np.nan)

    x["quote_ma20"] = x["quote_volume"].rolling(20).mean()
    x["quote_volume_ratio"] = x["quote_volume"] / x["quote_ma20"].replace(0, np.nan)

    x["trade_ma20"] = x["trades"].rolling(20).mean()
    x["trade_ratio"] = x["trades"] / x["trade_ma20"].replace(0, np.nan)

    x["taker_buy_ratio"] = x["taker_quote"] / x["quote_volume"].replace(0, np.nan)

    x["body"] = (x["close"] - x["open"]).abs()
    x["range"] = (x["high"] - x["low"]).replace(0, np.nan)
    x["body_ratio"] = x["body"] / x["range"]
    x["close_location"] = (x["close"] - x["low"]) / x["range"]

    x["swing_high_30"] = x["high"].rolling(CFG.BOTTOM_SWING_BARS).max()
    x["swing_low_30"] = x["low"].rolling(CFG.BOTTOM_SWING_BARS).min()
    x["low_72h"] = x["low"].rolling(CFG.BOTTOM_NEAR_LOW_4H_BARS).min()

    return x.replace([np.inf, -np.inf], np.nan)


# =========================
# BTC REGIME
# =========================

def trend_state(df):
    if df is None or len(df) < 210:
        return "UNKNOWN"

    r = df.iloc[-1]
    close = safe_float(r["close"])
    ema50 = safe_float(r["ema50"])
    ema200 = safe_float(r["ema200"])
    adx = safe_float(r["adx"])
    pdi = safe_float(r["plus_di"])
    mdi = safe_float(r["minus_di"])

    if close > ema50 > ema200 and pdi > mdi and adx >= CFG.MIN_ADX_DAILY:
        return "BULLISH"

    if close < ema50 < ema200 and mdi > pdi and adx >= CFG.MIN_ADX_DAILY:
        return "BEARISH"

    return "MIXED"


def get_btc_context():
    d = get_klines("BTCUSDT", CFG.DAILY_INTERVAL, CFG.KLINE_LIMIT_DAILY)
    h4 = get_klines("BTCUSDT", CFG.PRIMARY_INTERVAL, CFG.KLINE_LIMIT_4H)

    if d is None or h4 is None:
        return "UNKNOWN", "UNKNOWN"

    d = add_indicators(drop_forming_candle(d))
    h4 = add_indicators(drop_forming_candle(h4))

    return trend_state(d), trend_state(h4)


# =========================
# DIVERGENCE / BOTTOM TOOLS
# =========================

def bullish_rsi_divergence(df, lookback=30):
    if df is None or len(df) < lookback + 5:
        return False

    x = df.iloc[-lookback:].copy()
    lows = x["low"].values
    rsis = x["rsi"].values

    pivot_idxs = []
    for i in range(2, len(x) - 2):
        if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]:
            pivot_idxs.append(i)

    if len(pivot_idxs) < 2:
        return False

    i1, i2 = pivot_idxs[-2], pivot_idxs[-1]

    return (
        lows[i2] <= lows[i1] * 1.01
        and rsis[i2] > rsis[i1] + 2.0
    )


def bearish_rsi_divergence(df, lookback=30):
    if df is None or len(df) < lookback + 5:
        return False

    x = df.iloc[-lookback:].copy()
    highs = x["high"].values
    rsis = x["rsi"].values

    pivot_idxs = []
    for i in range(2, len(x) - 2):
        if highs[i] >= highs[i-1] and highs[i] >= highs[i+1]:
            pivot_idxs.append(i)

    if len(pivot_idxs) < 2:
        return False

    i1, i2 = pivot_idxs[-2], pivot_idxs[-1]

    return (
        highs[i2] >= highs[i1] * 0.99
        and rsis[i2] < rsis[i1] - 2.0
    )


def fib_zone(row):
    hi = safe_float(row["swing_high_30"])
    lo = safe_float(row["swing_low_30"])
    price = safe_float(row["close"])
    atr = safe_float(row["atr"])

    if hi <= lo or price <= 0:
        return False, None

    fib618 = hi - 0.618 * (hi - lo)
    fib786 = hi - 0.786 * (hi - lo)

    zone_low = min(fib618, fib786)
    zone_high = max(fib618, fib786)

    tolerance = max(atr * CFG.BOTTOM_FIB_TOLERANCE_ATR, price * 0.004)

    inside = zone_low - tolerance <= price <= zone_high + tolerance
    return inside, (fib618, fib786)


# =========================
# 1H CONFIRMATION
# =========================

def confirm_1h(symbol, direction):
    df = get_klines(symbol, CFG.CONFIRM_INTERVAL, CFG.KLINE_LIMIT_1H)

    if df is None or len(df) < 100:
        return False, {}

    df = add_indicators(drop_forming_candle(df))
    r = df.iloc[-1]
    p = df.iloc[-2]

    price = safe_float(r["close"])
    ema9 = safe_float(r["ema9"])
    ema21 = safe_float(r["ema21"])
    rsi = safe_float(r["rsi"])
    adx = safe_float(r["adx"])
    vol = safe_float(r["volume_ratio"])
    hist = safe_float(r["macd_hist"])
    prev_hist = safe_float(p["macd_hist"])

    distance = abs(pct(price, ema21))

    if distance > CFG.MAX_DISTANCE_EMA21_1H:
        return False, {}

    if direction == "LONG":
        ok = (
            price > ema21
            and ema9 >= ema21
            and rsi >= 45
            and rsi <= 68
            and hist > 0
            and hist >= prev_hist
            and safe_float(r["plus_di"]) > safe_float(r["minus_di"])
            and adx >= CFG.MIN_ADX_1H
            and vol >= CFG.MIN_VOLUME_RATIO_1H
        )
    else:
        ok = (
            price < ema21
            and ema9 <= ema21
            and rsi >= 32
            and rsi <= 55
            and hist < 0
            and hist <= prev_hist
            and safe_float(r["minus_di"]) > safe_float(r["plus_di"])
            and adx >= CFG.MIN_ADX_1H
            and vol >= CFG.MIN_VOLUME_RATIO_1H
        )

    return ok, {
        "price": price,
        "rsi": rsi,
        "adx": adx,
        "volume_ratio": vol,
        "ema9": ema9,
        "ema21": ema21,
    }


# =========================
# RISK / TARGETS
# =========================

def build_trade(row, direction, bottom=False):
    price = safe_float(row["close"])
    atr = safe_float(row["atr"])
    swing_low = safe_float(row["swing_low_30"])
    swing_high = safe_float(row["swing_high_30"])

    if price <= 0 or atr <= 0:
        return None

    if direction == "LONG":
        structural_sl = swing_low * 0.997 if swing_low > 0 else price - atr
        atr_sl = price - atr * CFG.ATR_SL_MULTIPLIER
        sl = min(structural_sl, atr_sl)
        risk = price - sl

        if risk <= 0:
            return None

        sl_pct = risk / price * 100

        max_sl = CFG.BOTTOM_MAX_SL_PCT if bottom else CFG.MAX_SL_PCT
        if sl_pct > max_sl:
            return None

        tp1 = max(price * (1 + CFG.TP1_PCT_TARGET / 100), price + risk * CFG.MIN_RR_TP1)
        tp2 = max(price * (1 + CFG.TP2_PCT_TARGET / 100), price + risk * CFG.MIN_RR_TP2)

        if swing_high > price:
            resistance_cap = swing_high * 0.995
            if resistance_cap > price:
                tp1 = min(tp1, resistance_cap)

        rr1 = (tp1 - price) / risk
        rr2 = (tp2 - price) / risk

    else:
        structural_sl = swing_high * 1.003 if swing_high > 0 else price + atr
        atr_sl = price + atr * CFG.ATR_SL_MULTIPLIER
        sl = max(structural_sl, atr_sl)
        risk = sl - price

        if risk <= 0:
            return None

        sl_pct = risk / price * 100

        if sl_pct > CFG.MAX_SL_PCT:
            return None

        tp1 = min(price * (1 - CFG.TP1_PCT_TARGET / 100), price - risk * CFG.MIN_RR_TP1)
        tp2 = min(price * (1 - CFG.TP2_PCT_TARGET / 100), price - risk * CFG.MIN_RR_TP2)

        if swing_low < price:
            support_cap = swing_low * 1.005
            if support_cap < price:
                tp1 = max(tp1, support_cap)

        rr1 = (price - tp1) / risk
        rr2 = (price - tp2) / risk

    if rr1 < CFG.MIN_RR_TP1 or rr2 < CFG.MIN_RR_TP2:
        return None

    risk_usdt = CFG.ACCOUNT_USDT * CFG.RISK_PCT / 100
    position = risk_usdt / (risk / price)
    position = min(position, CFG.MAX_POSITION_USDT)

    return {
        "entry": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "sl_pct": sl_pct,
        "rr1": rr1,
        "rr2": rr2,
        "risk_usdt": risk_usdt,
        "position": position,
    }


# =========================
# TREND STRATEGY
# =========================

def analyze_trend(symbol, daily, h4, btc_daily, btc_4h):
    d = daily.iloc[-1]
    r = h4.iloc[-1]
    p = h4.iloc[-2]

    price = safe_float(r["close"])

    # Hard trend structure.
    bull_4h = (
        price > r["ema9"] > r["ema21"] > r["ema50"] > r["ema200"]
        and r["plus_di"] > r["minus_di"]
        and r["adx"] >= CFG.MIN_ADX_4H
    )

    bear_4h = (
        price < r["ema9"] < r["ema21"] < r["ema50"] < r["ema200"]
        and r["minus_di"] > r["plus_di"]
        and r["adx"] >= CFG.MIN_ADX_4H
    )

    if bull_4h:
        direction = "LONG"
    elif bear_4h:
        direction = "SHORT"
    else:
        return None

    # Daily must agree. This is deliberately hard, not just scoring.
    if direction == "LONG":
        daily_ok = (
            d["close"] > d["ema50"] > d["ema200"]
            and d["plus_di"] > d["minus_di"]
            and d["adx"] >= CFG.MIN_ADX_DAILY
        )
        if not daily_ok or btc_daily == "BEARISH":
            return None

    else:
        daily_ok = (
            d["close"] < d["ema50"] < d["ema200"]
            and d["minus_di"] > d["plus_di"]
            and d["adx"] >= CFG.MIN_ADX_DAILY
        )
        if not daily_ok or btc_daily == "BULLISH":
            return None

    # No late chase.
    distance_ema21 = abs(pct(price, r["ema21"]))
    if distance_ema21 > CFG.MAX_DISTANCE_EMA21_4H:
        return None

    # Hard volume gate.
    if safe_float(r["volume_ratio"]) < CFG.MIN_VOLUME_RATIO_4H:
        return None

    # Momentum hard gate.
    if direction == "LONG":
        if not (
            45 <= r["rsi"] <= 68
            and r["macd_hist"] > 0
            and r["macd_hist"] >= p["macd_hist"]
            and r["plus_di"] > r["minus_di"]
        ):
            return None
        if safe_float(r["taker_buy_ratio"]) < CFG.TAKER_LONG:
            return None
    else:
        if not (
            32 <= r["rsi"] <= 55
            and r["macd_hist"] < 0
            and r["macd_hist"] <= p["macd_hist"]
            and r["minus_di"] > r["plus_di"]
        ):
            return None
        if safe_float(r["taker_buy_ratio"]) > CFG.TAKER_SHORT:
            return None

    score = 0
    reasons = []

    score += 20
    reasons.append("4H EMA trend fully aligned")

    if d["adx"] >= CFG.STRONG_ADX_DAILY:
        score += 10
        reasons.append("Daily ADX strong")
    else:
        score += 6
        reasons.append("Daily trend confirmed")

    if r["adx"] >= CFG.STRONG_ADX_4H:
        score += 15
        reasons.append("4H ADX strong")
    else:
        score += 9

    score += 15
    reasons.append("MACD momentum confirmed")

    if r["volume_ratio"] >= CFG.STRONG_VOLUME_RATIO_4H:
        score += 15
        reasons.append("Volume expansion strong")
    else:
        score += 9
        reasons.append("Volume confirmed")

    if direction == "LONG" and r["taker_buy_ratio"] >= CFG.TAKER_STRONG_LONG:
        score += 10
        reasons.append("Buyer pressure strong")
    elif direction == "SHORT" and r["taker_buy_ratio"] <= CFG.TAKER_STRONG_SHORT:
        score += 10
        reasons.append("Seller pressure strong")
    else:
        score += 6

    if btc_4h == ("BULLISH" if direction == "LONG" else "BEARISH"):
        score += 8
        reasons.append("BTC 4H agrees")
    elif btc_4h == "MIXED":
        score += 3

    if direction == "LONG" and bullish_rsi_divergence(h4):
        score += 5
    elif direction == "SHORT" and bearish_rsi_divergence(h4):
        score += 5

    if score < CFG.MIN_SCORE:
        return None

    trade = build_trade(r, direction, bottom=False)
    if not trade:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "setup": "CONFIRMED TREND",
        "score": min(score, 100),
        "strength": "STRONG" if score >= CFG.STRONG_SCORE else "VALID",
        "trade": trade,
        "rsi": safe_float(r["rsi"]),
        "adx": safe_float(r["adx"]),
        "volume_ratio": safe_float(r["volume_ratio"]),
        "taker_buy_ratio": safe_float(r["taker_buy_ratio"]),
        "reasons": reasons,
        "warnings": [],
    }


# =========================
# BOTTOM HUNTER
# =========================

def analyze_bottom(symbol, daily, h4, btc_daily, btc_4h):
    d = daily.iloc[-1]
    r = h4.iloc[-1]
    p = h4.iloc[-2]

    price = safe_float(r["close"])
    atr = safe_float(r["atr"])

    if price <= 0 or atr <= 0:
        return None

    # Never buy a coin while Daily is strongly bearish.
    if btc_daily == "BEARISH":
        return None

    daily_strong_bear = (
        d["close"] < d["ema50"] < d["ema200"]
        and d["minus_di"] > d["plus_di"]
        and d["adx"] >= CFG.STRONG_ADX_DAILY
    )
    if daily_strong_bear:
        return None

    low72 = safe_float(r["low_72h"])
    if low72 <= 0:
        return None

    near_low_pct = (price / low72 - 1) * 100
    if near_low_pct > CFG.BOTTOM_NEAR_LOW_PCT:
        return None

    # Fib zone.
    fib_ok, fib_values = fib_zone(r)
    if not fib_ok:
        return None

    # Do not catch a falling knife.
    bullish_candle = (
        r["close"] > r["open"]
        and r["close_location"] >= 0.62
        and r["body_ratio"] >= 0.35
    )

    reclaim_4h = (
        r["close"] > r["ema9"]
        or r["close"] > p["high"]
    )

    macd_turn = (
        r["macd_hist"] > p["macd_hist"]
        and r["macd_hist_delta"] > 0
    )

    rsi_recovery = (
        28 <= r["rsi"] <= 48
        and r["rsi"] >= p["rsi"]
    )

    divergence = bullish_rsi_divergence(h4)

    volume_ok = r["volume_ratio"] >= CFG.BOTTOM_VOLUME_RATIO_4H
    taker_ok = r["taker_buy_ratio"] >= CFG.TAKER_LONG
    trade_ok = r["trade_ratio"] >= 1.0

    # At least one strong reversal trigger is mandatory.
    reversal_triggers = sum([
        bool(divergence),
        bool(bullish_candle),
        bool(reclaim_4h),
        bool(macd_turn),
    ])

    if reversal_triggers < 3:
        return None

    # Volume + buyer pressure are hard requirements.
    if not volume_ok or not taker_ok or not trade_ok:
        return None

    # 4H ADX can be lower at a real bottom, but cannot be completely dead.
    if r["adx"] < 14:
        return None

    score = 0
    reasons = []

    # Location.
    score += 18
    reasons.append(f"Near 72H low ({near_low_pct:.1f}%)")

    score += 12
    reasons.append("Fib 0.618-0.786 zone")

    if divergence:
        score += 15
        reasons.append("Bullish RSI divergence")

    if bullish_candle:
        score += 10
        reasons.append("Strong bullish reversal candle")

    if reclaim_4h:
        score += 12
        reasons.append("4H structure/EMA reclaim")

    if macd_turn:
        score += 12
        reasons.append("MACD turning upward")

    if r["volume_ratio"] >= 1.70:
        score += 10
        reasons.append("Strong volume expansion")
    else:
        score += 6
        reasons.append("Volume expansion confirmed")

    if r["taker_buy_ratio"] >= CFG.TAKER_STRONG_LONG:
        score += 8
        reasons.append("Strong buyer pressure")
    else:
        score += 5

    if btc_4h == "BULLISH":
        score += 8
        reasons.append("BTC 4H bullish")
    elif btc_4h == "MIXED":
        score += 3

    if rsi_recovery:
        score += 5
        reasons.append("RSI recovering")

    if score < CFG.BOTTOM_MIN_SCORE:
        return None

    trade = build_trade(r, "LONG", bottom=True)
    if not trade:
        return None

    return {
        "symbol": symbol,
        "direction": "LONG",
        "setup": "BOTTOM HUNTER",
        "score": min(score, 100),
        "strength": "STRONG" if score >= CFG.BOTTOM_STRONG_SCORE else "VALID",
        "trade": trade,
        "rsi": safe_float(r["rsi"]),
        "adx": safe_float(r["adx"]),
        "volume_ratio": safe_float(r["volume_ratio"]),
        "taker_buy_ratio": safe_float(r["taker_buy_ratio"]),
        "reasons": reasons,
        "warnings": [
            "Bottom reversal setup: confirmation is required; not a blind dip buy."
        ],
    }


# =========================
# COIN ANALYSIS
# =========================

def analyze_coin(symbol, btc_daily, btc_4h):
    daily = get_klines(symbol, CFG.DAILY_INTERVAL, CFG.KLINE_LIMIT_DAILY)
    h4 = get_klines(symbol, CFG.PRIMARY_INTERVAL, CFG.KLINE_LIMIT_4H)

    if daily is None or h4 is None:
        return None

    daily = add_indicators(drop_forming_candle(daily))
    h4 = add_indicators(drop_forming_candle(h4))

    if len(daily) < 210 or len(h4) < 210:
        return None

    candidates = []

    trend = analyze_trend(symbol, daily, h4, btc_daily, btc_4h)
    if trend:
        candidates.append(trend)

    bottom = analyze_bottom(symbol, daily, h4, btc_daily, btc_4h)
    if bottom:
        candidates.append(bottom)

    if not candidates:
        return None

    # Highest quality setup wins.
    best = max(candidates, key=lambda x: x["score"])

    ok, confirm = confirm_1h(symbol, best["direction"])
    if not ok:
        return None

    best["confirm_1h"] = confirm
    return best


# =========================
# TELEGRAM FORMAT
# =========================

def fmt_price(v):
    if v >= 100:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    if v >= 0.01:
        return f"{v:,.6f}"
    return f"{v:.10f}"


def format_signal(sig):
    t = sig["trade"]

    emoji = "🟢" if sig["direction"] == "LONG" else "🔴"

    reasons = "\n".join(
        f"• {html.escape(str(x))}" for x in sig.get("reasons", [])[:10]
    )

    warnings = "\n".join(
        f"⚠️ {html.escape(str(x))}" for x in sig.get("warnings", [])
    )

    return (
        f"{emoji} <b>{html.escape(sig['symbol'])}</b>\n"
        f"<b>Setup:</b> {html.escape(sig['setup'])}\n"
        f"<b>Direction:</b> {sig['direction']}\n"
        f"<b>Quality:</b> {sig['strength']} | Score {sig['score']}/100\n\n"
        f"<b>Entry:</b> {fmt_price(t['entry'])}\n"
        f"<b>SL:</b> {fmt_price(t['sl'])} ({t['sl_pct']:.2f}%)\n"
        f"<b>TP1:</b> {fmt_price(t['tp1'])} | RR {t['rr1']:.2f}\n"
        f"<b>TP2:</b> {fmt_price(t['tp2'])} | RR {t['rr2']:.2f}\n\n"
        f"<b>RSI 4H:</b> {sig['rsi']:.1f}\n"
        f"<b>ADX 4H:</b> {sig['adx']:.1f}\n"
        f"<b>Volume:</b> {sig['volume_ratio']:.2f}x\n"
        f"<b>Taker buy:</b> {sig['taker_buy_ratio']:.2%}\n"
        f"<b>1H:</b> confirmed\n\n"
        f"<b>Why:</b>\n{reasons}\n"
        f"{warnings}\n\n"
        f"<b>Risk:</b> ${t['risk_usdt']:.2f}\n"
        f"<b>Position cap:</b> ${t['position']:.2f}\n"
        f"<i>Signal only — no auto-trading.</i>"
    )


# =========================
# SCAN
# =========================

def scan():
    start = time.time()

    universe = get_universe()
    if not universe:
        send_telegram("❌ Binance universe could not be loaded.")
        return

    btc_daily, btc_4h = get_btc_context()
    log.info("BTC regime: Daily=%s | 4H=%s", btc_daily, btc_4h)

    results = []
    total = len(universe)

    def worker(item):
        try:
            return analyze_coin(item["symbol"], btc_daily, btc_4h)
        except Exception as exc:
            log.warning("Analysis failed %s: %s", item["symbol"], exc)
            return None

    with ThreadPoolExecutor(max_workers=CFG.WORKERS) as ex:
        futures = [ex.submit(worker, item) for item in universe]

        for i, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            if result:
                results.append(result)

            if i % 25 == 0:
                log.info("Progress %d/%d | candidates=%d", i, total, len(results))

    results.sort(key=lambda x: x["score"], reverse=True)

    state = load_state()
    sent = 0
    selected = []

    for sig in results:
        if sent >= CFG.MAX_SIGNALS_PER_RUN:
            break

        candle_key = sig["symbol"] + "|" + sig["direction"] + "|" + sig["setup"]
        if is_duplicate(state, candle_key):
            continue

        selected.append(sig)
        mark_signal(state, candle_key)
        send_telegram(format_signal(sig))
        sent += 1

    save_state(state)

    elapsed = time.time() - start

    if CFG.SEND_SCAN_REPORT:
        report = (
            f"📊 <b>Scan Report</b>\n\n"
            f"Universe: {total}\n"
            f"Qualified before dedup: {len(results)}\n"
            f"Sent: {sent}\n"
            f"BTC Daily: {btc_daily}\n"
            f"BTC 4H: {btc_4h}\n"
            f"Time: {elapsed:.1f}s\n\n"
            f"<b>Policy:</b> weak signals are intentionally filtered."
        )
        send_telegram(report)


def main():
    log.info("Starting Crypto Signal Bot PRO MAX")
    log.info("Primary logic: Daily + 4H | 1H confirmation | Bottom Hunter enabled")
    scan()


if __name__ == "__main__":
    main()
