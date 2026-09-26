import os, sys, json, time, logging
import requests
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)

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
SPOT_BASE = "https://data-api.binance.vision"
FUT_BASE = "https://fapi.binance.com"
PARALLEL_WORKERS = 20
DEDUP_HOURS = 6


def http_get(url, params=None, retries=2, timeout=6):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                st = (2 ** attempt) + np.random.uniform(0, 0.5)
                print(f"[RATE] sleep {st:.1f}s", flush=True)
                time.sleep(st)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1 + attempt)
            else:
                print(f"[HTTP FAIL] {url}: {e}", flush=True)
    return None


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG] missing token/chat", flush=True)
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
            info = res.json().get("parameters", {})
            ra = info.get("retry_after", 5)
            print(f"[TG 429] retry {ra}s", flush=True)
            time.sleep(ra + 1)
            return send_telegram(text)
        if res.status_code != 200:
            msg = res.text[:300]
            print(f"[TG ERROR] {res.status_code}: {msg}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[TG EXC] {e}", flush=True)
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


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        if default is not None:
            return default
        return {}


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SAVE] {path}: {e}", flush=True)


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
    
    pi = http_get(FUT_BASE + "/fapi/v1/premiumIndex", timeout=5)
    if isinstance(pi, list):
        for item in pi:
            s = item.get("symbol")
            if s in metrics:
                metrics[s]["funding_rate"] = float(item.get("lastFundingRate", 0) or 0)

    def fetch_single_oi(sym):
        res = {"symbol": sym, "oi": 0.0, "oi_change": 0.0}
        url1 = FUT_BASE + "/fapi/v1/openInterest"
        oi = http_get(url1, params={"symbol": sym}, timeout=3)
        if oi and isinstance(oi, dict):
            try:
                res["oi"] = float(oi.get("openInterest", 0) or 0)
            except Exception:
                pass
        
        url2 = FUT_BASE + "/futures/data/openInterestHist"
        hist = http_get(url2, params={"symbol": sym, "period": "4h", "limit": 2}, timeout=3)
        if hist and isinstance(hist, list) and len(hist) >= 2:
            try:
                p = float(hist[-2].get("sumOpenInterest", 0) or 0)
                c = float(hist[-1].get("sumOpenInterest", 0) or 0)
                if p > 0:
                    res["oi_change"] = ((c - p) / p) * 100
            except Exception:
                pass
        return res

    with ThreadPoolExecutor(max_workers=15) as executor:
        results = executor.map(fetch_single_oi, symbols)
        for r in results:
            s = r["symbol"]
            if s in metrics:
                metrics[s]["open_interest"] = r["oi"]
                metrics[s]["oi_change"] = r["oi_change"]

    return metrics


def get_scan_coins():
    coins = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "TON", "ADA", "DOGE",
        "AVAX", "LINK", "DOT", "LTC", "BCH", "ETC", "XLM", "UNI",
        "FIL", "TRX", "ATOM", "NEAR", "AAVE", "SUI", "APT", "ARB",
        "OP", "SEI", "INJ", "TIA", "STX", "ALGO", "PEPE", "WIF",
        "BONK", "FLOKI", "SHIB", "MEME", "NOT", "ORDI", "BOME",
        "POL", "RENDER", "ICP", "KAS", "IMX", "GRT", "HBAR",
        "ENA", "PENDLE", "JUP", "PYTH", "W", "MANTA", "ALT",
        "STRK", "AXL", "AEVO", "REZ", "BB", "IO", "ZK",
        "LISTA", "DOGS", "CATI", "HMSTR", "EIGEN", "SCR",
        "PNUT", "ACT", "GOAT", "CHZ", "SAND", "MANA", "GALA",
        "ENJ", "AXS", "THETA", "FTM", "SNX", "CRV", "MKR",
        "COMP"
    ]
    uniq = sorted(set(coins))
    tickers = http_get(SPOT_BASE + "/api/v3/ticker/24hr")
    vols = {}
    if tickers and isinstance(tickers, list):
        for t in tickers:
            s = t.get("symbol")
            if s and s.endswith("USDT"):
                try:
                    v = float(t.get("quoteVolume", 0) or 0)
                    vols[s] = v
                except Exception:
                    pass
    result = []
    for c in uniq:
        sym = c + "USDT"
        if vols.get(sym, 0) >= MIN_24H_USDT_VOLUME:
            result.append({"coin": c, "symbol": sym})
    print(f"[SCAN] {len(result)} coins passed", flush=True)
    return result


def get_klines(symbol, interval="4h", limit=KLINE_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    data = http_get(SPOT_BASE + "/api/v3/klines", params=params)
    if not data or len(data) < 100:
        return None
    cols = ["ot", "open", "high", "low", "close", "volume",
            "ct", "qv", "tr", "tb", "tq", "ig"]
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
        if len(r) < 20:
            return False
        idx = find_minima(r["low"], 2)
        if len(idx) < 2:
            return False
        i1, i2 = idx[-2], idx[-1]
        gap = i2 - i1
        if gap < DIVERGENCE_MIN_GAP or gap > DIVERGENCE_MAX_GAP:
            return False
        p1, p2 = r.loc[i1, "low"], r.loc[i2, "low"]
        r1, r2 = r.loc[i1, "rsi"], r.loc[i2, "rsi"]
        if not all(np.isfinite(x) for x in [p1, p2, r1, r2]):
            return False
        return p2 < p1 and r2 > r1 + 2
    except Exception:
        return False


def bear_div(df):
    try:
        r = df.tail(DIVERGENCE_LOOKBACK).reset_index(drop=True)
        if len(r) < 20:
            return False
        idx = find_maxima(r["high"], 2)
        if len(idx) < 2:
            return False
        i1, i2 = idx[-2], idx[-1]
        gap = i2 - i1
        if gap < DIVERGENCE_MIN_GAP or gap > DIVERGENCE_MAX_GAP:
            return False
        p1, p2 = r.loc[i1, "high"], r.loc[i2, "high"]
        r1, r2 = r.loc[i1, "rsi"], r.loc[i2, "rsi"]
        if not all(np.isfinite(x) for x in [p1, p2, r1, r2]):
            return False
        return p2 > p1 and r2 < r1 - 2
    except Exception:
        return False


def find_obs(df, direction):
    obs = []
    try:
        r = df.tail(OB_LOOKBACK).reset_index(drop=True)
        a = float(df["atr"].iloc[-1])
        if not np.isfinite(a) or a <= 0:
            return obs
        for i in range(len(r) - 3, 5, -1):
            c = r.iloc[i]
            n = r.iloc[i + 1:i + 4]
            if len(n) == 0:
                continue
            if direction == "BUY":
                if c["close"] < c["open"] and (n["close"].max() - c["low"]) > a * 1.5:
                    obs.append({"top": float(c["open"]), "bottom": float(c["low"])})
                    if len(obs) >= 2:
                        break
            else:
                if c["close"] > c["open"] and (c["high"] - n["close"].min()) > a * 1.5:
                    obs.append({"top": float(c["high"]), "bottom": float(c["close"])})
                    if len(obs) >= 2:
                        break
    except Exception as e:
        print(f"[OB] {e}", flush=True)
    return obs


def find_fvgs(df, direction):
    fvgs = []
    try:
        r = df.tail(OB_LOOKBACK).reset_index(drop=True)
        a = float(df["atr"].iloc[-1])
        if not np.isfinite(a) or a <= 0:
            return fvgs
        for i in range(2, len(r) - 1):
            p = r.iloc[i - 1]
            c = r.iloc[i]
            af = r.iloc[i + 1:]
            if direction == "BUY":
                if c["low"] > p["high"] and (c["low"] - p["high"]) > a * FVG_MIN_SIZE_ATR:
                    filled = len(af) > 0 and (af["low"] < p["high"]).any()
                    if not filled:
                        fvgs.append({"top": float(c["low"]), "bottom": float(p["high"])})
            elif direction == "SELL":
                if c["high"] < p["low"] and (p["low"] - c["high"]) > a * FVG_MIN_SIZE_ATR:
                    filled = len(af) > 0 and (af["high"] > p["low"]).any()
                    if not filled:
                        fvgs.append({"top": float(p["low"]), "bottom": float(c["high"])})
        if len(fvgs) > 3:
            fvgs = fvgs[-3:]
    except Exception as e:
        print(f"[FVG] {e}", flush=True)
    return fvgs


def in_zone(price, zone):
    return zone["bottom"] <= price <= zone["top"]


def analyze_coin(df, symbol, fng_val=50, btc_bullish=True):
    try:
        df = add_indicators(df)
        last = df.iloc[-1]
        close = float(last["close"])
        atr = float(last["atr"])
        rsi = float(last["rsi"])
        vr = float(last["vol_ratio"])
        if not all(np.isfinite([close, atr, rsi, vr])) or atr <= 0 or vr < 0.8:
            return None
        
        atr_ratio = atr / float(last["atr_avg50"])
        regime = "📈 Trending" if atr_ratio >= VOL_REGIME_THRESHOLD else "🔄 Ranging"
        
        direction = None
        score = 50
        reasons = []
        e9, e21, e50 = last["ema9"], last["ema21"], last["ema50"]
        
        if e9 > e21 and e21 > e50:
            direction = "BUY"
            score += 15
            reasons.append("• تقاطع صعودی EMA 9/21")
        elif e9 < e21 and e21 < e50:
            direction = "SELL"
            score += 15
            reasons.append("• تقاطع نزولی EMA 9/21")
        else:
            return None

        if direction == "BUY" and not btc_bullish:
            return None

        if direction == "BUY" and rsi > RSI_OVERBOUGHT:
            return None
        if direction == "SELL" and rsi < RSI_OVERSOLD:
            return None

        e200 = last["ema200"]
        if np.isfinite(e200):
            if direction == "BUY" and close > e200:
                reasons.append("• بالای EMA200 (4H)")
            elif direction == "SELL" and close < e200:
                reasons.append("• زیر EMA200 (4H)")

        if vr >= MIN_VOLUME_RATIO:
            score += 10
            reasons.append(f"• حجم قوی ({vr:.2f}x)")

        if direction == "BUY" and 45 <= rsi <= 65:
            score += 10
            reasons.append(f"• RSI ({rsi:.1f})")
        if direction == "SELL" and 35 <= rsi <= 55:
            score += 10
            reasons.append(f"• RSI ({rsi:.1f})")

        if direction == "BUY" and bull_div(df):
            score += 15
            reasons.append("• واگرایی صعودی")
        if direction == "SELL" and bear_div(df):
            score += 15
            reasons.append("• واگرایی نزولی")

        obs = find_obs(df, direction)
        fvgs = find_fvgs(df, direction)
        if any(in_zone(close, o) for o in obs) or any(in_zone(close, f) for f in fvgs):
            score += 10
            reasons.append("• OB / FVG")

        if fng_val > 70 and direction == "BUY":
            score -= 15
            reasons.append(f"⚠️ Greed ({fng_val})")
        if fng_val < 30 and direction == "SELL":
            score -= 15
            reasons.append(f"⚠️ Fear ({fng_val})")

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

        slp = ((sl - close) / close) * 100
        if abs(slp) > MAX_SL_PCT:
            return None

        return {
            "symbol": symbol,
            "direction": direction,
            "score": score,
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
        }
    except Exception as e:
        return None


def load_state():
    return load_json(STATE_FILE, default={"signals": {}})


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
    stype = "سیگنال قوی (4H)" if sig["strong"] else "سیگنال معمولی (4H)"
    tag = "#" + sig["symbol"].replace("USDT", "")
    
    risk_usd = 10.0
    pr = abs(sig["close"] - sig["sl"])
    units = (risk_usd / pr) if pr > 0 else 0
    notional = units * sig["close"]
    
    v = sig["volume_ratio"]
    v_text = f"قوی {v:.2f}x" if v >= 1.5 else f"متوسط {v:.2f}x"

    L = [
        f"{emoji} <b>{stype}</b>",
        "",
        f"نماد: <b>{tag}</b>",
        f"جهت: <b>{sig['direction']}</b>",
        f"ورود: <code>{sig['close']:.4f}</code>",
        "",
        f"🛑 SL: <code>{sig['sl']:.4f}</code> ({sig['sl_pct']:+.2f}%)",
        f"🎯 TP1: <code>{sig['tp1']:.4f}</code> ({sig['tp1_pct']:+.2f}%)",
        f"🎯 TP2: <code>{sig['tp2']:.4f}</code> ({sig['tp2_pct']:+.2f}%)",
        "",
        "پیشنهاد حجم (سرمایه $1000، ریسک 1%):",
        f"🔹 <code>{units:.4f}</code> واحد | ~$<code>{notional:.2f}</code>",
        "",
        f"📊 امتیاز: <b>{sig['score']}/100</b> | RSI: {sig['rsi']:.1f}",
        f"📦 حجم: {v_text}"
    ]

    fr = sig.get("funding_rate", 0)
    if fr != 0.0:
        L.append(f"⚡ فاندینگ: <code>{fr*100:.4f}%</code>")
    
    oi = sig.get("open_interest", 0)
    if oi > 0:
        L.append(f"💼 OI: <code>{format_num(oi)}</code> | {sig['oi_change']:+.1f}%")

    L.extend([
        f"🌊 رژیم: {sig['regime']}",
        f"😱 F&G: {fng_val}",
        "",
        "دلایل:"
    ])
    L.extend(sig["reasons"])
    L.append("—" * 20)
    return "\n".join(L)


def main():
    print("=" * 50, flush=True)
    print("BOT STARTED", flush=True)

    send_telegram("🤖 ربات اسکنر پیشرفته بازار روشن شد...")

    fng_val, fng_cls = get_fear_greed_index()
    print(f"[FNG] Index: {fng_val} ({fng_cls})", flush=True)

    btc_bullish = True
    btc_df = get_klines("BTCUSDT", TIMEFRAME_MAIN, 200)
    if btc_df is not None and len(btc_df) > 50:
        btc_df = add_indicators(btc_df)
        btc_last = btc_df.iloc[-1]
        if np.isfinite(btc_last["ema200"]) and btc_last["close"] < btc_last["ema200"]:
            btc_bullish = False

    coins = get_scan_coins()
    if not coins:
        return

    symbols = [item["symbol"] for item in coins]
    fut_metrics = fetch_futures_metrics_batch(symbols)

    signals = []

    def process_coin(item):
        symbol = item["symbol"]
        df = get_klines(symbol, TIMEFRAME_MAIN, KLINE_LIMIT)
        if df is None or len(df) < 100:
            return None
        sig = analyze_coin(df, symbol, fng_val, btc_bullish)
        if sig:
            m = fut_metrics.get(symbol, {})
            sig["funding_rate"] = m.get("funding_rate", 0.0)
            sig["open_interest"] = m.get("open_interest", 0.0)
            sig["oi_change"] = m.get("oi_change", 0.0)
        return sig

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        results = executor.map(process_coin, coins)
        for r in results:
            if r is not None:
                signals.append(r)

    signals.sort(key=lambda x: x["score"], reverse=True)
    signals = signals[:MAX_CONCURRENT_SIGNALS]

    state = load_state()
    fresh_signals, state = filter_dups(signals, state)
    save_json(STATE_FILE, state)

    if not fresh_signals:
        send_telegram("ℹ️ اسکن بازار به اتمام رسید. در این چرخه سیگنال جدیدی یافت نشد.")
        return

    header_text = (
        "🤖 <b>گزارش اسکن پیشرفته بازار (" + TIMEFRAME_MAIN + ")</b>\n" +
        "📅 شاخص ترس و طمع: <b>" + str(fng_val) + " (" + str(fng_cls) + ")</b>\n" +
        "🔍 سیگن
