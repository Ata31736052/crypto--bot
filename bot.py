# =========================================================
# Crypto Signal Bot - INSTITUTIONAL GRADE v5.0 FINAL
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
COOLDOWN_AFTER_3_SL = 6

REQUIRE_DAILY_ALIGNMENT = True

ANOMALY_VOLUME_MULT = 5.0
ANOMALY_ATR_MULT = 3.0

STATE_FILE = "signals_state.json"
HISTORY_FILE = "signals_history.json"
COOLDOWN_FILE = "cooldown_state.json"

BINANCE_SPOT_BASE = "https://data-api.binance.vision"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

DAILY_REPORT_HOUR = 21
WEEKLY_REPORT_DAY = 6

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
        pi = http_get(BINANCE_FUTURES_BASE + "/fapi/v1/premiumIndex",
                      params={"symbol": symbol})
        if pi and isinstance(pi, dict):
            funding_rate = float(pi.get("lastFundingRate", 0) or 0)

        oi_data = http_get(BINANCE_FUTURES_BASE + "/fapi/v1/openInterest",
                           params={"symbol": symbol})
        if oi_data and isinstance(oi_data, dict):
            open_interest = float(oi_data.get("openInterest", 0) or 0)

        oi_hist = http_get(
            BINANCE_FUTURES_BASE + "/futures/data/openInterestHist",
            params={"symbol": symbol, "period": "4h", "limit": 25}
        )
        if oi_hist and isinstance(oi_hist, list) and len(oi_hist) >= 2:
            first = float(oi_hist[0].get("sumOpenInterest", 0) or 0)
            last = float(oi_hist[-1].get("sumOpenInterest", 0) or 0)
            if first > 0:
                oi_change = ((last - first) / first) * 100
    except Exception:
        pass
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


def detect_bos_choch(df):
    result = {"bos_bull": False, "bos_bear": False,
              "choch_bull": False, "choch_bear": False}
    try:
        highs = find_swing_highs(df, lookback=3)
        lows = find_swing_lows(df, lookback=3)
        if len(highs) < 2 or len(lows) < 2:
            return result

        last_close = float(df["close"].iloc[-1])
        last_high = highs[-1]["price"]
        prev_high = highs[-2]["price"]
        last_low = lows[-1]["price"]
        prev_low = lows[-2]["price"]

        if last_high > prev_high and last_close > last_high:
            result["bos_bull"] = True
        if last_low < prev_low and last_close < last_low:
            result["bos_bear"] = True
        if last_low > prev_low and prev_high < highs[-2]["price"] if len(highs) > 2 else False:
            result["choch_bull"] = True
        if last_high < prev_high and last_low < prev_low:
            result["choch_bear"] = True
    except Exception:
        pass
    return result


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
# ---------- REGIME & ANOMALY ----------
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


def detect_bb_squeeze(df):
    try:
        last = df.iloc[-1]
        width = float(last["bb_width"])
        avg_width = float(last["bb_width_avg20"])
        if not np.isfinite(width) or not np.isfinite(avg_width) or avg_width <= 0:
            return False
        return (width / avg_width) <= BB_SQUEEZE_THRESHOLD
    except Exception:
        return False


def detect_anomaly(df):
    try:
        last = df.iloc[-1]
        vol_ratio = float(last["volume_ratio"])
        atr = float(last["atr"])
        atr_avg = float(last["atr_avg50"])
        if not np.isfinite(vol_ratio):
            return False
        if vol_ratio >= ANOMALY_VOLUME_MULT:
            return True
        if np.isfinite(atr) and np.isfinite(atr_avg) and atr_avg > 0:
            if (atr / atr_avg) >= ANOMALY_ATR_MULT:
                return True
        return False
    except Exception:
        return False


def get_vwap_position(df):
    try:
        last = df.iloc[-1]
        vwap = float(last["vwap_cum"])
        price = float(last["close"])
        if not np.isfinite(vwap):
            return "UNKNOWN"
        if price > vwap * 1.005:
            return "ABOVE"
        if price < vwap * 0.995:
            return "BELOW"
        return "AT"
    except Exception:
        return "UNKNOWN"
# ---------- ANALYZE ENGINE ----------
def analyze_coin(df, symbol, btc_trend):
    df = add_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    try:
        price = float(last["close"])
        rsi = float(last["rsi"])
        volume_ratio = float(last["volume_ratio"])
        atr = float(last["atr"])
        ema200 = float(last["ema200"])
        ema50 = float(last["ema50"])
    except (TypeError, ValueError):
        return None

    if not all(np.isfinite(x) for x in [price, rsi, atr, ema200, ema50]):
        return None
    if atr <= 0:
        return None
    if not np.isfinite(volume_ratio):
        volume_ratio = 0.0

    is_anomaly = detect_anomaly(df)
    if is_anomaly:
        return {
            "signal": "ANOMALY",
            "symbol": symbol,
            "entry": price,
            "candle_time": last["close_time"].isoformat()
        }

    regime = get_volatility_regime(df)
    bb_squeeze = detect_bb_squeeze(df)
    smc = detect_bos_choch(df)
    vwap_pos = get_vwap_position(df)

    buy_score, sell_score = 0, 0
    reasons_buy, reasons_sell = [], []

    if price > ema200:
        buy_score += 18
        reasons_buy.append("بالای EMA200 (4H)")
    else:
        sell_score += 18
        reasons_sell.append("پایین EMA200 (4H)")

    if last["ema9"] > last["ema21"]:
        buy_score += 12
        reasons_buy.append("تقاطع صعودی EMA 9/21")
    else:
        sell_score += 12
        reasons_sell.append("تقاطع نزولی EMA 9/21")

    if price > ema50:
        buy_score += 5
    else:
        sell_score += 5

    divergence_found = False
    if rsi < 40 and has_bullish_divergence(df):
        buy_score += 22
        reasons_buy.append("واگرایی صعودی + RSI " + f"{rsi:.1f}")
        divergence_found = True
    elif rsi > 60 and has_bearish_divergence(df):
        sell_score += 22
        reasons_sell.append("واگرایی نزولی + RSI " + f"{rsi:.1f}")
        divergence_found = True

    if not divergence_found:
        if 45 <= rsi <= 68:
            buy_score += 10
            reasons_buy.append("RSI مومنتوم (" + f"{rsi:.1f}" + ")")
        elif 32 <= rsi <= 55:
            sell_score += 10
            reasons_sell.append("RSI مومنتوم (" + f"{rsi:.1f}" + ")")

    if prev["macd"] <= prev["macd_signal"] and last["macd"] > last["macd_signal"]:
        buy_score += 10
        reasons_buy.append("تقاطع صعودی MACD")
    elif prev["macd"] >= prev["macd_signal"] and last["macd"] < last["macd_signal"]:
        sell_score += 10
        reasons_sell.append("تقاطع نزولی MACD")
    elif last["macd_hist"] > 0:
        buy_score += 4
    elif last["macd_hist"] < 0:
        sell_score += 4

    if last["obv"] > last["obv_ema"]:
        buy_score += 6
        reasons_buy.append("OBV مثبت")
    else:
        sell_score += 6
        reasons_sell.append("OBV منفی")

    if volume_ratio >= MIN_VOLUME_RATIO:
        if last["close"] > last["open"]:
            buy_score += 12
            reasons_buy.append("حجم قوی (" + f"{volume_ratio:.2f}" + "x)")
        else:
            sell_score += 12
            reasons_sell.append("حجم سنگین فروش (" + f"{volume_ratio:.2f}" + "x)")

    if smc["bos_bull"]:
        buy_score += 8
        reasons_buy.append("BOS صعودی (شکست ساختار)")
    if smc["bos_bear"]:
        sell_score += 8
        reasons_sell.append("BOS نزولی")
    if smc["choch_bull"]:
        buy_score += 6
        reasons_buy.append("CHoCH صعودی (تغییر روند)")
    if smc["choch_bear"]:
        sell_score += 6
        reasons_sell.append("CHoCH نزولی")

    if regime == "TRENDING":
        if buy_score > sell_score:
            buy_score += 5
            reasons_buy.append("رژیم Trending (تایید مومنتوم)")
        elif sell_score > buy_score:
            sell_score += 5
            reasons_sell.append("رژیم Trending (تایید مومنتوم)")

    if bb_squeeze:
        if buy_score > sell_score:
            buy_score += 4
            reasons_buy.append("Squeeze بولینگر (انفجار نزدیک)")
        elif sell_score > buy_score:
            sell_score += 4
            reasons_sell.append("Squeeze بولینگر")

    if vwap_pos == "ABOVE":
        buy_score += 4
        reasons_buy.append("بالای VWAP")
    elif vwap_pos == "BELOW":
        sell_score += 4
        reasons_sell.append("پایین VWAP")

    direction, score, reasons = None, 0, []
    if buy_score >= MIN_SCORE and buy_score > sell_score:
        if btc_trend == "BEARISH" and symbol != "BTCUSDT":
            return None
        direction, score, reasons = "BUY", buy_score, reasons_buy
    elif sell_score >= MIN_SCORE and sell_score > buy_score:
        direction, score, reasons = "SELL", sell_score, reasons_sell
    else:
        return None

    if REQUIRE_DAILY_ALIGNMENT:
        if not check_daily_alignment(symbol, direction):
            return None
        score += 5
        reasons.append("تاییدیه Daily (روند کلان)")

    if not check_1h_confirmation(symbol, direction):
        return None
    score += 5
    reasons.append("تاییدیه 1H (Multi-TF)")

    ob_in_zone = False
    obs = find_order_blocks(df, direction)
    for ob in obs:
        if is_price_in_zone(price, ob):
            ob_in_zone = True
            score += 8
            reasons.append("ورود در ناحیه Order Block")
            break

    fvgs = find_fair_value_gaps(df, direction)
    fvg_in_zone = False
    for fvg in fvgs:
        if is_price_in_zone(price, fvg):
            fvg_in_zone = True
            score += 6
            reasons.append("ورود در Fair Value Gap")
            break

    funding_rate, open_interest, oi_change = get_futures_cached(symbol)
    if direction == "BUY" and funding_rate < -0.001:
        score += 4
        reasons.append("فاندینگ مساعد (" + f"{funding_rate*100:.3f}" + "%)")
    elif direction == "SELL" and funding_rate > 0.001:
        score += 4
        reasons.append("فاندینگ مساعد (" + f"{funding_rate*100:.3f}" + "%)")

    if direction == "BUY":
        atr_sl = price - (atr * SL_ATR_MULTIPLIER)
        ob_sl = atr_sl
        for ob in obs:
            if ob["bottom"] < price:
                candidate = ob["bottom"] - (atr * 0.2)
                if candidate > atr_sl and candidate < price:
                    ob_sl = candidate
                    break
        stop_loss = ob_sl if ob_sl != atr_sl else atr_sl
        risk_per_unit = price - stop_loss
        tp1 = price + (risk_per_unit * TP1_RR)
        tp2 = price + (risk_per_unit * TP2_RR)
    else:
        atr_sl = price + (atr * SL_ATR_MULTIPLIER)
        ob_sl = atr_sl
        for ob in obs:
            if ob["top"] > price:
                candidate = ob["top"] + (atr * 0.2)
                if candidate < atr_sl and candidate > price:
                    ob_sl = candidate
                    break
        stop_loss = ob_sl if ob_sl != atr_sl else atr_sl
        risk_per_unit = stop_loss - price
        tp1 = price - (risk_per_unit * TP1_RR)
        tp2 = price - (risk_per_unit * TP2_RR)

    return {
        "signal": direction,
        "symbol": symbol,
        "entry": price,
        "score": score,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "funding_rate": funding_rate,
        "open_interest": open_interest,
        "oi_change_24h": oi_change,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "reasons": reasons,
        "has_divergence": divergence_found,
        "regime": regime,
        "bb_squeeze": bb_squeeze,
        "ob_in_zone": ob_in_zone,
        "fvg_in_zone": fvg_in_zone,
        "candle_time": last["close_time"].isoformat()
}
# ---------- STATE ----------
def load_state():
    state = load_json(STATE_FILE, {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    trimmed = {}
    for k, v in state.items():
        try:
            ts = k.rsplit("_", 1)[-1]
            if ts >= cutoff:
                trimmed[k] = v
        except Exception:
            continue
    return trimmed


def save_state(state):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    trimmed = {}
    for k, v in state.items():
        try:
            ts = k.rsplit("_", 1)[-1]
            if ts >= cutoff:
                trimmed[k] = v
        except Exception:
            continue
    save_json(STATE_FILE, trimmed)


def load_history():
    return load_json(HISTORY_FILE, [])


def save_history(history):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    history = [h for h in history if h.get("time", "") >= cutoff]
    save_json(HISTORY_FILE, history)


# ---------- COOLDOWN ----------
def load_cooldown():
    return load_json(COOLDOWN_FILE, {
        "consecutive_sl": 0,
        "cooldown_until": None,
        "daily_count": 0,
        "daily_date": None
    })


def save_cooldown(data):
    save_json(COOLDOWN_FILE, data)


def is_in_cooldown():
    cd = load_cooldown()
    cooldown_until = cd.get("cooldown_until")
    if not cooldown_until:
        return False
    try:
        until_dt = datetime.fromisoformat(cooldown_until)
        if datetime.now(timezone.utc) < until_dt:
            return True
        cd["cooldown_until"] = None
        cd["consecutive_sl"] = 0
        save_cooldown(cd)
        return False
    except Exception:
        return False


def register_sl_result():
    cd = load_cooldown()
    cd["consecutive_sl"] = cd.get("consecutive_sl", 0) + 1
    if cd["consecutive_sl"] >= 3:
        until = datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_AFTER_3_SL)
        cd["cooldown_until"] = until.isoformat()
        send_telegram(
            "🚨 <b>حالت Cooldown فعال شد</b>\n\n"
            "3 SL پشت‌سرهم خورده شد.\n"
            "ربات برای " + str(COOLDOWN_AFTER_3_SL) + " ساعت استراحت می‌کنه."
        )
    save_cooldown(cd)


def register_tp_result():
    cd = load_cooldown()
    cd["consecutive_sl"] = 0
    save_cooldown(cd)


def check_daily_limit():
    cd = load_cooldown()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if cd.get("daily_date") != today:
        cd["daily_date"] = today
        cd["daily_count"] = 0
        save_cooldown(cd)
    return cd.get("daily_count", 0) < MAX_DAILY_SIGNALS


def increment_daily_count():
    cd = load_cooldown()
    cd["daily_count"] = cd.get("daily_count", 0) + 1
    save_cooldown(cd)


def count_open_signals():
    history = load_history()
    return len([h for h in history if h.get("status") == "open"])


# ---------- POSITION SIZING ----------
def calculate_position_size(entry, stop_loss, score, account_size=1000, risk_per_trade_pct=1.0):
    try:
        risk_amount = account_size * (risk_per_trade_pct / 100)
        risk_per_unit = abs(entry - stop_loss)
        if risk_per_unit <= 0:
            return None
        units = risk_amount / risk_per_unit
        position_value = units * entry
        position_pct = (position_value / account_size) * 100
        return {
            "units": units,
            "position_value": position_value,
            "position_pct": position_pct,
            "risk_amount": risk_amount
        }
    except Exception:
        return None
# ---------- MESSAGE ----------
def build_message(res, fng_val):
    is_strong = res['score'] >= STRONG_SCORE
    has_div = res.get("has_divergence", False)
    has_ob = res.get("ob_in_zone", False)
    has_fvg = res.get("fvg_in_zone", False)

    if has_div:
        emoji = "💎"
        badge = "سیگنال واگرایی (نایاب)"
    elif has_ob and has_fvg:
        emoji = "🔥"
        badge = "سیگنال SMC کامل (OB+FVG)"
    elif is_strong:
        emoji = "🟢" if res['signal'] == "BUY" else "🔴"
        badge = "سیگنال قوی (4H)"
    else:
        emoji = "🟢" if res['signal'] == "BUY" else "🔴"
        badge = "سیگنال معمولی (4H)"

    if res['volume_ratio'] >= MIN_VOLUME_RATIO:
        vol_status = "قوی " + f"{res['volume_ratio']:.2f}" + "x"
    elif res['volume_ratio'] >= 1.0:
        vol_status = "معمولی " + f"{res['volume_ratio']:.2f}" + "x"
    else:
        vol_status = "ضعیف " + f"{res['volume_ratio']:.2f}" + "x"

    oi_change = res['oi_change_24h']
    if oi_change > 5:
        oi_status = "+" + f"{oi_change:.1f}" + "%"
    elif oi_change < -5:
        oi_status = f"{oi_change:.1f}" + "%"
    else:
        oi_status = f"{oi_change:+.1f}" + "%"

    regime = res.get("regime", "UNKNOWN")
    if regime == "TRENDING":
        regime_emoji = "🚀 Trending"
    elif regime == "RANGING":
        regime_emoji = "🔄 Ranging"
    else:
        regime_emoji = "❓ Unknown"

    squeeze_text = " ⚡ Squeeze" if res.get("bb_squeeze") else ""

    reasons_text = "\n".join(["• " + r for r in res["reasons"]])

    risk_pct = abs(res['entry'] - res['stop_loss']) / res['entry'] * 100
    reward1_pct = abs(res['tp1'] - res['entry']) / res['entry'] * 100
    reward2_pct = abs(res['tp2'] - res['entry']) / res['entry'] * 100

    pos = calculate_position_size(res['entry'], res['stop_loss'], res['score'])
    if pos:
        pos_text = (
            "<b>پیشنهاد حجم (حساب $1000، ریسک 1%):</b>\n"
            "🔹 " + f"{pos['units']:.4f}" + " واحد (~$" + f"{pos['position_value']:.2f}" + ")\n\n"
        )
    else:
        pos_text = ""

    msg = (
        emoji + " <b>" + badge + "</b>\n\n"
        "<b>نماد:</b> #" + res['symbol'].replace('USDT', '') + "\n"
        "<b>جهت:</b> " + res['signal'] + "\n"
        "<b>ورود:</b> " + f"{res['entry']:.6g}" + "\n\n"
        "🛑 <b>SL:</b> " + f"{res['stop_loss']:.6g}" + " (-" + f"{risk_pct:.2f}" + "%)\n"
        "🎯 <b>TP1:</b> " + f"{res['tp1']:.6g}" + " (+" + f"{reward1_pct:.2f}" + "%)\n"
        "🎯 <b>TP2:</b> " + f"{res['tp2']:.6g}" + " (+" + f"{reward2_pct:.2f}" + "%)\n\n"
        + pos_text
        + "📊 <b>امتیاز:</b> " + str(res['score']) + "/100 | <b>RSI:</b> " + f"{res['rsi']:.1f}" + "\n"
        + "📦 <b>حجم:</b> " + vol_status + "\n"
        + "⚡ <b>فاندینگ:</b> " + f"{res['funding_rate']*100:.4f}" + "%\n"
        + "💼 <b>OI:</b> " + format_num(res['open_interest']) + " | " + oi_status + "\n"
        + "🌊 <b>رژیم بازار:</b> " + regime_emoji + squeeze_text + "\n"
        + "😱 <b>ترس و طمع:</b> " + str(fng_val) + "\n\n"
        + "<b>دلایل:</b>\n" + reasons_text
    )
    return msg


def build_anomaly_message(res):
    return (
        "⚠️ <b>هشدار شرایط غیرعادی</b>\n\n"
        "<b>نماد:</b> #" + res['symbol'].replace('USDT', '') + "\n"
        "<b>قیمت:</b> " + f"{res['entry']:.6g}" + "\n\n"
        "حجم یا نوسان غیرطبیعی شناسایی شد.\n"
        "ممکنه دام (Trap) یا خبر مهم باشه.\n\n"
        "🚫 سیگنال معاملاتی صادر نشد."
    )


# ---------- TRACKING ----------
def get_current_price(symbol):
    data = http_get(BINANCE_SPOT_BASE + "/api/v3/ticker/price",
                    params={"symbol": symbol}, timeout=10)
    try:
        if data and isinstance(data, dict):
            return float(data.get("price", 0))
    except Exception:
        pass
    return None


def update_open_signals():
    history = load_history()
    updated = False

    for sig in history:
        if sig.get("status") != "open":
            continue

        price = get_current_price(sig["symbol"])
        if price is None:
            continue

        direction = sig["signal"]
        hit_event = None
        new_status = None

        if direction == "BUY":
            if price <= sig["stop_loss"]:
                hit_event, new_status = "SL", "sl"
            elif price >= sig["tp2"]:
                hit_event, new_status = "TP2", "tp2"
            elif price >= sig["tp1"] and not sig.get("tp1_hit"):
                sig["tp1_hit"] = True
                hit_event = "TP1"
                sig["stop_loss"] = sig["entry"]
                sig["trailing_activated"] = True
        else:
            if price >= sig["stop_loss"]:
                hit_event, new_status = "SL", "sl"
            elif price <= sig["tp2"]:
                hit_event, new_status = "TP2", "tp2"
            elif price <= sig["tp1"] and not sig.get("tp1_hit"):
                sig["tp1_hit"] = True
                hit_event = "TP1"
                sig["stop_loss"] = sig["entry"]
                sig["trailing_activated"] = True

        if hit_event:
            sig["status"] = new_status if new_status else "open"
            sig["closed_at"] = datetime.now(timezone.utc).isoformat()
            updated = True

            sym_name = sig['symbol'].replace('USDT', '')

            if hit_event == "TP1":
                register_tp_result()
                send_telegram(
                    "🎯 <b>TP1 خورد!</b>\n\n"
                    "نماد: #" + sym_name + "\n"
                    "قیمت: " + f"{price:.6g}" + "\n"
                    "SL به ورود منتقل شد (" + f"{sig['entry']:.6g}" + ")\n\n"
                    "✅ الان ریسک صفره!"
                )
            elif hit_event == "TP2":
                register_tp_result()
                profit_pct = abs(sig['tp2'] - sig['entry']) / sig['entry'] * 100
                send_telegram(
                    "🏆 <b>TP2 خورد!</b>\n\n"
                    "نماد: #" + sym_name + "\n"
                    "سود: +" + f"{profit_pct:.2f}" + "%"
                )
            elif hit_event == "SL":
                register_sl_result()
                if sig.get("trailing_activated"):
                    send_telegram(
                        "🛡️ <b>SL خورد (ریسک صفر)</b>\n\n"
                        "نماد: #" + sym_name
                    )
                else:
                    loss_pct = -abs(sig['stop_loss'] - sig['entry']) / sig['entry'] * 100
                    send_telegram(
                        "🛑 <b>SL خورد</b>\n\n"
                        "نماد: #" + sym_name + "\n"
                        "ضرر: " + f"{loss_pct:.2f}" + "%"
                    )

    if updated:
        save_history(history)


# ---------- STATS ----------
def compute_stats(days=7):
    history = load_history()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [h for h in history if h.get("time", "") >= cutoff]
    closed = [h for h in recent if h.get("status") in ("tp1", "tp2", "sl")]
    tp1_count = len([h for h in recent if h.get("tp1_hit")])
    tp2_count = len([h for h in closed if h.get("status") == "tp2"])
    sl_count = len([h for h in closed
                    if h.get("status") == "sl" and not h.get("trailing_activated")])
    sl_zero = len([h for h in closed
                   if h.get("status") == "sl" and h.get("trailing_activated")])

    pnl = 0
    for h in closed:
        if h.get("status") == "tp2":
            pnl += TP2_RR
        elif h.get("status") == "sl":
            if not h.get("trailing_activated"):
                pnl -= 1

    total = len(recent)
    wins = tp2_count + tp1_count
    win_rate = (wins / total * 100) if total > 0 else 0

    return {
        "total": total, "tp1": tp1_count, "tp2": tp2_count,
        "sl": sl_count, "sl_zero": sl_zero,
        "win_rate": win_rate, "pnl_r": pnl, "days": days
    }


def build_stats_report(stats, title):
    return (
        "📊 <b>گزارش " + title + "</b>\n"
        "بازه: " + str(stats['days']) + " روز\n\n"
        "کل سیگنال‌ها: " + str(stats['total']) + "\n\n"
        "🏆 TP2: " + str(stats['tp2']) + "\n"
        "🎯 TP1: " + str(stats['tp1']) + "\n"
        "🛑 SL: " + str(stats['sl']) + "\n"
        "🛡️ SL ریسک صفر: " + str(stats['sl_zero']) + "\n\n"
        "📈 نرخ موفقیت: " + f"{stats['win_rate']:.1f}" + "%\n"
        "💰 سود فرضی: " + f"{stats['pnl_r']:+.2f}" + "R"
        )
# ---------- PARALLEL SCAN ----------
def process_single_coin(item, btc_trend, fng_val, state, history_lock):
    symbol = item["symbol"]
    try:
        df = get_klines(symbol, TIMEFRAME_MAIN)
        if df is None:
            return None

        res = analyze_coin(df, symbol, btc_trend)
        if not res:
            return None

        if res.get("signal") == "ANOMALY":
            return {"type": "anomaly", "data": res}

        key = symbol + "_" + res['signal'] + "_" + res['candle_time']
        if key in state:
            return None

        return {"type": "signal", "data": res, "key": key}

    except Exception as e:
        log("[PROCESS ERROR] " + symbol + ": " + str(e))
        return None


def run_scan():
    start_time = time.time()

    if is_in_cooldown():
        log("[COOLDOWN] ربات در حالت استراحت پس از SL‌های پیاپی")
        return

    if not check_daily_limit():
        log("[LIMIT] به حداکثر سیگنال روزانه رسیده")
        return

    open_count = count_open_signals()
    if open_count >= MAX_CONCURRENT_SIGNALS:
        log("[LIMIT] حداکثر سیگنال باز: " + str(open_count))
        return

    fng_val, fng_text = get_fear_greed_index()
    log("\n[" + datetime.now(timezone.utc).strftime('%H:%M:%S UTC') + "] "
        "اسکن | ترس و طمع: " + str(fng_val) + " (" + fng_text + ")")

    btc_trend = get_btc_macro_trend()
    coins = get_scan_coins()
    if not coins:
        log("[WARNING] لیست کوین خالیه")
        return

    log("[INFO] " + str(len(coins)) + " کوین | BTC: " + btc_trend +
        " | باز: " + str(open_count))

    state = load_state()
    history = load_history()
    sent_count = 0
    div_count = 0
    anomaly_count = 0

    results = []
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        futures = {
            executor.submit(process_single_coin, item, btc_trend, fng_val, state, None): item
            for item in coins
        }
        for future in as_completed(futures):
            try:
                result = future.result(timeout=60)
                if result:
                    results.append(result)
            except Exception as e:
                log("[FUTURE ERROR] " + str(e))

    for r in results:
        if r["type"] == "anomaly":
            anomaly_count += 1
            send_telegram(build_anomaly_message(r["data"]))
            continue

        if r["type"] == "signal":
            res = r["data"]
            key = r["key"]

            msg = build_message(res, fng_val)
            if send_telegram(msg):
                state[key] = {"sent": True}
                save_state(state)

                history.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "symbol": res["symbol"],
                    "signal": res["signal"],
                    "entry": res["entry"],
                    "stop_loss": res["stop_loss"],
                    "tp1": res["tp1"],
                    "tp2": res["tp2"],
                    "score": res["score"],
                    "has_divergence": res.get("has_divergence", False),
                    "ob_in_zone": res.get("ob_in_zone", False),
                    "fvg_in_zone": res.get("fvg_in_zone", False),
                    "regime": res.get("regime", "UNKNOWN"),
                    "status": "open",
                    "tp1_hit": False,
                    "trailing_activated": False,
                })
                save_history(history)

                increment_daily_count()
                sent_count += 1

                if res.get("has_divergence"):
                    div_count += 1
                    log("[💎 DIV] " + res["symbol"] + " " + res["signal"] +
                        " | " + str(res["score"]))
                else:
                    log("[SENT] " + res["symbol"] + " " + res["signal"] +
                        " | " + str(res["score"]))

                if sent_count + open_count >= MAX_CONCURRENT_SIGNALS:
                    log("[LIMIT] حداکثر سیگنال باز رسید - توقف")
                    break

    elapsed = time.time() - start_time
    log("[DONE] " + str(sent_count) + " سیگنال | " + str(div_count) +
        " واگرایی | " + str(anomaly_count) + " غیرعادی | " +
        f"{elapsed:.1f}s")

    log("[TRACK] چک سیگنال‌های باز...")
    try:
        update_open_signals()
    except Exception as e:
        log("[TRACK ERROR] " + str(e))


# ---------- REPORT SCHEDULING ----------
def should_send_daily_report(now):
    return now.hour == DAILY_REPORT_HOUR and now.minute < 30


def should_send_weekly_report(now):
    return (now.weekday() == WEEKLY_REPORT_DAY
            and now.hour == DAILY_REPORT_HOUR
            and now.minute < 30)


# ---------- MAIN ----------
def main_github_actions():
    log("=" * 50)
    log("ربات INSTITUTIONAL GRADE v5 - GitHub Actions")
    log("=" * 50)

    try:
        run_scan()

        now = datetime.now(timezone.utc)
        today_key = now.strftime("%Y-%m-%d")
        cooldown = load_cooldown()

        if should_send_weekly_report(now):
            if cooldown.get("last_weekly_report") != today_key:
                log("[REPORT] گزارش هفتگی...")
                send_telegram(build_stats_report(compute_stats(7), "هفتگی"))
                cooldown["last_weekly_report"] = today_key
                save_cooldown(cooldown)

        if should_send_daily_report(now):
            if cooldown.get("last_daily_report") != today_key:
                log("[REPORT] گزارش روزانه...")
                send_telegram(build_stats_report(compute_stats(1), "روزانه"))
                cooldown["last_daily_report"] = today_key
                save_cooldown(cooldown)

    except Exception as e:
        log("[FATAL] " + str(e))
        log(traceback.format_exc())


def main_pydroid_loop():
    log("=" * 50)
    log("ربات INSTITUTIONAL GRADE v5 - Pydroid")
    log("=" * 50)

    last_daily = None
    last_weekly = None
    consecutive_errors = 0

    while True:
        try:
            run_scan()

            now = datetime.now(timezone.utc)
            today_key = now.strftime("%Y-%m-%d")

            if should_send_weekly_report(now) and last_weekly != today_key:
                log("[REPORT] گزارش هفتگی...")
                send_telegram(build_stats_report(compute_stats(7), "هفتگی"))
                last_weekly = today_key

            if should_send_daily_report(now) and last_daily != today_key:
                log("[REPORT] گزارش روزانه...")
                send_telegram(build_stats_report(compute_stats(1), "روزانه"))
                last_daily = today_key

            consecutive_errors = 0

        except KeyboardInterrupt:
            log("\n[STOP]")
            break
        except SystemExit:
            log("[STOP]")
            break
        except Exception as e:
            consecutive_errors += 1
            log("[FATAL] #" + str(consecutive_errors) + ": " + str(e))
            log(traceback.format_exc())
            if consecutive_errors >= 5:
                log("[RECOVER] 5 دقیقه صبر...")
                time.sleep(300)
                consecutive_errors = 0

        try:
            time.sleep(1800)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    if os.getenv("GITHUB_ACTIONS") == "true":
        main_github_actions()
    else:
        main_pydroid_loop()
