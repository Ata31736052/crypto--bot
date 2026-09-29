# ============================================================
# Crypto Signal Bot - Binance Spot / 4H
# Market universe: Binance USDT spot pairs
# Alerts: Telegram
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


# ============================================================
# CONFIG
# ============================================================

class Config:
    telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # Binance public market-data API
    binance_base = "https://data-api.binance.vision"

    timeframe = "4h"
    confirmation_timeframe = "1h"
    kline_limit = 300
    confirmation_kline_limit = 220

    # Signal quality
    min_score = 78
    strong_score = 88

    # Liquidity / volume
    min_24h_usdt_volume = 0
    max_1h_confirmation_candidates = 25
    min_volume_ratio = 1.20
    strong_volume_ratio = 1.45
    tape_buy_ratio_long = 0.53
    tape_buy_ratio_strong_long = 0.58
    tape_buy_ratio_short = 0.47
    tape_buy_ratio_strong_short = 0.42
    min_trade_count_ratio = 1.00
    confirmation_min_volume_ratio = 0.85

    # Indicators
    rsi_period = 14
    atr_period = 14
    macd_fast = 12
    macd_slow = 26
    macd_signal = 9
    adx_period = 14

    # Entry filters
    max_distance_from_ema21 = 3.5
    min_adx = 20.0
    strong_adx = 25.0
    confirmation_min_adx = 16.0

    # RSI divergence / market structure
    pivot_left = 3
    pivot_right = 3
    divergence_min_separation = 5

    # Risk calculation only; no orders are placed
    account_size_usdt = 1000.0
    risk_percent = 1.0
    max_position_usdt = 1000.0
    sl_atr_multiplier = 1.40
    max_sl_percent = 6.0
    tp1_rr = 1.80
    tp2_rr = 3.00

    # Operational
    max_signals_per_run = 3
    dedup_hours = 8
    max_workers = 8
    request_timeout = 20
    request_retries = 4
    send_scan_report = True
    state_file = "signals_state.json"
    timezone = timezone(timedelta(hours=3, minutes=30))

    # Assets that should not become directional signals
    excluded_assets = {
        "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP",
        "USDE", "USD1", "USDS", "EUR", "TRY", "BRL", "GBP", "AUD",
        "BIDR", "UAH", "RUB", "NGN", "PLN", "RON", "ZAR", "JPY",
    }

    # Common leveraged-token suffixes / exchange products
    excluded_suffixes = (
        "UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S",
    )


CFG = Config()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("crypto_bot")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "CryptoSignalBot/2.0",
    "Accept": "application/json",
})


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value)
    except Exception:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def fmt_price(price):
    price = safe_float(price)
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.4f}"
    if price >= 0.01:
        return f"{price:,.5f}"
    return f"{price:.8f}"


def utc_now():
    return datetime.now(timezone.utc)


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None, timeout=None):
    timeout = timeout or CFG.request_timeout
    last_error = None

    for attempt in range(CFG.request_retries):
        try:
            response = SESSION.get(url, params=params, timeout=timeout)

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait = safe_float(retry_after, 2 + attempt * 2)
                time.sleep(min(max(wait, 1), 12))
                continue

            if response.status_code in (418, 451):
                raise RuntimeError(f"Binance blocked request: HTTP {response.status_code}")

            response.raise_for_status()
            return response.json()

        except Exception as exc:
            last_error = exc
            if attempt < CFG.request_retries - 1:
                time.sleep(1.5 * (attempt + 1))

    logger.warning("HTTP failed: %s | %s", url, last_error)
    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not CFG.telegram_token or not CFG.telegram_chat_id:
        logger.error("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is missing")
        return False

    url = f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage"
    payload = {
        "chat_id": CFG.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = SESSION.post(url, json=payload, timeout=CFG.request_timeout)
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            return True
        logger.error("Telegram API error: %s", data)
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
    return False


# ============================================================
# STATE / DUPLICATE PROTECTION
# ============================================================

def load_state():
    try:
        if not os.path.exists(CFG.state_file):
            return {}
        with open(CFG.state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("State load failed: %s", exc)
        return {}


def save_state(state):
    try:
        tmp = CFG.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CFG.state_file)
        return True
    except Exception as exc:
        logger.error("State save failed: %s", exc)
        return False


def clean_old_state(state):
    cutoff = utc_now().timestamp() - CFG.dedup_hours * 3600
    cleaned = {}
    for key, value in state.items():
        try:
            if safe_float(value.get("timestamp"), 0) >= cutoff:
                cleaned[key] = value
        except Exception:
            continue
    return cleaned


def signal_key(result):
    # Deduplicate per completed 4H candle, not merely per direction.
    candle = result.get("signal_candle", "unknown")
    return f"{result.get('symbol')}:{result.get('direction')}:{candle}"


def is_duplicate(result, state):
    item = state.get(signal_key(result))
    if not item:
        return False
    return safe_float(item.get("timestamp"), 0) > utc_now().timestamp() - CFG.dedup_hours * 3600


def mark_sent(result, state):
    state[signal_key(result)] = {
        "timestamp": utc_now().timestamp(),
        "score": result.get("score", 0),
        "price": result.get("price", 0),
    }


# ============================================================
# BINANCE MARKET UNIVERSE
# ============================================================

def is_excluded_asset(asset):
    asset = str(asset).upper()
    if asset in CFG.excluded_assets:
        return True
    return any(asset.endswith(suffix) for suffix in CFG.excluded_suffixes)


def get_binance_symbols():
    """Return liquid Binance spot USDT symbols and their 24h quote volume."""
    exchange = http_get(f"{CFG.binance_base}/api/v3/exchangeInfo")
    if not exchange:
        return {}

    tickers = http_get(f"{CFG.binance_base}/api/v3/ticker/24hr")
    if not tickers:
        return {}

    volume_map = {}
    for item in tickers:
        symbol = str(item.get("symbol", "")).upper()
        if symbol.endswith("USDT"):
            volume_map[symbol] = safe_float(item.get("quoteVolume"))

    result = {}
    for item in exchange.get("symbols", []):
        symbol = str(item.get("symbol", "")).upper()
        if not symbol or item.get("status") != "TRADING":
            continue
        if item.get("quoteAsset") != "USDT":
            continue
        if item.get("isSpotTradingAllowed") is False:
            continue
        if item.get("permissions") and "SPOT" not in item.get("permissions", []):
            continue

        base = str(item.get("baseAsset", "")).upper()
        if not base or is_excluded_asset(base):
            continue

        volume = volume_map.get(symbol, 0.0)
        if volume < CFG.min_24h_usdt_volume:
            continue

        result[symbol] = {
            "base": base,
            "quote_volume": volume,
        }

    result = dict(sorted(result.items(), key=lambda x: x[1]["quote_volume"], reverse=True))
    logger.info("Binance USDT spot symbols after liquidity filter: %s", len(result))
    return result


# ============================================================
# KLINES
# ============================================================

def get_klines(symbol, interval=None, limit=None):
    interval = interval or CFG.timeframe
    limit = limit or CFG.kline_limit
    data = http_get(
        f"{CFG.binance_base}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    if not data or not isinstance(data, list):
        return None

    try:
        columns = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_base",
            "taker_quote", "ignore",
        ]
        df = pd.DataFrame(data, columns=columns)
        for col in ["open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_base", "taker_quote"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        df = df.dropna(subset=["open", "high", "low", "close", "volume"])
        return df.reset_index(drop=True)
    except Exception as exc:
        logger.warning("Kline parse failed for %s: %s", symbol, exc)
        return None


# ============================================================
# INDICATORS
# ============================================================

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # Wilder RSI: all-gain windows are 100; flat windows are 50.
    rsi = rsi.where(avg_loss.ne(0), 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi.fillna(50)


def calculate_atr(df, period=14):
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def calculate_adx(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx.fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def add_indicators(df):
    df = df.copy()

    for span in (9, 21, 50, 200):
        df[f"ema{span}"] = df["close"].ewm(span=span, adjust=False).mean()

    df["rsi"] = calculate_rsi(df["close"], CFG.rsi_period)

    ema_fast = df["close"].ewm(span=CFG.macd_fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=CFG.macd_slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=CFG.macd_signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["macd_hist_delta"] = df["macd_hist"].diff()

    df["atr"] = calculate_atr(df, CFG.atr_period)
    df["adx"], df["plus_di"], df["minus_di"] = calculate_adx(df, CFG.adx_period)

    df["volume_ma20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma20"].replace(0, np.nan)

    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    df["body_ratio"] = (df["close"] - df["open"]).abs() / candle_range
    df["close_location"] = (df["close"] - df["low"]) / candle_range

    # Binance trade-flow proxy (not a full order book).
    df["taker_buy_ratio"] = df["taker_base"] / df["volume"].replace(0, np.nan)
    df["trade_count_ma20"] = df["trades"].rolling(20).mean()
    df["trade_count_ratio"] = df["trades"] / df["trade_count_ma20"].replace(0, np.nan)
    df["quote_volume_ma20"] = df["quote_volume"].rolling(20).mean()
    df["quote_volume_ratio"] = df["quote_volume"] / df["quote_volume_ma20"].replace(0, np.nan)
    df["sell_ratio"] = 1.0 - df["taker_buy_ratio"]

    # Simple market-structure references
    df["swing_high_20"] = df["high"].rolling(20).max().shift(1)
    df["swing_low_20"] = df["low"].rolling(20).min().shift(1)

    return df


# ============================================================
# BTC REGIME
# ============================================================

def get_trend_state(df):
    if df is None or len(df) < 210:
        return "UNKNOWN"
    d = add_indicators(df)
    x = d.iloc[-1]

    close = safe_float(x.close)
    ema50 = safe_float(x.ema50)
    ema200 = safe_float(x.ema200)
    adx = safe_float(x.adx)
    plus_di = safe_float(x.plus_di)
    minus_di = safe_float(x.minus_di)

    if close > ema50 > ema200 and plus_di > minus_di and adx >= CFG.min_adx:
        return "BULLISH"
    if close < ema50 < ema200 and minus_di > plus_di and adx >= CFG.min_adx:
        return "BEARISH"
    return "NEUTRAL"


def get_btc_context():
    btc_4h_df = get_klines("BTCUSDT", "4h", CFG.kline_limit)
    btc_1d_df = get_klines("BTCUSDT", "1d", 250)

    btc_4h = get_trend_state(btc_4h_df)
    btc_1d = get_trend_state(btc_1d_df)

    if btc_4h == "BULLISH" and btc_1d != "BEARISH":
        combined = "BULLISH"
    elif btc_4h == "BEARISH" and btc_1d != "BULLISH":
        combined = "BEARISH"
    else:
        combined = "MIXED"

    return {"4h": btc_4h, "1d": btc_1d, "combined": combined}


# ============================================================
# PRICE ACTION / DIVERGENCE
# ============================================================

def _pivot_indices(series, left=3, right=3, mode="low"):
    values = series.to_numpy(dtype=float)
    pivots = []
    for i in range(left, len(values) - right):
        window = values[i-left:i+right+1]
        center = values[i]
        if mode == "low" and np.isfinite(center) and center == np.nanmin(window) and np.sum(window == center) == 1:
            pivots.append(i)
        elif mode == "high" and np.isfinite(center) and center == np.nanmax(window) and np.sum(window == center) == 1:
            pivots.append(i)
    return pivots


def detect_rsi_divergence(df, lookback=100):
    if df is None or len(df) < max(40, CFG.pivot_left + CFG.pivot_right + 10):
        return "NONE"
    d = df.tail(lookback).reset_index(drop=True)
    lows = _pivot_indices(d["low"], CFG.pivot_left, CFG.pivot_right, "low")
    highs = _pivot_indices(d["high"], CFG.pivot_left, CFG.pivot_right, "high")

    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        if i2 - i1 >= CFG.divergence_min_separation:
            p1, p2 = safe_float(d.iloc[i1].low), safe_float(d.iloc[i2].low)
            r1, r2 = safe_float(d.iloc[i1].rsi), safe_float(d.iloc[i2].rsi)
            if p2 < p1 and r2 > r1 + 1.0:
                return "BULLISH"

    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        if i2 - i1 >= CFG.divergence_min_separation:
            p1, p2 = safe_float(d.iloc[i1].high), safe_float(d.iloc[i2].high)
            r1, r2 = safe_float(d.iloc[i1].rsi), safe_float(d.iloc[i2].rsi)
            if p2 > p1 and r2 < r1 - 1.0:
                return "BEARISH"
    return "NONE"


def candle_confirmation(last, direction):
    body = safe_float(last.body_ratio)
    location = safe_float(last.close_location)
    if direction == "LONG":
        return safe_float(last.close) > safe_float(last.open) and body >= 0.45 and location >= 0.65
    return safe_float(last.close) < safe_float(last.open) and body >= 0.45 and location <= 0.35


# ============================================================
# 1H CONFIRMATION
# ============================================================

def confirm_1h_entry(symbol, direction):
    """Use 1H only as entry confirmation; 4H remains the primary signal timeframe."""
    df = get_klines(symbol, CFG.confirmation_timeframe, CFG.confirmation_kline_limit)
    if df is None or len(df) < 205:
        return False, "داده 1H کافی نیست", {}

    # Remove the still-forming candle BEFORE indicator calculation.
    if len(df) >= 2 and df.iloc[-1]["close_time"] > pd.Timestamp.now(tz="UTC"):
        df = df.iloc[:-1].copy()
    df = add_indicators(df)
    if len(df) < 205:
        return False, "کندل کامل 1H کافی نیست", {}

    x = df.iloc[-1]
    price = safe_float(x.close)
    ema9 = safe_float(x.ema9)
    ema21 = safe_float(x.ema21)
    rsi = safe_float(x.rsi)
    macd = safe_float(x.macd)
    macd_signal = safe_float(x.macd_signal)
    hist = safe_float(x.macd_hist)
    adx = safe_float(x.adx)
    plus_di = safe_float(x.plus_di)
    minus_di = safe_float(x.minus_di)
    volume_ratio = safe_float(x.volume_ratio)

    if price <= 0:
        return False, "قیمت 1H نامعتبر است", {}

    if volume_ratio < CFG.confirmation_min_volume_ratio:
        return False, f"حجم 1H پایین است: {volume_ratio:.2f}x", {}

    if direction == "LONG":
        ok = (
            price > ema21 and ema9 > ema21
            and 48 <= rsi <= 68
            and macd > macd_signal and hist > 0
            and plus_di > minus_di and adx >= CFG.confirmation_min_adx
        )
    else:
        ok = (
            price < ema21 and ema9 < ema21
            and 32 <= rsi <= 52
            and macd < macd_signal and hist < 0
            and minus_di > plus_di and adx >= CFG.confirmation_min_adx
        )

    details = {
        "rsi": rsi, "adx": adx, "volume_ratio": volume_ratio,
        "macd_hist": hist, "ema9": ema9, "ema21": ema21,
    }
    if not ok:
        return False, "تأیید 1H کامل نیست", details
    return True, "تأیید 1H هم‌جهت است", details


# ============================================================
# ANALYSIS
# ============================================================

def analyze_coin(symbol, btc_context, do_confirmation=True):
    result = {
        "symbol": symbol,
        "status": "UNKNOWN",
        "direction": None,
        "score": 0,
        "strength": "NONE",
        "price": 0,
        "rsi": 0,
        "adx": 0,
        "volume_ratio": 0,
        "taker_buy_ratio": 0.5,
        "trade_count_ratio": 1.0,
        "quote_volume_ratio": 0,
        "tape_pressure": "NEUTRAL",
        "atr_percent": 0,
        "macd_hist": 0,
        "confirmation_1h": {},
        "reasons": [],
        "warnings": [],
    }

    try:
        df = get_klines(symbol, CFG.timeframe, CFG.kline_limit)
        if df is None or len(df) < 220:
            result.update(status="NOT_ENOUGH_DATA", reason="کندل کافی نیست")
            return result

        # Remove the still-forming candle BEFORE indicator calculation.
        if len(df) >= 2 and df.iloc[-1]["close_time"] > pd.Timestamp.now(tz="UTC"):
            df = df.iloc[:-1].copy()
        df = add_indicators(df)

        if len(df) < 210:
            result.update(status="NOT_ENOUGH_DATA", reason="کندل کامل کافی نیست")
            return result

        x = df.iloc[-1]
        prev = df.iloc[-2]

        price = safe_float(x.close)
        ema9, ema21, ema50, ema200 = map(safe_float, [x.ema9, x.ema21, x.ema50, x.ema200])
        rsi = safe_float(x.rsi)
        adx = safe_float(x.adx)
        plus_di = safe_float(x.plus_di)
        minus_di = safe_float(x.minus_di)
        volume_ratio = safe_float(x.volume_ratio)
        taker_buy_ratio = safe_float(x.taker_buy_ratio, 0.5)
        trade_count_ratio = safe_float(x.trade_count_ratio, 1.0)
        quote_volume_ratio = safe_float(x.quote_volume_ratio, volume_ratio)
        close_location = safe_float(x.close_location, 0.5)
        body_ratio = safe_float(x.body_ratio, 0.0)
        atr = safe_float(x.atr)
        atr_percent = atr / price * 100 if price > 0 else 0
        macd = safe_float(x.macd)
        macd_signal = safe_float(x.macd_signal)
        macd_hist = safe_float(x.macd_hist)
        prev_hist = safe_float(prev.macd_hist)

        result.update(
            price=price,
            signal_candle=str(x.open_time),
            rsi=rsi,
            adx=adx,
            volume_ratio=volume_ratio,
            taker_buy_ratio=taker_buy_ratio,
            trade_count_ratio=trade_count_ratio,
            quote_volume_ratio=quote_volume_ratio,
            atr_percent=atr_percent,
            macd_hist=macd_hist,
        )

        if price <= 0 or atr <= 0:
            result.update(status="INVALID_DATA", reason="داده نامعتبر")
            return result

        # Direction requires a complete 4H trend stack.
        bullish_structure = price > ema9 > ema21 > ema50 > ema200
        bearish_structure = price < ema9 < ema21 < ema50 < ema200
        if not bullish_structure and not bearish_structure:
            result.update(status="NO_TREND_STRUCTURE", reason="ساختار روند 4H کامل نیست")
            return result

        direction = "LONG" if bullish_structure else "SHORT"
        result["direction"] = direction

        ema21_distance = abs(price - ema21) / ema21 * 100
        if ema21_distance > CFG.max_distance_from_ema21:
            result.update(status="EMA21_DISTANCE", reason=f"فاصله از EMA21 زیاد است: {ema21_distance:.2f}%")
            return result

        # Hard volume gate: a weak-volume candle cannot become a signal.
        if volume_ratio < CFG.min_volume_ratio:
            result.update(status="LOW_VOLUME", reason=f"حجم پایین است: {volume_ratio:.2f}x")
            return result

        # RSI should support continuation, not an exhausted move.
        if direction == "LONG" and not 45 <= rsi <= 65:
            result.update(status="RSI_FILTER", reason=f"RSI نامناسب برای LONG: {rsi:.1f}")
            return result
        if direction == "SHORT" and not 35 <= rsi <= 55:
            result.update(status="RSI_FILTER", reason=f"RSI نامناسب برای SHORT: {rsi:.1f}")
            return result

        # MACD is now mandatory confirmation, not just optional score.
        if direction == "LONG":
            macd_ok = macd > macd_signal and macd_hist > 0
            macd_improving = macd_hist >= prev_hist
        else:
            macd_ok = macd < macd_signal and macd_hist < 0
            macd_improving = macd_hist <= prev_hist

        if not macd_ok:
            result.update(status="MACD_NOT_CONFIRMED", reason="MACD هم‌جهت با روند نیست")
            return result

        # ADX + DI confirms that the trend has directional pressure.
        if adx < CFG.min_adx:
            result.update(status="WEAK_ADX", reason=f"ADX پایین است: {adx:.1f}")
            return result
        di_ok = plus_di > minus_di if direction == "LONG" else minus_di > plus_di
        if not di_ok:
            result.update(status="DI_NOT_CONFIRMED", reason="+DI/-DI تأییدکننده جهت نیست")
            return result

        # BTC is a market regime filter. Daily opposition blocks the signal.
        btc_daily = btc_context.get("1d", "UNKNOWN")
        btc_4h = btc_context.get("4h", "UNKNOWN")
        if direction == "LONG" and btc_daily == "BEARISH":
            result.update(status="BTC_DAILY_AGAINST", reason="روند Daily بیت‌کوین مخالف LONG است")
            return result
        if direction == "SHORT" and btc_daily == "BULLISH":
            result.update(status="BTC_DAILY_AGAINST", reason="روند Daily بیت‌کوین مخالف SHORT است")
            return result

        # 1H is a confirmation layer, not the primary trend timeframe.
        # In the first scan pass it is skipped to reduce Binance API load;
        # only the best 4H candidates receive a 1H request in the second pass.
        if do_confirmation:
            confirm_ok, confirm_reason, confirm_data = confirm_1h_entry(symbol, direction)
            if not confirm_ok:
                result.update(status="1H_NOT_CONFIRMED", reason=confirm_reason)
                result["confirmation_1h"] = confirm_data
                return result
            result["confirmation_1h"] = confirm_data
        else:
            result["confirmation_1h"] = {"deferred": True}

        divergence = detect_rsi_divergence(df)
        if (direction == "LONG" and divergence == "BEARISH") or (direction == "SHORT" and divergence == "BULLISH"):
            result.update(status="OPPOSITE_DIVERGENCE", reason="واگرایی RSI مخالف جهت سیگنال است")
            return result

        # Binance tape proxy: taker flow + trade expansion + candle location.
        if taker_buy_ratio >= CFG.tape_buy_ratio_long and trade_count_ratio >= CFG.min_trade_count_ratio and close_location >= 0.60:
            tape_pressure = "BUY"
        elif taker_buy_ratio <= CFG.tape_buy_ratio_short and trade_count_ratio >= CFG.min_trade_count_ratio and close_location <= 0.40:
            tape_pressure = "SELL"
        else:
            tape_pressure = "NEUTRAL"
        result["tape_pressure"] = tape_pressure

        # Score only after all hard gates pass.
        score = 0
        reasons = []
        warnings = []

        # Normalized score: exactly 100 possible points.
        # Trend 20 | RSI 10 | MACD 15 | ADX/DI 15 | Volume 10 | Tape 15 | BTC 5 | PA 5 | Div 5
        score += 20
        reasons.append("ساختار EMA 9/21/50/200 کاملاً هم‌جهت")

        if direction == "LONG":
            if 50 <= rsi <= 60:
                score += 10
            elif 45 <= rsi <= 65:
                score += 7
        else:
            if 40 <= rsi <= 50:
                score += 10
            elif 35 <= rsi <= 55:
                score += 7
        reasons.append("RSI در محدوده مناسب ادامه روند")

        score += 10
        if macd_improving:
            score += 5
            reasons.append("MACD و Histogram هم‌جهت و در حال تقویت")
        else:
            reasons.append("MACD هم‌جهت؛ شتاب Histogram متوسط")

        if adx >= CFG.strong_adx:
            score += 15
        else:
            score += 10
        reasons.append(f"ADX/DI تأییدکننده روند: {adx:.1f}")

        score += 10 if volume_ratio >= CFG.strong_volume_ratio else 7
        reasons.append(f"حجم: {volume_ratio:.2f}x")

        tape_aligned = (direction == "LONG" and tape_pressure == "BUY") or (direction == "SHORT" and tape_pressure == "SELL")
        if tape_aligned:
            score += 15 if ((direction == "LONG" and taker_buy_ratio >= CFG.tape_buy_ratio_strong_long) or (direction == "SHORT" and taker_buy_ratio <= CFG.tape_buy_ratio_strong_short)) else 11
            reasons.append(f"فشار معاملات هم‌جهت: {taker_buy_ratio*100:.1f}% Taker Buy")
        elif tape_pressure == "NEUTRAL":
            score += 5
            warnings.append("فشار معاملات خنثی است")
        else:
            warnings.append("فشار معاملات خلاف جهت سیگنال است")

        if (direction == "LONG" and btc_4h == "BULLISH") or (direction == "SHORT" and btc_4h == "BEARISH"):
            score += 5
            reasons.append("روند 4H بیت‌کوین هم‌جهت")
        else:
            warnings.append("روند 4H بیت‌کوین کاملاً هم‌جهت نیست")

        if candle_confirmation(x, direction):
            score += 5
            reasons.append("Price Action کندل تأییدکننده")
        else:
            warnings.append("Price Action کندل متوسط است")

        if (direction == "LONG" and divergence == "BULLISH") or (direction == "SHORT" and divergence == "BEARISH"):
            score += 5
            reasons.append("واگرایی RSI هم‌جهت")

        # Prevent overextended entries even when score is high.
        if atr_percent < 0.35:
            warnings.append("ATR بسیار پایین؛ حرکت ممکن است کم‌دامنه باشد")
        elif atr_percent > 5.5:
            warnings.append("ATR بالا؛ نوسان و ریسک بیشتر است")

        score = int(clamp(score, 0, 100))

        if score < CFG.min_score:
            result.update(status="LOW_SCORE", score=score, reason=f"امتیاز کافی نیست: {score}")
            return result

        stop_loss = price - atr * CFG.sl_atr_multiplier if direction == "LONG" else price + atr * CFG.sl_atr_multiplier
        sl_percent = abs(price - stop_loss) / price * 100
        if stop_loss <= 0 or sl_percent > CFG.max_sl_percent:
            result.update(status="SL_FILTER", score=score, reason=f"حد ضرر نامناسب: {sl_percent:.2f}%")
            return result

        risk_distance = abs(price - stop_loss)
        if direction == "LONG":
            tp1 = price + risk_distance * CFG.tp1_rr
            tp2 = price + risk_distance * CFG.tp2_rr
        else:
            tp1 = price - risk_distance * CFG.tp1_rr
            tp2 = price - risk_distance * CFG.tp2_rr

        risk_amount = CFG.account_size_usdt * CFG.risk_percent / 100
        raw_quantity = risk_amount / risk_distance
        max_quantity = CFG.max_position_usdt / price
        position_size = min(raw_quantity, max_quantity)
        position_usdt = position_size * price
        actual_risk_amount = position_size * risk_distance

        result.update({
            "status": "SIGNAL",
            "score": score,
            "strength": "STRONG" if score >= CFG.strong_score else "NORMAL",
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "sl_percent": sl_percent,
            "risk_amount": risk_amount,
            "actual_risk_amount": actual_risk_amount,
            "position_size": position_size,
            "position_usdt": position_usdt,
            "divergence": divergence,
            "reasons": reasons,
            "warnings": warnings,
            "btc_4h": btc_4h,
            "btc_1d": btc_daily,
            "signal_candle": str(x["open_time"]),
        })
        return result

    except Exception as exc:
        logger.exception("Analysis error for %s", symbol)
        result.update(status="ERROR", reason=f"خطای تحلیل: {str(exc)[:120]}")
        return result


# ============================================================
# TELEGRAM FORMATTING
# ============================================================

def format_signal(r):
    direction_text = "BUY / LONG" if r["direction"] == "LONG" else "SELL / SHORT"
    icon = "🟢" if r["direction"] == "LONG" else "🔴"

    reasons = r.get("reasons", [])[:6]
    warnings = r.get("warnings", [])[:4]

    lines = [
        f"{icon} <b>CRYPTO SIGNAL</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🪙 <b>{html.escape(r['symbol'].replace('USDT', ''))}</b>",
        f"📌 Direction: <b>{direction_text}</b>",
        f"⭐ Score: <b>{r['score']}/100</b>",
        f"✅ Strength: <b>{r['strength']}</b>",
        "",
        f"💰 Entry: <b>{fmt_price(r['price'])}</b>",
        f"🛡 SL: <b>{fmt_price(r['stop_loss'])}</b>",
        f"🎯 TP1: <b>{fmt_price(r['tp1'])}</b>  RR={CFG.tp1_rr:.2f}",
        f"🎯 TP2: <b>{fmt_price(r['tp2'])}</b>  RR={CFG.tp2_rr:.2f}",
        f"📏 SL Distance: <b>{r['sl_percent']:.2f}%</b>",
        "",
        f"📊 4H RSI: <b>{r['rsi']:.1f}</b>",
        f"📈 ADX: <b>{r['adx']:.1f}</b>",
        f"📦 Volume: <b>{r['volume_ratio']:.2f}x</b>",
        f"📡 Tape: <b>{r.get('tape_pressure', 'NEUTRAL')}</b> | Taker Buy {r.get('taker_buy_ratio', 0.5)*100:.1f}%",
        f"🔁 Trades: <b>{r.get('trade_count_ratio', 1.0):.2f}x</b>",
        f"⚡ ATR: <b>{r['atr_percent']:.2f}%</b>",
        f"₿ BTC 4H: <b>{r.get('btc_4h', 'UNKNOWN')}</b>",
        f"₿ BTC Daily: <b>{r.get('btc_1d', 'UNKNOWN')}</b>",
        f"⏱ 1H Confirmation: <b>OK</b>",
        "",
        "🧠 <b>دلایل</b>",
    ]

    for reason in reasons:
        lines.append(f"• {html.escape(reason)}")

    if warnings:
        lines += ["", "⚠️ <b>هشدارها</b>"]
        for warning in warnings:
            lines.append(f"• {html.escape(warning)}")

    lines += [
        "",
        f"💵 Target Risk: <b>${r['risk_amount']:.2f}</b>",
        f"💵 Actual Risk: <b>${r.get('actual_risk_amount', r['risk_amount']):.2f}</b>",
        f"📐 Position Value: <b>${r['position_usdt']:.2f}</b>",
        "",
        "ℹ️ تحلیل خودکار است؛ معامله به‌صورت خودکار انجام نمی‌شود.",
    ]
    return "\n".join(lines)


STATUS_NAMES = {
    "NOT_ENOUGH_DATA": "داده/کندل ناکافی",
    "NO_TREND_STRUCTURE": "ساختار روند کامل نیست",
    "EMA21_DISTANCE": "فاصله از EMA21 زیاد",
    "LOW_VOLUME": "حجم پایین",
    "1H_NOT_CONFIRMED": "تأیید 1H ناقص",
    "RSI_FILTER": "RSI نامناسب",
    "MACD_NOT_CONFIRMED": "MACD تأیید نکرد",
    "WEAK_ADX": "ADX ضعیف",
    "DI_NOT_CONFIRMED": "DI تأیید نکرد",
    "BTC_DAILY_AGAINST": "روند Daily BTC مخالف",
    "OPPOSITE_DIVERGENCE": "واگرایی مخالف",
    "LOW_SCORE": "امتیاز ناکافی",
    "SL_FILTER": "حد ضرر نامناسب",
    "INVALID_DATA": "داده نامعتبر",
    "ERROR": "خطای تحلیل",
}


def format_scan_report(total, analyzed, candidates, new_signals, btc, results):
    from collections import Counter
    counter = Counter(r.get("status", "UNKNOWN") for r in results if r.get("status") != "SIGNAL")

    lines = [
        "📊 <b>گزارش اسکن Binance</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🪙 ارزهای قابل اسکن: <b>{total}</b>",
        f"🔎 تحلیل‌شده: <b>{analyzed}</b>",
        f"🎯 کاندیدای نهایی: <b>{candidates}</b>",
        f"📨 سیگنال جدید: <b>{new_signals}</b>",
        "",
        "₿ <b>BTC Regime</b>",
        f"4H: <b>{btc.get('4h')}</b>",
        f"Daily: <b>{btc.get('1d')}</b>",
        f"Combined: <b>{btc.get('combined')}</b>",
    ]

    if counter:
        lines += ["", "❌ <b>دلایل اصلی رد شدن</b>"]
        for status, count in counter.most_common(8):
            lines.append(f"• {html.escape(STATUS_NAMES.get(status, status))}: <b>{count}</b>")

    return "\n".join(lines)


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():
    if not CFG.telegram_token or not CFG.telegram_chat_id:
        logger.warning("Telegram credentials are not configured")

    state = clean_old_state(load_state())
    markets = get_binance_symbols()
    if not markets:
        logger.error("No Binance USDT spot symbols available")
        send_telegram("⚠️ <b>Crypto Bot Error</b>\n\nلیست بازار Binance دریافت نشد.")
        return

    btc_context = get_btc_context()
    symbols = list(markets.keys())
    logger.info("Scanning %s Binance symbols | BTC=%s", len(symbols), btc_context)

    # Pass 1: scan all 4H markets only. This avoids one 1H request per symbol.
    results = []
    with ThreadPoolExecutor(max_workers=CFG.max_workers) as executor:
        futures = {executor.submit(analyze_coin, symbol, btc_context, False): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as exc:
                logger.error("Worker error %s: %s", symbol, exc)
                results.append({"symbol": symbol, "status": "ERROR", "reason": str(exc)})

    # Pass 2: only the strongest 4H candidates get expensive 1H confirmation.
    pre_candidates = [r for r in results if r.get("status") == "SIGNAL"]
    pre_candidates.sort(key=lambda r: (r.get("score", 0), r.get("volume_ratio", 0), r.get("taker_buy_ratio", 0.5)), reverse=True)
    shortlist = pre_candidates[:CFG.max_1h_confirmation_candidates]

    final_results = []
    for r in shortlist:
        checked = analyze_coin(r["symbol"], btc_context, True)
        final_results.append(checked)

    # Replace the provisional entries with their 1H-confirmed results.
    final_map = {r.get("symbol"): r for r in final_results}
    results = [final_map.get(r.get("symbol"), r) if r.get("symbol") in final_map else r for r in results]

    candidates = [r for r in final_results if r.get("status") == "SIGNAL"]
    candidates.sort(key=lambda r: (r.get("score", 0), r.get("volume_ratio", 0)), reverse=True)

    new_signals = 0
    for result in candidates:
        if new_signals >= CFG.max_signals_per_run:
            break
        if is_duplicate(result, state):
            continue
        if send_telegram(format_signal(result)):
            mark_sent(result, state)
            new_signals += 1

    save_state(state)

    if CFG.send_scan_report:
        report = format_scan_report(
            total=len(symbols),
            analyzed=len(results),
            candidates=len(candidates),
            new_signals=new_signals,
            btc=btc_context,
            results=results,
        )
        send_telegram(report)

    logger.info("Finished | symbols=%s analyzed=%s candidates=%s new=%s", len(symbols), len(results), len(candidates), new_signals)


if __name__ == "__main__":
    run_scan()
