# =========================================================
# Crypto Signal Bot - Final Complete Version
# =========================================================

import os, json, time, logging
import requests, pandas as pd, numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ---------- SETTINGS ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TIMEFRAME_MAIN = "4h"
KLINE_LIMIT = 500

MIN_SCORE = 65
STRONG_SCORE = 88
MIN_VOLUME_RATIO = 1.20
MIN_24H_USDT_VOLUME = 15_000_000

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.50
MAX_SL_PCT = 6.0
TP1_RR = 1.60
TP2_RR = 2.80

RSI_OVERBOUGHT = 72
RSI_OVERSOLD = 28

DIVERGENCE_LOOKBACK = 40
DIVERGENCE_MIN_GAP = 3
DIVERGENCE_MAX_GAP = 20

OB_LOOKBACK = 40
FVG_MIN_SIZE_ATR = 0.3
VOL_REGIME_THRESHOLD = 1.15

MAX_CONCURRENT_SIGNALS = 8
STATE_FILE = "signals_state.json"
BINANCE_SPOT_BASE = "https://data-api.binance.vision"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

PARALLEL_WORKERS = 20
DEDUP_HOURS = 6


# ---------- UTILS ----------
def http_get(url, params=None, retries=2, timeout=6):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                sleep_time = (2 ** attempt) + np.random.uniform(0, 0.5)
                log.warning(f"[RATE LIMIT] sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1 + attempt)
            else:
                log.debug(f"[HTTP FAIL] {url}: {e}")
    return None


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("[TELEGRAM] missing token/chat_id")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 429:
            retry_after = res.json().get("parameters", {}).get("retry_after", 5)
            log.warning(f"[TELEGRAM 429] retry after {retry_after}s")
            time.sleep(retry_after + 1)
            return send_telegram(text)
        if res.status_code != 200:
            log.error(f"[TELEGRAM ERROR] {res.status_code}: {res.text[:300]}")
            return False
        return True
    except Exception as e:
        log.error(f"[TELEGRAM EXC] {e}")
        return False


def send_telegram_chunks(text, max_len=3800):
    if not text:
        return
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        cut = text.rfind("\n\n", 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    for i, part in enumerate(parts):
        if len(parts) > 1:
            part = f"<i>({i+1}/{len(parts)})</i>\n" + part
        send_telegram(part)
        time.sleep(1.2)


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


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"[SAVE JSON] {path}: {e}")


# ---------- DATA & FUTURES ----------
def get_fear_greed_index():
    data = http_get("https://api.alternative.me/fng/?limit=1")
    try:
        if data and "data" in data:
            return int(data["data"][0]["value"]), data["data"][0]["value_classification"]
    except Exception:
        pass
    return 50, "Neutral"


def fetch_futures_metrics_batch(symbols):
    metrics = {sym: {"funding_rate": 0.0, "open_interest": 0.0, "oi_change": 0.0}
               for sym in symbols}

    pi_data = http_get(BINANCE_FUTURES_BASE + "/fapi/v1/premiumIndex", timeout=5)
    if isinstance(pi_data, list) and pi_data:
        pi_map = {item["symbol"]: float(item.get("lastFundingRate", 0) or 0)
                  for item in pi_data if "symbol" in item}
    else:
        pi_map = {}
        log.warning("[FUTURES] premiumIndex unavailable - funding will be 0")

    for sym in symbols:
        metrics[sym]["funding_rate"] = pi_map.get(sym, 0.0)
        oi_data = http_get(BINANCE_FUTURES_BASE + "/fapi/v1/openInterest",
                           params={"symbol": sym}, timeout=3)
        if oi_data and isinstance(oi_data, dict):
            try:
                metrics[sym]["open_interest"] = float(oi_data.get("openInterest", 0) or 0)
            except Exception:
                pass

        hist = http_get(BINANCE_FUTURES_BASE + "/futures/data/openInterestHist",
                        params={"symbol": sym, "period": "4h", "limit": 2},
                        timeout=3)
        if hist and isinstance(hist, list) and len(hist) >= 2:
            try:
                prev_oi = float(hist[-2].get("sumOpenInterest", 0) or 0)
                curr_oi = float(hist[-1].get("sumOpenInterest", 0) or 0)
                if prev_oi > 0:
                    metrics[sym]["oi_change"] = ((curr_oi - prev_oi) / prev_oi) * 100
            except Exception:
                pass

    if not pi_map:
        log.warning("[FUTURES] no funding data - check API access")
    return metrics


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
        "GALA", "ENJ", "AXS", "THETA", "FTM", "SNX", "CRV", "MKR", "COMP"
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
                except Exception:
                    pass

    result = []
    for coin in unique_coins:
        symbol = coin + "USDT"
        if valid_volumes.get(symbol, 0) >= MIN_24H_USDT_VOLUME:
            result.append({"coin": coin, "symbol": symbol})
    log.info(f"[SCAN] {len(result)} coins passed volume filter")
    return result


def get_klines(symbol, interval="4h", limit=KLINE_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    data = http_get(BINANCE_SPOT_BASE + "/api/v3/klines", params=params)
    if not data or len(data) < 100:
        return None
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.iloc[:-1].reset_index(drop=True)


# ---------- INDICATORS ----------
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

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    df["atr_avg50"] = df["atr"].rolling(50).mean()

    df["volume_avg20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg20"].replace(0, np.nan)
    return df


def find_local_minima(series, order=2):
    idxs, vals = [], series.values
    for i in range(order, len(vals) - order):
        w = vals[i - order:i + order + 1]
        if vals[i] == w.min() and np.isfinite(vals[i]):
            idxs.append(i)
    return idxs


def find_local_maxima(series, order=2):
    idxs, vals = [], series.values
    for i in range(order, len(vals) - order):
        w = vals[i - order:i + order + 1]
        if vals[i] == w.max() and np.isfinite(vals[i]):
            idxs.append(i)
    return idxs


def has_bullish_divergence(df):
    try:
        recent = df.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
        if len(recent) < 20:
            return False
        lows_idx = find_local_minima(recent["low"], order=2)
        if len(lows_idx) < 2:
            return False
        i1, i2 = lows_idx[-2], lows_idx[-1]
        gap = i2 - i1
        if gap < DIVERGENCE_MIN_GAP or gap > DIVERGENCE_MAX_GAP:
            return False
        p1, p2 = recent.loc[i1, "low"], recent.loc[i2, "low"]
        r1, r2 = recent.loc[i1, "rsi"], recent.loc[i2, "rsi"]
        if not all(np.isfinite(x) for x in [p1, p2, r1, r2]):
            return False
        return p2 < p1 and r2 > r1 + 2
    except Exception:
        return False


def has_bearish_divergence(df):
    try:
        recent = df.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
        if len(recent) < 20:
            return False
        highs_idx = find_local_maxima(recent["high"], order=2)
        if len(highs_idx) < 2:
            return False
        i1, i2 = highs_idx[-2], highs_idx[-1]
        gap = i2 - i1
        if gap < DIVERGENCE_MIN_GAP or gap > DIVERGENCE_MAX_GAP:
            return False
        p1, p2 = recent.loc[i1, "high"], recent.loc[i2, "high"]
        r1, r2 = recent.loc[i1, "rsi"], recent.loc[i2, "rsi"]
        if not all(np.isfinite(x) for x in [p1, p2, r1, r2]):
            return False
        return p2 > p1 and r2 < r1 - 2
    except Exception:
        return False


# ---------- SMC ----------
def find_order_blocks(df, direction):
    obs = []
    try:
        recent = df.tail(OB_LOOKBACK).reset_index(drop=True)
        atr_last = float(df["atr"].iloc[-1])
        if not np.isfinite(atr_last) or atr_last <= 0:
            return obs

        for i in range(len(recent) - 3, 5, -1):
            candle = recent.iloc[i]
            nxt = recent.iloc[i + 1:i + 4]
            if len(nxt) == 0:
                continue
            if direction == "BUY":
                if candle["close"] < candle["open"] and (nxt["close"].max() - candle["low"]) > atr_last * 1.5:
                    obs.append({"top": float(candle["open"]), "bottom": float(candle["low"])})
                    if len(obs) >= 2:
                        break
            else:
                if candle["close"] > candle["open"] and (candle["high"] - nxt["close"].min()) > atr_last * 1.5:
                    obs.append({"top": float(candle["high"]), "bottom": float(candle["close"])})
                    if len(obs) >= 2:
                        break
    except Exception as e:
        log.debug(f"[OB] {e}")
    return obs


def find_fair_value_gaps(df, direction):
    fvgs = []
    try:
        recent = df.tail(OB_LOOKBACK).reset_index(drop=True)
        atr_last = float(df["atr"].iloc[-1])
        if not np.isfinite(atr_last) or atr_last <= 0:
            return fvgs

        for i in range(2, len(recent) - 1):
            prev, curr = recent.iloc[i - 1], recent.iloc[i]
            after = recent.iloc[i + 1:]
            if direction == "BUY" and curr["low"] > prev["high"]:
                size = curr["low"] - prev["high"]
                if size > atr_last * FVG_MIN_SIZE_ATR:
                    if len(after) == 0 or not (after["low"] < prev["high"]).any():
                        fvgs.append({"top": float(curr["low"]), "bottom": float(prev["high"])})
            elif direction == "SELL" and curr["high"] < prev["low"]:
                size = prev["low"] - curr["high"]
                if size > atr_last * FVG_MIN_SIZE_ATR:
                    if len(after) == 0 or not (after["high"] > prev["low"]).any():
                        fvgs.append({"top": float(prev["low"]), "bottom": float(curr["high"])})
        if len(fvgs) > 3:
            fvgs = fvgs[-3:]
    except Exception as e:
        log.debug(f"[FVG] {e}")
    return fvgs


def is_price_in_zone(price, zone):
    return zone["bottom"] <= price <= zone["top"]


# ---------- ANALYZE ----------
def analyze_coin(df, symbol, fng_val=50):
    try:
        df = add_indicators(df)
        last = df.iloc[-1]
        close = float(last["close"])
        atr = float(last["atr"])
        rsi = float(last["rsi"])
        volume_ratio = float(last["volume_ratio"])

        if not all(np.isfinite([close, atr, rsi, volume_ratio])) or atr <= 0:
            return None

        if volume_ratio < 0.8:
            return None

        regime = "📈 Trending" if (atr / float(last["atr_avg50"])) >= VOL_REGIME_THRESHOLD else "🔄 Ranging"
        direction, score = None, 50
        reasons = []

        if last["ema9"] > last["ema21"] and last["ema21"] > last["ema50"]:
            direction = "BUY"
            score += 15
            reasons.append("• تقاطع صعودی EMA 9/21")
        elif last["ema9"] < last["ema21"] and last["ema21"] < last["ema50"]:
            direction = "SELL"
            score += 15
            reasons.append("• تقاطع نزولی EMA 9/21")
        else:
            return None

        if direction == "BUY" and rsi > RSI_OVERBOUGHT:
            return None
        if direction == "SELL" and rsi < RSI_OVERSOLD:
            return None

        if np.isfinite(last["ema200"]):
            if direction == "BUY" and close > last["ema200"]:
                reasons.append("• بالای EMA200 (4H)")
            elif direction == "SELL" and close < last["ema200"]:
                reasons.append("• زیر EMA200 (4H)")

        if volume_ratio >= MIN_VOLUME_RATIO:
            score += 10
            reasons.append(f"• حجم قوی ({volume_ratio:.2f}x)")

        if direction == "BUY" and 45 <= rsi <= 65:
            score += 10
            reasons.append(f"• RSI مومنتوم ({rsi:.1f})")
        elif direction == "SELL" and 35 <= rsi <= 55:
            score += 10
            reasons.append(f"• RSI مومنتوم ({rsi:.1f})")

        if direction == "BUY" and has_bullish_divergence(df):
            score += 15
            reasons.append("• واگرایی صعودی (Bullish Div)")
        elif direction == "SELL" and has_bearish_divergence(df):
            score += 15
            reasons.append("• واگرایی نزولی (Bearish Div)")

        obs = find_order_blocks(df, direction)
        fvgs = find_fair_value_gaps(df, direction)
        if any(is_price_in_zone(close, ob) for ob in obs) or \
           any(is_price_in_zone(close, fvg) for fvg in fvgs):
            score += 10
            reasons.append("• در محدوده Order Block / FVG")

        if fng_val > 70 and direction == "BUY":
            score -= 15
            reasons.append(f"⚠️ جریمه: بازار Greed ({fng_val})")
        if fng_val < 30 and direction == "SELL":
            score -= 15
            reasons.append(f"⚠️ جریمه: بازار Fear ({fng_val})")

        if score < MIN_SCORE:
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

        sl_pct = ((sl - close) / close) * 100
        tp1_pct = ((tp1 - close) / close) * 100
        tp2_pct = ((tp2 - close) / close) * 100

        if abs(sl_pct) > MAX_SL_PCT:
            return None

        return {
            "symbol": symbol, "direction": direction, "score": score,
            "close": close, "sl": sl, "tp1": tp1, "tp2": tp2,
            "sl_pct": sl_pct, "tp1_pct": tp1_pct, "tp2_pct": tp2_pct,
            "rsi": rsi, "volume_ratio": volume_ratio, "regime": regime,
            "reasons": reasons, "strong": score >= STRONG_SCORE,
        }
    except Exception as e:
        log.error(f"[ANALYZE ERROR] {symbol}: {e}")
        return None


# ---------- DEDUP ----------
def load_sent_state():
    return load_json(STATE_FILE, default={"signals": {}})


def filter_duplicates(signals, state):
    now = time.time()
    sent = state.get("signals", {})
    filtered = []
    for sig in signals:
        key = f"{sig['symbol']}_{sig['direction']}"
        last_ts = sent.get(key, 0)
        if (now - last_ts) < DEDUP_HOURS * 3600:
            log.info(f"[DEDUP] skipped {key}")
            continue
        filtered.append(sig)
        sent[key] = now
    state["signals"] = sent
    return filtered, state


# ---------- MAIN ----------
def main():
    log.info("=== Crypto Signal Bot Started ===")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("[FATAL] TELEGRAM_TOKEN or TELEGRAM_CHAT_ID missing!")
        log.error("Set them in GitHub Secrets.")
        return

    fng_val, fng_text = get_fear_greed_index()
    log.info(f"Fear & Greed: {fng_val} ({fng_text})")

    coins = get_scan_coins()
    if not coins:
        log.error("[FATAL] No coins passed volume filter. Check Binance API access.")
        send_telegram("⚠️ ربات: هیچ کوینی از فیلتر حجم رد نشد.")
        return
    log.info(f"Scanning {len(coins)} coins...")

    signals = []
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        futures = {executor.submit(get_klines, c["symbol"]): c for c in coins}
        for future in as_completed(futures):
            c = futures[future]
            try:
                df = future.result()
                if df is not None:
                    res = analyze_coin(df, c["symbol"], fng_val=fng_val)
                    if res:
                        signals.append(res)
            except Exception as e:
                log.error(f"[ERROR] {c['symbol']}: {e}")

    log.info(f"[ANALYZE] {len(signals)} raw signals found")

    signals.sort(key=lambda x: x["score"], reverse=True)
    top_signals = signals[:MAX_CONCURRENT_SIGNALS]

    state = load_sent_state()
    top_signals, state = filter_duplicates(top_signals, state)
    log.info(f"[DEDUP] {len(top_signals)} signals after dedup")

    if top_signals:
        syms = [s["symbol"] for s in top_signals]
        futures_data = fetch_futures_metrics_batch(syms)
        for sig in top_signals:
            fm = futures_data.get(sig["symbol"], {})
           
