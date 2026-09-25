# =========================================================
# Crypto Signal Bot - INSTITUTIONAL GRADE v5.4 FINAL
# =========================================================

import os, json, time, traceback
import requests, pandas as pd, numpy as np
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- SETTINGS ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TIMEFRAME_MAIN = "4h"
TIMEFRAME_SUB = "1h"
TIMEFRAME_MACRO = "1d"
KLINE_LIMIT = 500
KLINE_LIMIT_1H = 300

MIN_SCORE = 60
STRONG_SCORE = 78
MIN_VOLUME_RATIO = 1.30
MIN_24H_USDT_VOLUME = 10_000_000

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.50
TP1_RR = 1.60
TP2_RR = 2.80

DIVERGENCE_LOOKBACK = 30
DIVERGENCE_MIN_GAP = 3
DIVERGENCE_MAX_GAP = 20

OB_LOOKBACK = 50
FVG_MIN_SIZE_ATR = 0.3

VOL_REGIME_ATR_WINDOW = 50
VOL_REGIME_THRESHOLD = 1.15

BB_PERIOD = 20
BB_STD = 2.0
BB_SQUEEZE_THRESHOLD = 0.5

MAX_CONCURRENT_SIGNALS = 8
MAX_DAILY_SIGNALS = 15

REQUIRE_DAILY_ALIGNMENT = True

STATE_FILE = "signals_state.json"
HISTORY_FILE = "signals_history.json"
COOLDOWN_FILE = "cooldown_state.json"

BINANCE_SPOT_BASE = "https://data-api.binance.vision"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

FUTURES_CACHE = {}
FUTURES_CACHE_TTL = 3600
PARALLEL_WORKERS = 5

# ---------- UTILS ----------
def log(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


def http_get(url, params=None, retries=3, timeout=25):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep((2 ** attempt) * 3)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    return None


def send_telegram(text, retries=3):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    if len(text) > 4000:
        text = text[:3990] + "\n...(کوتاه شد)"
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(retries):
        try:
            res = requests.post(url, json=payload, timeout=(10, 45))
            if res.status_code == 200:
                return True
            if res.status_code == 429:
                time.sleep(int(res.headers.get("Retry-After", 5)))
                continue
            return False
        except Exception:
            time.sleep(3)
    return False


def format_num(n):
    try:
        n = float(n)
        if n >= 1_000_000_000:
            return f"{n/1_000_000_000:.2f}B"
        if n >= 1_000_000:
            return f"{n/1_000_000:.2f}M"
        if n >= 1_000:
            return f"{n/1_000:.2f}K"
        return f"{n:.2f}"
    except Exception:
        return str(n)


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("[SAVE ERROR] " + path + ": " + str(e))

# ---------- DATA ----------
def get_fear_greed_index():
    data = http_get("https://api.alternative.me/fng/?limit=1")
    try:
        if data and "data" in data and len(data["data"]) > 0:
            return int(data["data"][0]["value"]), data["data"][0]["value_classification"]
    except Exception:
        pass
    return 50, "Neutral"


def get_futures_metrics(symbol):
    funding_rate, open_interest, oi_change = 0.0, 0.0, 0.0
    try:
        pi = http_get(BINANCE_FUTURES_BASE + "/fapi/v1/premiumIndex", params={"symbol": symbol})
        if pi and isinstance(pi, dict):
            funding_rate = float(pi.get("lastFundingRate", 0) or 0)

        oi_data = http_get(BINANCE_FUTURES_BASE + "/fapi/v1/openInterest", params={"symbol": symbol})
        if oi_data and isinstance(oi_data, dict):
            open_interest = float(oi_data.get("openInterest", 0) or 0)

        oi_hist = http_get(
            "https://www.binance.com/futures/data/openInterestHist",
            params={"symbol": symbol, "period": "4h", "limit": 25}
        )
        if oi_hist and isinstance(oi_hist, list) and len(oi_hist) >= 2:
            first = float(oi_hist[0].get("sumOpenInterest", 0) or 0)
            last = float(oi_hist[-1].get("sumOpenInterest", 0) or 0)
            if first > 0:
                oi_change = ((last - first) / first) * 100
    except Exception as e:
        log(f"[FUTURES ERROR] {symbol}: {str(e)}")
        
    return funding_rate, open_interest, oi_change


def get_futures_cached(symbol):
    now = time.time()
    if symbol in FUTURES_CACHE:
        cached_time, data = FUTURES_CACHE[symbol]
        if now - cached_time < FUTURES_CACHE_TTL:
            return data
    data = get_futures_metrics(symbol)
    FUTURES_CACHE[symbol] = (now, data)
    return data

# ---------- MARKET DISCOVERY ----------
def get_scan_coins():
    all_coins = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "TON", "ADA", "DOGE", "AVAX", "LINK",
        "DOT", "LTC", "BCH", "ETC", "XLM", "UNI", "FIL", "TRX", "ATOM", "NEAR",
        "AAVE", "SUI", "APT", "ARB", "OP", "SEI", "INJ", "TIA", "STX", "ALGO",
        "PEPE", "WIF", "BONK", "FLOKI", "SHIB", "MEME", "NOT", "ORDI", "BOME",
        "POL", "RENDER", "ICP", "KAS", "IMX", "GRT", "HBAR", "ENA",
        "PENDLE", "JUP", "PYTH", "W", "MANTA", "ALT", "STRK", "AXL",
        "AEVO", "REZ", "BB", "IO", "ZK", "LISTA", "DOGS", "CATI",
        "HMSTR", "EIGEN", "SCR", "PNUT", "ACT", "GOAT", "CHZ", "SAND", "MANA",
        "GALA", "ENJ", "AXS", "THETA", "FTM", "SNX", "CRV", "MKR", "COMP",
        "1INCH", "SUSHI", "BAL", "ZRX", "LDO", "RPL", "SSV", "FXS", "DYDX",
        "GMX", "PERP", "RLC", "AR", "STORJ", "SC", "HOT",
        "RVN", "ZIL", "IOST", "ONT", "ICX", "ZEC", "DASH", "KSM", "ZEN", "QTUM",
        "NEXO", "BAT", "SKL", "MINA", "FLOW", "MASK", "AGLD", "API3", "SUPER",
        "BICO", "GLMR", "MOVR", "ASTR", "TLM", "DAR", "ALICE", "YGG",
        "GHST", "RARE", "STG", "LPT", "HIGH", "CVX", "MDT"
    ]
    unique_coins = sorted(set(all_coins))

    tickers = http_get(BINANCE_SPOT_BASE + "/api/v3/ticker/24hr")
    valid_volumes = {}
    if tickers and isinstance(tickers, list):
        for t in tickers:
            sym = t.get("symbol")
            if sym and sym.endswith("USDT"):
                try:
                    valid_volumes[sym] = float(t.get("quoteVolume", 0) or 0)
                except (ValueError, TypeError):
                    pass

    info = http_get(BINANCE_SPOT_BASE + "/api/v3/exchangeInfo")
    if not info or "symbols" not in info:
        return []

    binance_symbols = set()
    for item in info.get("symbols", []):
        if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT":
            binance_symbols.add(item["symbol"])

    result = []
    for coin in unique_coins:
        symbol = coin + "USDT"
        if symbol in binance_symbols and valid_volumes.get(symbol, 0) >= MIN_24H_USDT_VOLUME:
            result.append({"coin": coin, "symbol": symbol})
    return result

# ---------- KLINES ----------
def get_klines(symbol, interval, limit=None):
    if limit is None:
        limit = KLINE_LIMIT
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    data = http_get(BINANCE_SPOT_BASE + "/api/v3/klines", params=params)
    if not data or len(data) < 210:
        return None
    columns = ["open_time", "open", "high", "low", "close", "volume",
               "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"]
    df = pd.DataFrame(data, columns=columns)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df.iloc[:-1].reset_index(drop=True)


def add_indicators(df):
    df = df.copy()
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    df["atr_avg50"] = df["atr"].rolling(VOL_REGIME_ATR_WINDOW).mean()

    df["volume_avg20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg20"].replace(0, np.nan)

    df["obv"] = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    df["obv_ema"] = df["obv"].ewm(span=20, adjust=False).mean()

    df["bb_mid"] = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"] = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_width_avg20"] = df["bb_width"].rolling(20).mean()

    typical = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap_cum"] = (typical * df["volume"]).cumsum() / df["volume"].cumsum()

    return df

# ---------- MULTI-TF ----------
def get_btc_macro_trend():
    df_btc = get_klines("BTCUSDT", TIMEFRAME_MACRO)
    if df_btc is None or len(df_btc) < 200:
        return "BULLISH"
    ema200 = df_btc["close"].ewm(span=200, adjust=False).mean().iloc[-1]
    if not np.isfinite(ema200):
        return "BULLISH"
    return "BULLISH" if df_btc.iloc[-1]["close"] > ema200 else "BEARISH"


def check_daily_alignment(symbol, direction):
    df = get_klines(symbol, TIMEFRAME_MACRO)
    if df is None or len(df) < 50:
        return True
    df = add_indicators(df)
    last = df.iloc[-1]
    if not np.isfinite(last["ema50"]):
        return True
    if direction == "BUY":
        return last["close"] > last["ema50"] and last["ema9"] > last["ema21"]
    else:
        return last["close"] < last["ema50"] and last["ema9"] < last["ema21"]


def check_1h_confirmation(symbol, direction):
    df = get_klines(symbol, TIMEFRAME_SUB, limit=KLINE_LIMIT_1H)
    if df is None or len(df) < 50:
        return False
    df = add_indicators(df)
    last = df.iloc[-1]
    if not np.isfinite(last["ema21"]) or not np.isfinite(last["rsi"]):
        return False
    if direction == "BUY":
        return last["ema9"] > last["ema21"] and 40 <= last["rsi"] <= 75
    else:
        return last["ema9"] < last["ema21"] and 25 <= last["rsi"] <= 60

# ---------- DIVERGENCE ----------
def find_local_minima(series, order=2):
    idxs = []
    vals = series.values
    for i in range(order, len(vals) - order):
        window = vals[i - order:i + order + 1]
        if vals[i] == window.min() and np.isfinite(vals[i]):
            idxs.append(i)
    return idxs


def find_local_maxima(series, order=2):
    idxs = []
    vals = series.values
    for i in range(order, len(vals) - order):
        window = vals[i - order:i + order + 1]
        if vals[i] == window.max() and np.isfinite(vals[i]):
            idxs.append(i)
    return idxs


def has_bullish_divergence(df):
    try:
        recent = df.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
        if len(recent) < 15:
            return False
        lows_idx = find_local_minima(recent["low"], order=2)
        if len(lows_idx) < 2:
            return False
        i1, i2 = lows_idx[-2], lows_idx[-1]
        gap = i2 - i1
        if gap < DIVERGENCE_MIN_GAP or gap > DIVERGENCE_MAX_GAP:
            return False
        p1 = recent.loc[i1, "low"]
        p2 = recent.loc[i2, "low"]
        r1 = recent.loc[i1, "rsi"]
        r2 = recent.loc[i2, "rsi"]
        if not all(np.isfinite(x) for x in [p1, p2, r1, r2]):
            return False
        return p2 < p1 and r2 > r1 + 2
    except Exception:
        return False


def has_bearish_divergence(df):
    try:
        recent = df.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
        if len(recent) < 15:
            return False
        highs_idx = find_local_maxima(recent["high"], order=2)
        if len(highs_idx) < 2:
            return False
        i1, i2 = highs_idx[-2], highs_idx[-1]
        gap = i2 - i1
        if gap < DIVERGENCE_MIN_GAP or gap > DIVERGENCE_MAX_GAP:
            return False
        p1 = recent.loc[i1, "high"]
        p2 = recent.loc[i2, "high"]
        r1 = recent.loc[i1, "rsi"]
        r2 = recent.loc[i2, "rsi"]
        if not all(np.isfinite(x) for x in [p1, p2, r1, r2]):
            return False
        return p2 > p1 and r2 < r1 - 2
    except Exception:
        return False

# ---------- SMC ----------
def find_swing_highs(df, lookback=3):
    highs = []
    for i in range(lookback, len(df) - lookback):
        window = df["high"].iloc[i-lookback:i+lookback+1]
        if df["high"].iloc[i] == window.max():
            highs.append({"idx": i, "price": float(df["high"].iloc[i])})
    return highs


def find_swing_lows(df, lookback=3):
    lows = []
    for i in range(lookback, len(df) - lookback):
        window = df["low"].iloc[i-lookback:i+lookback+1]
        if df["low"].iloc[i] == window.min():
            lows.append({"idx": i, "price": float(df["low"].iloc[i])})
    return lows


def find_order_blocks(df, direction):
    obs = []
    try:
        recent = df.tail(OB_LOOKBACK).reset_index(drop=True)
        atr_last = float(df["atr"].iloc[-1])
        if not np.isfinite(atr_last) or atr_last <= 0:
            return obs

        for i in range(len(recent) - 3, 5, -1):
            candle = recent.iloc[i]
            nxt = recent.iloc[i+1:i+4]

            if direction == "BUY":
                is_bearish = candle["close"] < candle["open"]
                move_up = (nxt["close"].max() - candle["low"]) > atr_last * 1.5
                if is_bearish and move_up:
                    obs.append({
                        "top": float(candle["open"]),
                        "bottom": float(candle["low"]),
                        "idx": i,
                    })
                    if len(obs) >= 2:
                        break
            else:
                is_bullish = candle["close"] > candle["open"]
                move_down = (candle["high"] - nxt["close"].min()) > atr_last * 1.5
                if is_bullish and move_down:
                    obs.append({
                        "top": float(candle["high"]),
                        "bottom": float(candle["close"]),
                        "idx": i,
                    })
                    if len(obs) >= 2:
                        break
    except Exception:
        pass
    return obs


def find_fair_value_gaps(df, direction):
    fvgs = []
    try:
        recent = df.tail(OB_LOOKBACK).reset_index(drop=True)
        atr_last = float(df["atr"].iloc[-1])
        if not np.isfinite(atr_last) or atr_last <= 0:
            return fvgs

        for i in range(2, len(recent) - 1):
            prev = recent.iloc[i-1]
            curr = recent.iloc[i]

            if direction == "BUY":
                if curr["low"] > prev["high"]:
                    gap_size = curr["low"] - prev["high"]
                    if gap_size > atr_last * FVG_MIN_SIZE_ATR:
                        fvgs.append({
                            "top": float(curr["low"]),
                            "bottom": float(prev["high"]),
                            "idx": i,
                        })
            else:
                if curr["high"] < prev["low"]:
                    gap_size = prev["low"] - curr["high"]
                    if gap_size > atr_last * FVG_MIN_SIZE_ATR:
                        fvgs.append({
                            "top": float(prev["low"]),
                            "bottom": float(curr["high"]),
                            "idx": i,
                        })
        if len(fvgs) > 3:
            fvgs = fvgs[-3:]
    except Exception:
        pass
    return fvgs


def is_price_in_zone(price, zone):
    return zone["bottom"] <= price <= zone["top"]

# ---------- REGIME ----------
def get_volatility_regime(df):
    try:
        last = df.iloc[-1]
        atr = float(last["atr"])
        atr_avg = float(last["atr_avg50"])
        if not np.isfinite(atr) or not np.isfinite(atr_avg) or atr_avg <= 0:
            return "UNKNOWN"
        ratio = atr / atr_avg
        if ratio >= VOL_REGIME_THRESHOLD:
            return "TRENDING"
        return "RANGING"
    except Exception:
        return "UNKNOWN"

# ---------- ANALYZE ENGINE ----------
def analyze_coin(df, symbol):
    try:
        df = add_indicators(df)
        last = df.iloc[-1]
        
        close = float(last["close"])
        atr = float(last["atr"])
        rsi = float(last["rsi"])
        volume_ratio = float(last["volume_ratio"])
        
        if not all(np.isfinite([close, atr, rsi, volume_ratio])) or atr <= 0:
            return None
            
        regime = get_volatility_regime(df)
        
        direction = None
        score = 50
        
        if last["ema9"] > last["ema21"] and last["ema21"] > last["ema50"]:
            direction = "BUY"
            score += 15
        elif last["ema9"] < last["ema21"] and last["ema21"] < last["ema50"]:
            direction = "SELL"
            score += 15
        else:
            return None
            
        if volume_ratio >= MIN_VOLUME_RATIO:
            score += 10
            
        if direction == "BUY" and 45 <= rsi <= 65:
            score += 10
        elif direction == "SELL" and 35 <= rsi <= 55:
            score += 10
            
        if direction == "BUY" and has_bullish_divergence(df):
            score += 15
        elif direction == "SELL" and has_bearish_divergence(df):
            score += 15
            
        obs = find_order_blocks(df, direction)
        fvgs = find_fair_value_gaps(df, direction)
        if any(is_price_in_zone(close, ob) for ob in obs) or any(is_price_in_zone(close, fvg) for fvg in fvgs):
            score += 10
            
        if score < MIN_SCORE:
            return None
            
        if REQUIRE_DAILY_ALIGNMENT and not check_daily_alignment(symbol, direction):
            return None
            
        if not check_1h_confirmation(symbol, direction):
            return None
            
        funding_rate, open_interest, oi_change = get_futures_cached(symbol)
        
        if open_interest <= 0:
            return None
            
        if direction == "BUY":
            sl = close - (atr * SL_ATR_MULTIPLIER)
            risk = close - sl
            tp1 = close + (risk * TP1_RR)
            tp2 = close + (risk * TP2_RR)
        else:
            sl = close + (atr * SL_ATR_MULTIPLIER)
            risk = sl - close
            tp1 = close - (risk * TP1_RR)
            tp2 = close - (risk * TP2_RR)
            
        result_dict = {
            "symbol": symbol,
            "direction": direction,
            "score": score,
  
