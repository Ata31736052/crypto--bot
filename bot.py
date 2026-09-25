# =========================================================
# Crypto Signal Bot - Final Part 1
# =========================================================

import os, json, time
import requests, pandas as pd, numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- SETTINGS ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TIMEFRAME_MAIN = "4h"
KLINE_LIMIT = 200

MIN_SCORE = 60
STRONG_SCORE = 78
MIN_VOLUME_RATIO = 1.20
MIN_24H_USDT_VOLUME = 15_000_000

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.50
TP1_RR = 1.60
TP2_RR = 2.80

DIVERGENCE_LOOKBACK = 25
DIVERGENCE_MIN_GAP = 3
DIVERGENCE_MAX_GAP = 15

OB_LOOKBACK = 40
FVG_MIN_SIZE_ATR = 0.3
VOL_REGIME_THRESHOLD = 1.15

MAX_CONCURRENT_SIGNALS = 8
STATE_FILE = "signals_state.json"
BINANCE_SPOT_BASE = "https://data-api.binance.vision"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

PARALLEL_WORKERS = 20

# ---------- UTILS ----------
def log(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


def http_get(url, params=None, retries=1, timeout=6):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
    return None


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    if len(text) > 4000:
        text = text[:3990] + "\n...(کوتاه شد)"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception:
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


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


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
    metrics = {}
    try:
        pi_data = http_get(BINANCE_FUTURES_BASE + "/fapi/v1/premiumIndex", timeout=5)
        pi_map = {item["symbol"]: float(item.get("lastFundingRate", 0) or 0) for item in pi_data} if isinstance(pi_data, list) else {}

        for sym in symbols:
            fr = pi_map.get(sym, 0.0)
            oi = 0.0
            try:
                oi_data = http_get(BINANCE_FUTURES_BASE + "/fapi/v1/openInterest", params={"symbol": sym}, timeout=3)
                if oi_data and isinstance(oi_data, dict):
                    oi = float(oi_data.get("openInterest", 0) or 0)
            except Exception:
                pass
            metrics[sym] = {"funding_rate": fr, "open_interest": oi, "oi_change": 0.0}
    except Exception:
        for sym in symbols:
            metrics[sym] = {"funding_rate": 0.0, "open_interest": 0.0, "oi_change": 0.0}
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
    return result


def get_klines(symbol, interval="4h", limit=KLINE_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    data = http_get(BINANCE_SPOT_BASE + "/api/v3/klines", params=params)
    if not data or len(data) < 50:
        return None
    df = pd.DataFrame(data, columns=["open_time", "open", "high", "low", "close", "volume",
                                     "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.iloc[:-1].reset_index(drop=True)
# =========================================================
# Crypto Signal Bot - Final Part 2
# =========================================================

# ---------- INDICATORS & SMC ----------
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
        if len(recent) < 15:
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
                if candle["close"] < candle["open"] and (nxt["close"].max() - candle["low"]) > atr_last * 1.5:
                    obs.append({"top": float(candle["open"]), "bottom": float(candle["low"])})
                    if len(obs) >= 2:
                        break
            else:
                if candle["close"] > candle["open"] and (candle["high"] - nxt["close"].min()) > atr_last * 1.5:
                    obs.append({"top": float(candle["high"]), "bottom": float(candle["close"])})
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
            if direction == "BUY" and curr["low"] > prev["high"]:
                if (curr["low"] - prev["high"]) > atr_last * FVG_MIN_SIZE_ATR:
                    fvgs.append({"top": float(curr["low"]), "bottom": float(prev["high"])})
            elif direction == "SELL" and curr["high"] < prev["low"]:
                if (prev["low"] - curr["high"]) > atr_last * FVG_MIN_SIZE_ATR:
                    fvgs.append({"top": float(prev["low"]), "bottom": float(curr["high"])})
        if len(fvgs) > 3:
            fvgs = fvgs[-3:]
    except Exception:
        pass
    return fvgs


def is_price_in_zone(price, zone):
    return zone["bottom"] <= price <= zone["top"]


def analyze_coin(df, symbol):
    try:
        df = add_indicators(df)
        last = df.iloc[-1]
        close, atr, rsi, volume_ratio = float(last["close"]), float(last["atr"]), float(last["rsi"]), float(last["volume_ratio"])
        
        if not all(np.isfinite([close, atr, rsi, volume_ratio])) or atr <= 0:
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
            
        if close > last["ema200"]:
            reasons.append("• بالای EMA200 (4H)")
        
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
        if any(is_price_in_zone(close, ob) for ob in obs) or any(is_price_in_zone(close, fvg) for fvg in fvgs):
            score += 10
            reasons.append("• در محدوده Order Block / FVG")
            
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
        
        return {
            "symbol": symbol, "direction": direction, "score": score,
            "close": close, "sl": sl, "tp1": tp1, "tp2": tp2,
            "sl_pct": sl_pct, "tp1_pct": tp1_pct, "tp2_pct": tp2_pct,
            "rsi": rsi, "volume_ratio": volume_ratio, "regime": regime,
            "reasons": reasons, "strong": score >= STRONG_SCORE,
        }
    except Exception as e:
        log(f"[ANALYZE ERROR] {symbol}: {str(e)}")
        return None


# ---------- MAIN EXECUTION ----------
def main():
    log("=== Crypto Signal Bot Started ===")
    coins = get_scan_coins()
    log(f"Scanning {len(coins)} coins...")
    
    signals = []
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        futures = {executor.submit(get_klines, c["symbol"], TIMEFRAME_MAIN): c for c in coins}
        for future in as_completed(futures):
            c = futures[future]
            try:
                df = future.result()
                if df is not None:
                    res = analyze_coin(df, c["symbol"])
                    if res:
                        signals.append(res)
            except Exception as e:
                log(f"[ERROR] {c['symbol']}: {str(e)}")
                
    signals.sort(key=lambda x: x["score"], reverse=True)
    top_signals = signals[:MAX_CONCURRENT_SIGNALS]
    
    if top_signals:
        syms_to_fetch = [s["symbol"] for s in top_signals]
        futures_data = fetch_futures_metrics_batch(syms_to_fetch)
        for sig in top_signals:
            f_metrics = futures_data.get(sig["symbol"], {"funding_rate": 0.0, "open_interest": 0.0, "oi_change": 0.0})
            sig.update(f_metrics)

    fng_val, fng_text = get_fear_greed_index()
    
    if not top_signals:
        report = f"📊 <b>Market Status Update</b>\n😱 ترس و طمع: {fng_val} ({fng_text})\n\nNo high-probability signals found in this scan cycle."
    else:
        report = f"📊 <b>Market Status Update</b>\n😱 ترس و طمع: {fng_val} ({fng_text})\n\nFound <b>{len(top_signals)}</b> signals:\n"
        for sig in top_signals:
            header_emoji = "🟢" if sig["direction"] == "BUY" else "🔴"
            signal_type = "سیگنال قوی (4H)" if sig["strong"] else "سیگنال معمولی (4H)"
            coin_hashtag = "#" + sig["symbol"].replace("USDT", "")
            
            risk_usd = 10.0
            price_risk_per_unit = abs(sig["close"] - sig["sl"])
            position_units = risk_usd / price_risk_per_unit if price_risk_per_unit > 0 else 0.0
            position_value = position_units * sig["close"]
            
            v_ratio = sig['volume_ratio']
            v_text = f"قوی {v_ratio:.2f}x" if v_ratio >= 1.5 else f"متوسط {v_ratio:.2f}x"

            msg = f"\n{header_emoji} <b>{signal_type}</b>\n\n"
            msg += f"نماد: <b>{coin_hashtag}</b>\n"
            msg += f"جهت: <b>{sig['direction']}</b>\n"
            msg += f"ورود: <code>{sig['close']:.4f}</code>\n\n"
            msg += f"🛑 SL: <code>{sig['sl']:.4f}</code> ({sig['sl_pct']:+.2f}%)\n"
            msg += f"🎯 TP1: <code>{sig['tp1']:.4f}</code> ({sig['tp1_pct']:+.2f}%)\n"
            msg += f"🎯 TP2: <code>{sig['tp2']:.4f}</code> ({sig['tp2_pct']:+.2f}%)\n\n"
            msg += f"پیشنهاد حجم (حساب $1000، ریسک 1%):\n"
            msg += f"🔹 <code>{position_units:.4f}</code> واحد (~$<code>{position_value:.2f}</code>)\n\n"
            msg += f"📊 امتیاز: <b>{sig['score']}/100</b> | RSI: {sig['rsi']:.1f}\n"
            msg += f"📦 حجم: {v_text}\n"
            msg += f"⚡ فاندینگ: <code>{sig['funding_rate']*100:.4f}%</code>\n"
            msg += f"💼 OI: <code>{format_num(sig['open_interest'])}</code> | {sig['oi_change']:+.1f}%\n"
            msg += f"🌊 رژیم بازار: {sig['regime']}\n"
            msg += f"😱 ترس و طمع: {fng_val}\n\n"
            msg += f"دلایل:\n" + "\n".join(sig["reasons"]) + "\n" + "—" * 20
            report += msg

    send_telegram(report)
    
    state_data = {"last_run": time.time(), "signals_count": len(signals)}
    save_json(STATE_FILE, state_data)
    
    log("=== Scan Cycle Completed ===")

if __name__ == "__main__":
    main()
    
