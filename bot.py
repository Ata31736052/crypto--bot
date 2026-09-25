# =========================================================
# Crypto Signal Bot - Clean & Optimized Pro Edition
# Data source: Binance (Independent Fixed List)
# Executed via GitHub Actions
# =========================================================

import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# =========================================================
# 1. SETTINGS & CONFIGURATION
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME_MAIN = "4h"     # تایم‌فریم اصلی تحلیل (بهترین انتخاب برای اسوینگ)
TIMEFRAME_SUB = "1h"      # تایم‌فریم تاییدیه چندگانه (Multi-TF)
KLINE_LIMIT = 500         # تعداد کندل‌ها برای محاسبات دقیق EMA200

MIN_SCORE = 62            # آستانه امتیاز متوازن برای کیفیت و تعداد سیگنال
MIN_VOLUME_RATIO = 1.20   # حداقل ۲۰٪ افزایش حجم نسبت به میانگین
MIN_24H_USDT_VOLUME = 5_000_000  # حداقل حجم معاملات ۲۴ ساعته (۵ میلیون دلار)

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.50   # حد زیان پویا بر اساس ATR
TP1_RR = 1.50             # ریسک به ریوارد تارگت اول
TP2_RR = 2.80             # ریسک به ریوارد تارگت دوم

STATE_FILE = "signals_state.json"
BINANCE_BASE = "https://data-api.binance.vision"


# =========================================================
# 2. UTILS & TELEGRAM
# =========================================================

def http_get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    return None


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] توکن یا چت‌آیدی تلگرام تنظیم نشده است.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        res = requests.post(url, json=payload, timeout=20)
        res.raise_for_status()
        return True
    except Exception as e:
        print(f"[ERROR] خطا در ارسال پیام تلگرام: {e}")
        return False


# =========================================================
# 3. MARKET COINS DISCOVERY & VOLUME FILTER
# =========================================================

def get_scan_coins():
    all_coins = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "TON", "ADA", "DOGE", "AVAX", "LINK",
        "DOT", "LTC", "BCH", "ETC", "XLM", "UNI", "FIL", "TRX", "ATOM", "NEAR",
        "AAVE", "SUI", "APT", "ARB", "OP", "SEI", "INJ", "TIA", "STX", "ALGO",
        "PEPE", "WIF", "BONK", "FLOKI", "SHIB", "MEME", "NOT", "ORDI", "BOME",
        "POL", "ASI", "RENDER", "ICP", "KAS", "IMX", "GRT", "HBAR", "ENA", 
        "PENDLE", "JUP", "PYTH", "W", "MANTA", "ALT", "STRK", "AXL", "PORTAL", 
        "AEVO", "REZ", "BB", "IO", "ZK", "LISTA", "BANANA", "DOGS", "CATI", 
        "HMSTR", "EIGEN", "SCR", "PNUT", "ACT", "GOAT", "CHZ", "SAND", "MANA", 
        "GALA", "ENJ", "AXS", "THETA", "FTM", "SNX", "CRV", "MKR", "COMP", 
        "1INCH", "SUSHI", "BAL", "ZRX", "LDO", "RPL", "SSV", "FXS", "DYDX", 
        "GMX", "PERP", "OCEAN", "AGIX", "RLC", "AR", "STORJ", "SC", "HOT", 
        "RVN", "ZIL", "IOST", "ONT", "ICX", "ZEC", "DASH", "KSM", "ZEN", "QTUM", 
        "NEXO", "BAT", "SKL", "MINA", "FLOW", "MASK", "AGLD", "API3", "SUPER", 
        "BICO", "GLMR", "MOVR", "ACA", "ASTR", "TLM", "DAR", "ALICE", "YGG", 
        "GHST", "VOXEL", "RARE", "PROS", "STG", "LPT", "HIGH", "CVX", "MDT", "POLS"
    ]
    
    unique_coins = sorted(list(set(all_coins)))

    tickers = http_get(f"{BINANCE_BASE}/api/v3/ticker/24hr")
    valid_volumes = {}
    if tickers:
        for t in tickers:
            sym = t.get("symbol")
            if sym and sym.endswith("USDT"):
                try:
                    valid_volumes[sym] = float(t.get("quoteVolume", 0))
                except:
                    pass

    info = http_get(f"{BINANCE_BASE}/api/v3/exchangeInfo")
    if not info:
        return [{"coin": c, "symbol": c + "USDT"} for c in unique_coins]

    binance_symbols = {
        item["symbol"] 
        for item in info.get("symbols", []) 
        if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT"
    }

    result = []
    for coin in unique_coins:
        symbol = coin + "USDT"
        if symbol in binance_symbols:
            vol_24h = valid_volumes.get(symbol, 0)
            if vol_24h >= MIN_24H_USDT_VOLUME:
                result.append({"coin": coin, "symbol": symbol})
            
    return result


# =========================================================
# 4. TECHNICAL INDICATORS & DATA
# =========================================================

def get_klines(symbol, interval):
    params = {"symbol": symbol, "interval": interval, "limit": KLINE_LIMIT}
    data = http_get(f"{BINANCE_BASE}/api/v3/klines", params=params)
    if not data or len(data) < 210:
        return None

    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"
    ]
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

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    df["volume_avg20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg20"]

    obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    df["obv"] = obv
    df["obv_ema"] = df["obv"].ewm(span=20, adjust=False).mean()

    return df


def get_btc_macro_trend():
    df_btc = get_klines("BTCUSDT", "1d")
    if df_btc is None or len(df_btc) < 200:
        return "BULLISH"
    
    ema200_btc = df_btc["close"].ewm(span=200, adjust=False).mean().iloc[-1]
    last_btc = df_btc.iloc[-1]["close"]
    
    return "BULLISH" if last_btc > ema200_btc else "BEARISH"


def check_1h_confirmation(symbol, direction):
    df_1h = get_klines(symbol, TIMEFRAME_SUB)
    if df_1h is None or len(df_1h) < 50:
        return False

    df_1h = add_indicators(df_1h)
    last_1h = df_1h.iloc[-1]
    
    if direction == "BUY":
        return last_1h["close"] > last_1h["ema21"] or last_1h["ema9"] > last_1h["ema21"]
    elif direction == "SELL":
        return last_1h["close"] < last_1h["ema21"] or last_1h["ema9"] < last_1h["ema21"]
            
    return False


# =========================================================
# 5. STRATEGY ENGINE
# =========================================================

def analyze_coin(df, symbol, btc_trend):
    df = add_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    price = float(last["close"])
    rsi = float(last["rsi"])
    volume_ratio = float(last["volume_ratio"])
    atr = float(last["atr"])

    if not np.isfinite(atr) or atr <= 0:
        return None

    buy_score, sell_score = 0, 0
    reasons_buy, reasons_sell = [], []

    if price > last["ema200"]:
        buy_score += 20
        reasons_buy.append("قیمت بالاتر از EMA200 (4H)")
    else:
        sell_score += 20
        reasons_sell.append("قیمت پایین‌تر از EMA200 (4H)")

    if last["ema9"] > last["ema21"]:
        buy_score += 15
        reasons_buy.append("تقاطع صعودی EMA9 و EMA21")
    else:
        sell_score += 15
        reasons_sell.append("تقاطع نزولی EMA9 و EMA21")

    if 45 <= rsi <= 70:
        buy_score += 15
        reasons_buy.append(f"مومنتوم مناسب RSI ({rsi:.1f})")
    elif rsi < 35:
        buy_score += 12
        reasons_buy.append(f"اشباع فروش RSI ({rsi:.1f})")

    if 30 <= rsi <= 55:
        sell_score += 15
        reasons_sell.append(f"مومنتوم نزولی RSI ({rsi:.1f})")
    elif rsi > 65:
        sell_score += 12
        reasons_sell.append(f"اشباع خرید RSI ({rsi:.1f})")

    if prev["macd"] <= prev["macd_signal"] and last["macd"] > last["macd_signal"]:
        buy_score += 15
        reasons_buy.append("تقاطع صعودی MACD")
    if prev["macd"] >= prev["macd_signal"] and last["macd"] < last["macd_signal"]:
        sell_score += 15
        reasons_sell.append("تقاطع نزولی MACD")

    if last["obv"] > last["obv_ema"]:
        buy_score += 10
        reasons_buy.append("جریان پول مثبت (OBV)")
    else:
        sell_score += 10
        reasons_sell.append("جریان پول منفی (OBV)")

    if volume_ratio >= MIN_VOLUME_RATIO:
        if last["close"] > last["open"]:
            buy_score += 15
            reasons_buy.append(f"افزایش حجم صعودی ({volume_ratio:.2f}x)")
        else:
            sell_score += 15
            reasons_sell.append(f"افزایش حجم نزولی ({volume_ratio:.2f}x)")

    direction, score, reasons = None, 0, []
    
    if buy_score >= MIN_SCORE and buy_score > sell_score:
        if btc_trend == "BEARISH" and symbol != "BTCUSDT":
            return None
        direction = "BUY"
        score = buy_score
        reasons = reasons_buy
    elif sell_score >= MIN_SCORE and sell_score > buy_score:
        direction = "SELL"
        score = sell_score
        reasons = reasons_sell
    else:
        return None

    if not check_1h_confirmation(symbol, direction):
        return None
    
    reasons.append("تاییدیه مومنتوم ۱ ساعته (Multi-TF)")
    score += 5

    if direction == "BUY":
        stop_loss = price - (atr * SL_ATR_MULTIPLIER)
        risk = price - stop_loss
        tp1 = price + (risk * TP1_RR)
        tp2 = price + (risk * TP2_RR)
    else:
        stop_loss = price + (atr * SL_ATR_MULTIPLIER)
        risk = stop_loss - price
        tp1 = price - (risk * TP1_RR)
        tp2 = price - (risk * TP2_RR)

    return {
        "signal": direction,
        "symbol": symbol,
        "entry": price,
        "score": score,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "reasons": reasons,
        "candle_time": last["close_time"].isoformat()
    }


# =========================================================
# 6. STATE HANDLING & MAIN RUNNER
# =========================================================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run_scan():
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] شروع اسکن بهینه بازار...")
    
    btc_macro_trend = get_btc_macro_trend()
    print(f"روند کلان بیت‌کوین: {btc_macro_trend}")

    coins = get_scan_coins()
    print(f"تعداد ارزهای واجد شرایط حجم: {len(coins)}")
    if not coins:
        return

    state = load_state()
    sent_count = 0

    for item in coins:
        symbol = item["symbol"]
        df = get_klines(symbol, TIMEFRAME_MAIN)
        if df is None:
            continue

        result = analyze_coin(df, symbol, btc_macro_trend)
        if not result:
            continue

        signal_key = f"{symbol}_{result['signal']}_{result['candle_time']}"
        if signal_key in state:
            continue

        emoji = "🟢" if result["signal"] == "BUY" else "🔴"
        msg = (
            f"{emoji} <b>سیگنال جدید (Pro Clean Edition)</b>\n\n"
            f"<b>نماد:</b> #{result['symbol'].replace('USDT', '')}\n"
            f"<b>جهت:</b> {result['signal']}\n"
            f"<b>نقطه ورود:</b> {result['entry']:.6g}\n\n"
            f"🛑 <b>حد زیان (SL):</b> {result['stop_loss']:.6g}\n"
            f"🎯 <b>تارگت اول (TP1):</b> {result['tp1']:.6g}\n"
            f"🎯 <b>تارگت دوم (TP2):</b> {result['tp2']:.6g}\n\n"
            f"📊 <b>امتیاز استراتژی:</b> {result['score']}/95\n"
            f"📈 <b>RSI:</b> {result['rsi']:.1f}\n"
            f"📦 <b>نسبت حجم:</b> {result['volume_ratio']:.2f}x\n\n"
            f"<b>دلایل تاییدیه:</b>\n" + "\n".join([f"• {r}" for r in result["reasons"]])
        )

        if send_telegram(msg):
            state[signal_key] = {"sent_at": datetime.now(timezone.utc).isoformat()}
            save_state(state)
            sent_count += 1
            print(f"[SENT] سیگنال {symbol} ارسال شد.")

        time.sleep(0.3)

    print(f"اسکن پایان یافت. سیگنال‌های ارسال‌شده: {sent_count}")


if __name__ == "__main__":
    run_scan()
