import os, sys, json, time, logging, csv
from datetime import datetime
import requests
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor

# ==================== تنظیمات شخصی (حالت متعادل) ====================
DRY_RUN = False
SAVE_TO_CSV = True
CSV_FILE = "my_signals.csv"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TIMEFRAME_MAIN = "4h"
TIMEFRAME_HIGHER = "1d"
KLINE_LIMIT = 500

MIN_SCORE = 59
STRONG_SCORE = 78
MIN_VOLUME_RATIO = 0.82
MIN_24H_USDT_VOLUME = 10_000_000
ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.40
MAX_SL_PCT = 6.5
TP1_RR = 1.70
TP2_RR = 2.90
RSI_OVERBOUGHT = 68
RSI_OVERSOLD = 32
DIVERGENCE_LOOKBACK = 45
DIVERGENCE_MIN_GAP = 4
DIVERGENCE_MAX_GAP = 22
OB_LOOKBACK = 30
FVG_MIN_SIZE_ATR = 0.20
VOL_REGIME_THRESHOLD = 1.12
MAX_ATR_RATIO = 3.0
MAX_CONCURRENT_SIGNALS = 6
STATE_FILE = "signals_state.json"
SPOT_BASE = "https://data-api.binance.vision"
FUT_BASE = "https://fapi.binance.com"
PARALLEL_WORKERS = 18
DEDUP_HOURS = 7

# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)
logger = logging.getLogger()


def http_get(url, params=None, retries=2, timeout=7):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep((2 ** attempt) + np.random.uniform(0, 0.6))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1.2 + attempt)
            else:
                logger.warning(f"HTTP FAIL {url}: {e}")
    return None


def send_telegram(text):
    if DRY_RUN:
        print("\n[DRY RUN TELEGRAM]\n" + text + "\n")
        return True
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
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
            ra = res.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(ra + 1)
            return send_telegram(text)
        return res.status_code == 200
    except Exception:
        return False


def format_num(n):
    try:
        n = float(n)
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
        if n >= 1_000_000: return f"{n/1_000_000:.2f}M"
        if n >= 1_000: return f"{n/1_000:.2f}K"
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
        logger.warning(f"SAVE {path}: {e}")


def save_signal_to_csv(sig, fng_val):
    if not SAVE_TO_CSV:
        return
    file_exists = os.path.isfile(CSV_FILE)
    try:
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "datetime", "symbol", "direction", "score", "entry", "sl", "tp1", "tp2",
                    "rsi", "volume_ratio", "funding", "oi_change", "fng", "reasons"
                ])
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                sig["symbol"],
                sig["direction"],
                sig["score"],
                round(sig["close"], 6),
                round(sig["sl"], 6),
                round(sig["tp1"], 6),
                round(sig["tp2"], 6),
                round(sig["rsi"], 1),
                round(sig["volume_ratio"], 2),
                round(sig.get("funding_rate", 0) * 100, 4),
                round(sig.get("oi_change", 0), 1),
                fng_val,
                " | ".join(sig["reasons"])
            ])
    except Exception as e:
        logger.warning(f"CSV save error: {e}")


def get_fear_greed_index():
    data = http_get("https://api.alternative.me/fng/?limit=1")
    try:
        if data and "data" in data:
            val = int(data["data"][0]["value"])
            cls = data["data"][0]["value_classification"]
            return val, cls
    except Exception:
        pass
    return 50, "Neutral"


def fetch_futures_metrics_batch(symbols):
    metrics = {sym: {"funding_rate": 0.0, "open_interest": 0.0, "oi_change": 0.0} for sym in symbols}
    pi = http_get(FUT_BASE + "/fapi/v1/premiumIndex", timeout=6)
    if isinstance(pi, list):
        for item in pi:
            s = item.get("symbol")
            if s in metrics:
                metrics[s]["funding_rate"] = float(item.get("lastFundingRate", 0) or 0)

    def fetch_single_oi(sym):
        res = {"symbol": sym, "oi": 0.0, "oi_change": 0.0}
        oi = http_get(FUT_BASE + "/fapi/v1/openInterest", params={"symbol": sym}, timeout=3)
        if oi and isinstance(oi, dict):
            try:
                res["oi"] = float(oi.get("openInterest", 0) or 0)
            except Exception:
                pass
        hist = http_get(FUT_BASE + "/futures/data/openInterestHist",
                        params={"symbol": sym, "period": "4h", "limit": 2}, timeout=3)
        if hist and isinstance(hist, list) and len(hist) >= 2:
            try:
                p = float(hist[-2].get("sumOpenInterest", 0) or 0)
                c = float(hist[-1].get("sumOpenInterest", 0) or 0)
                if p > 0:
                    res["oi_change"] = ((c - p) / p) * 100
            except Exception:
                pass
        return res

    with ThreadPoolExecutor(max_workers=14) as executor:
        for r in executor.map(fetch_single_oi, symbols):
            s = r["symbol"]
            if s in metrics:
                metrics[s]["open_interest"] = r["oi"]
                metrics[s]["oi_change"] = r["oi_change"]
    return metrics


def get_scan_coins():
    coins = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "TON", "ADA", "DOGE", "AVAX", "LINK",
        "DOT", "LTC", "BCH", "ETC", "XLM", "UNI", "FIL", "TRX", "ATOM", "NEAR",
        "AAVE", "SUI", "APT", "ARB", "OP", "SEI", "INJ", "TIA", "STX", "ALGO",
        "PEPE", "WIF", "BONK", "FLOKI", "SHIB", "ORDI", "BOME", "POL", "RENDER",
        "ICP", "KAS", "IMX", "GRT", "HBAR", "ENA", "PENDLE", "JUP", "PYTH",
        "W", "MANTA", "STRK", "AEVO", "EIGEN", "PNUT", "ACT", "GOAT", "SAND",
        "MANA", "GALA", "AXS", "CRV", "MKR", "COMP", "SNX"
    ]
    uniq = sorted(set(coins))
    tickers = http_get(SPOT_BASE + "/api/v3/ticker/24hr")
    vols = {}
    if tickers and isinstance(tickers, list):
        for t in tickers:
            s = t.get("symbol")
            if s and s.endswith("USDT"):
                try:
                    vols[s] = float(t.get("quoteVolume", 0) or 0)
                except Exception:
                    pass
    result = []
    for c in uniq:
        sym = c + "USDT"
        if vols.get(sym, 0) >= MIN_24H_USDT_VOLUME:
            result.append({"coin": c, "symbol": sym})
    return result


def get_klines(symbol, interval="4h", limit=KLINE_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    data = http_get(SPOT_BASE + "/api/v3/klines", params=params)
    if not data or len(data) < 120:
        return None
    cols = ["ot", "open", "high", "low", "close", "volume", "ct", "qv", "tr", "tb", "tq", "ig"]
    df = pd.DataFrame(data, columns=cols)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.iloc[:-1].reset_index(drop=True)


def add_indicators(df):
    df = df.copy()
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    d = df["close"].diff()
    g = d.clip(lower=0)
    l = -d.clip(upper=0)
    ag = g.ewm(alpha=1/14, adjust=False).mean()
    al = l.ewm(alpha=1/14, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    t1 = df["high"] - df["low"]
    t2 = (df["high"] - df["close"].shift()).abs()
    t3 = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([t1, t2, t3], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    df["atr_avg50"] = df["atr"].rolling(50).mean()
    df["vol_avg20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_avg20"]
    return df


def find_minima(series, order=2):
    idxs = []
    vals = series.values
    for i in range(order, len(vals) - order):
        w = vals[i - order:i + order + 1]
        if vals[i] == w.min() and np.isfinite(vals[i]):
            idxs.append(i)
    return idxs


def find_maxima(series, order=2):
    idxs = []
    vals = series.values
    for i in range(order, len(vals) - order):
        w = vals[i - order:i + order + 1]
        if vals[i] == w.max() and np.isfinite(vals[i]):
            idxs.append(i)
    return idxs


def bull_div(df):
    try:
        r = df.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
        if len(r) < 25: return False
        idx = find_minima(r["low"], 2)
        if len(idx) < 2: return False
        i1, i2 = idx[-2], idx[-1]
        if not (DIVERGENCE_MIN_GAP <= (i2 - i1) <= DIVERGENCE_MAX_GAP): return False
        return r.loc[i2, "low"] < r.loc[i1, "low"] and r.loc[i2, "rsi"] > r.loc[i1, "rsi"] + 2.0
    except Exception:
        return False


def bear_div(df):
    try:
        r = df.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
        if len(r) < 25: return False
        idx = find_maxima(r["high"], 2)
        if len(idx) < 2: return False
        i1, i2 = idx[-2], idx[-1]
        if not (DIVERGENCE_MIN_GAP <= (i2 - i1) <= DIVERGENCE_MAX_GAP): return False
        return r.loc[i2, "high"] > r.loc[i1, "high"] and r.loc[i2, "rsi"] < r.loc[i1, "rsi"] - 2.0
    except Exception:
        return False


def detect_order_block(df, direction):
    try:
        look = df.tail(OB_LOOKBACK).reset_index(drop=True)
        if len(look) < 12: return False
        if direction == "BUY":
            for i in range(len(look)-4, 4, -1):
                body = abs(look.loc[i, "close"] - look.loc[i, "open"])
                rng = look.loc[i, "high"] - look.loc[i, "low"]
                if look.loc[i, "close"] < look.loc[i, "open"] and body > rng * 0.6:
                    if look.loc[i+1:, "high"].max() > look.loc[i, "high"] * 1.005:
                        return True
        else:
            for i in range(len(look)-4, 4, -1):
                body = abs(look.loc[i, "close"] - look.loc[i, "open"])
                rng = look.loc[i, "high"] - look.loc[i, "low"]
                if look.loc[i, "close"] > look.loc[i, "open"] and body > rng * 0.6:
                    if look.loc[i+1:, "low"].min() < look.loc[i, "low"] * 0.995:
                        return True
        return False
    except Exception:
        return False


def detect_fvg(df, direction, atr):
    try:
        look = df.tail(18).reset_index(drop=True)
        if len(look) < 6 or atr <= 0: return False
        min_gap = atr * FVG_MIN_SIZE_ATR
        if direction == "BUY":
            for i in range(2, len(look)):
                if look.loc[i, "low"] - look.loc[i-2, "high"] > min_gap:
                    return True
        else:
            for i in range(2, len(look)):
                if look.loc[i-2, "low"] - look.loc[i, "high"] > min_gap:
                    return True
        return False
    except Exception:
        return False


def get_higher_tf_trend(symbol):
    df = get_klines(symbol, TIMEFRAME_HIGHER, 120)
    if df is None or len(df) < 50:
        return "neutral"
    df = add_indicators(df)
    last = df.iloc[-1]
    if last["close"] > last["ema50"] and last["ema9"] > last["ema21"]:
        return "bullish"
    if last["close"] < last["ema50"] and last["ema9"] < last["ema21"]:
        return "bearish"
    return "neutral"


def analyze_coin(df, symbol, fng_val=50, btc_bullish=True, funding_rate=0.0, oi_change=0.0, higher_trend="neutral"):
    try:
        df = add_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(last["close"])
        atr = float(last["atr"])
        rsi = float(last["rsi"])
        vr = float(last["vol_ratio"])
        macd_hist = float(last["macd_hist"])
        macd_hist_prev = float(prev["macd_hist"])
        atr_avg = float(last["atr_avg50"])

        if not all(np.isfinite([close, atr, rsi, vr, macd_hist, atr_avg])) or atr <= 0:
            return None
        if vr < MIN_VOLUME_RATIO:
            return None
        if atr / atr_avg > MAX_ATR_RATIO:
            return None

        e9, e21 = last["ema9"], last["ema21"]
        e9p, e21p = prev["ema9"], prev["ema21"]
        e50 = last["ema50"]

        bullish_cross = e9p <= e21p and e9 > e21
        bearish_cross = e9p >= e21p and e9 < e21
        strong_buy_align = e9 > e21 > e50 and close > e50
        strong_sell_align = e9 < e21 < e50 and close < e50

        direction = None
        score = 42
        reasons = []

        if bullish_cross or strong_buy_align:
            direction = "BUY"
            if bullish_cross:
                score += 16
                reasons.append("• کراس صعودی تازه EMA 9/21")
            else:
                score += 9
                reasons.append("• هم‌راستایی قوی صعودی")
        elif bearish_cross or strong_sell_align:
            direction = "SELL"
            if bearish_cross:
                score += 16
                reasons.append("• کراس نزولی تازه EMA 9/21")
            else:
                score += 9
                reasons.append("• هم‌راستایی قوی نزولی")
        else:
            return None

        # تایم‌فریم بالاتر (جریمه کمتر شده)
        if direction == "BUY" and higher_trend == "bullish":
            score += 8
            reasons.append("• تأیید روند روزانه صعودی")
        elif direction == "SELL" and higher_trend == "bearish":
            score += 8
            reasons.append("• تأیید روند روزانه نزولی")
        elif direction == "BUY" and higher_trend == "bearish":
            score -= 6
            reasons.append("• ⚠️ خلاف روند روزانه")
        elif direction == "SELL" and higher_trend == "bullish":
            score -= 6
            reasons.append("• ⚠️ خلاف روند روزانه")

        # بیت‌کوین
        if direction == "BUY" and not btc_bullish:
            score -= 7
            reasons.append("• ⚠️ بیت‌کوین نزولی")
        elif direction == "SELL" and btc_bullish:
            score -= 4

        # RSI
        if direction == "BUY":
            if rsi > RSI_OVERBOUGHT: return None
            if 38 <= rsi <= 57:
                score += 10
                reasons.append(f"• RSI خوب ({rsi:.1f})")
            else:
                score += 4
        else:
            if rsi < RSI_OVERSOLD: return None
            if 43 <= rsi <= 62:
                score += 10
                reasons.append(f"• RSI خوب ({rsi:.1f})")
            else:
                score += 4

        # MACD
        if direction == "BUY" and macd_hist > 0 and macd_hist > macd_hist_prev:
            score += 8
            reasons.append("• مومنتوم صعودی در حال تقویت")
        elif direction == "SELL" and macd_hist < 0 and macd_hist < macd_hist_prev:
            score += 8
            reasons.append("• مومنتوم نزولی در حال تقویت")
        elif (direction == "BUY" and macd_hist > 0) or (direction == "SELL" and macd_hist < 0):
            score += 3

        # واگرایی
        if direction == "BUY" and bull_div(df):
            score += 12
            reasons.append("• واگرایی صعودی")
        elif direction == "SELL" and bear_div(df):
            score += 12
            reasons.append("• واگرایی نزولی")

        # Order Block & FVG
        if detect_order_block(df, direction):
            score += 7
            reasons.append("• Order Block")
        if detect_fvg(df, direction, atr):
            score += 6
            reasons.append("• Fair Value Gap")

        # حجم
        if vr >= 1.35:
            score += 6
            reasons.append(f"• حجم قوی ({vr:.2f}x)")
        elif vr >= 1.0:
            score += 3

        # Funding
        if funding_rate != 0:
            if direction == "BUY" and funding_rate < -0.00008:
                score += 5
                reasons.append("• فاندینگ منفی (به نفع لانگ)")
            elif direction == "SELL" and funding_rate > 0.00025:
                score += 5
                reasons.append("• فاندینگ مثبت (به نفع شورت)")
            elif direction == "BUY" and funding_rate > 0.00045:
                score -= 5
                reasons.append("• ⚠️ فاندینگ خیلی مثبت")

        # OI
        if oi_change > 4 and direction == "BUY":
            score += 3
        elif oi_change < -4 and direction == "SELL":
            score += 3

        # Fear & Greed
        if fng_val <= 25 and direction == "BUY":
            score += 6
            reasons.append("• ترس شدید بازار")
        elif fng_val >= 75 and direction == "SELL":
            score += 5
            reasons.append("• طمع بالا")

        if score < MIN_SCORE:
            return None

        # SL / TP
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

        slp = ((sl - close) / close) * 100
        if abs(slp) > MAX_SL_PCT:
            return None

        regime = "📈 Trending" if (atr / atr_avg) >= VOL_REGIME_THRESHOLD else "🔄 Ranging"

        return {
            "symbol": symbol,
            "direction": direction,
            "score": min(int(score), 96),
            "close": close,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "sl_pct": slp,
            "tp1_pct": ((tp1 - close) / close) * 100,
            "tp2_pct": ((tp2 - close) / close) * 100,
            "rsi": rsi,
            "volume_ratio": vr,
            "regime": regime,
            "reasons": reasons,
            "strong": score >= STRONG_SCORE,
            "funding_rate": funding_rate,
            "oi_change": oi_change,
            "higher_trend": higher_trend,
        }
    except Exception:
        return None


def filter_dups(signals, state):
    now = time.time()
    sent = state.get("signals", {})
    out = []
    for sig in signals:
        key = f"{sig['symbol']}_{sig['direction']}"
        if (now - sent.get(key, 0)) < DEDUP_HOURS * 3600:
            continue
        out.append(sig)
        sent[key] = now
    state["signals"] = sent
    return out, state


def build_msg(sig, fng_val):
    emoji = "🟢" if sig["direction"] == "BUY" else "🔴"
    tag = "#" + sig["symbol"].replace("USDT", "")
    risk_usd = 10.0
    pr = abs(sig["close"] - sig["sl"])
    units = (risk_usd / pr) if pr > 0 else 0
    notional = units * sig["close"]

    L = [
        f"{emoji} <b>سیگنال شخصی (4H) - حال
