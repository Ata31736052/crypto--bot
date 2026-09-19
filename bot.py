# =========================================================
# Crypto Signal Bot - Professional Edition (4H + Multi-TF + OBV)
# Data source: Binance
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

TIMEFRAME = "4h"          # تایم‌فریم اصلی تحلیل
KLINE_LIMIT = 300         # تعداد کندل‌ها برای محاسبات سنگین

MIN_SCORE = 75            # حداقل امتیاز سخت‌گیرانه‌تر برای سیگنال‌های باکیفیت
MIN_VOLUME_RATIO = 1.30   # حداقل ۳۰٪ افزایش حجم نسبت به میانگین

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.50   # حد زیان پویا بر اساس ATR
TP1_RR = 1.60             # ریسک به ریوارد تارگت اول
TP2_RR = 3.00             # ریسک به ریوارد تارگت دوم

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
# 3. MARKET COINS DISCOVERY
# =========================================================

def get_nobitex_coins():
    url = "https://api.nobitex.ir/v2/market/stats"
    data = http_get(url)
    
    default_coins = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "TON", "ADA", "DOGE", "AVAX", "LINK",
        "DOT", "LTC", "BCH", "ETC", "XLM", "UNI", "FIL", "TRX", "ATOM", "NEAR",
        "AAVE", "SUI", "APT", "ARB", "OP", "SEI", "INJ", "TIA", "STX", "ALGO",
        "PEPE", "WIF", "BONK", "FLOKI", "SHIB", "MEME", "NOT", "ORDI", "BOME"
    ]
    
    if not data or "stats" not in data:
        return default_coins

    coins = set()
    for market in data["stats"].keys():
        if market.endswith("-usdt") or market.endswith("-rls"):
            coins.add(market.split("-")[0].upper())

    mapping = {"MATIC": "POL", "FET": "ASI"}
    updated_coins = [mapping.get(c, c) for c in coins]
    return list(set(updated_coins)) if len(updated_coins) >= 20 else default_coins


def get_scan_coins():
    info = http_get(f"{BINANCE_BASE}/api/v3/exchangeInfo")
    if not info:
        return []

    binance_symbols = {
        item["symbol"] 
        for item in info.get("symbols", []) 
        if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT"
    }

    nobitex_coins = get_nobitex_coins()
    result = []
    for coin in nobitex_coins:
        symbol = coin + "USDT"
        if symbol in binance_symbols:
            result.append({"coin": coin, "symbol": symbol})
            
    return result


# =========================================================
# 4. ADVANCED DATA & INDICATORS (WITH OBV & MULTI-TF)
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

    # Moving Averages
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # ATR & Volume
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    df["volume_avg20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg20"]

    # OBV (On-Balance Volume)
    obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    df["obv"] = obv
    df["obv_ema"] = df["obv"].ewm(span=20, adjust=False).mean()

    return df


def check_daily_trend(symbol):
    """بررسی روند تایم‌فریم روزانه برای تایید چندتایم‌فریمی"""
    df_daily = get_klines(symbol, "1d")
    if df_daily is None or len(df_daily) < 50:
        return "NEUTRAL"
    
    last_d = df_daily.iloc[-1]
    ema50_d = last_d["close"] # simplified or calculated
    ema200_d = df_daily["close"].ewm(span=200, adjust=False).mean().iloc[-1]
    
    if last_d["close"] > ema200_d:
        return "BULLISH"
    elif last_d["close"] < ema200_d:
        return "BEARISH"
    return "NEUTRAL"


# =========================================================
# 5. PROFESSIONAL STRATEGY ENGINE
# =========================================================

def analyze_coin(df, symbol):
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

    # --- 1. Multi-Timeframe Trend Filter ---
    daily_trend = check_daily_trend(symbol)
    if daily_trend == "BULLISH":
        buy_score += 15
        reasons_buy.append("روند روزانه صعودی (Multi-TF Filter)")
    elif daily_trend == "BEARISH":
        sell_score += 15
        reasons_sell.append("روند روزانه نزولی (Multi-TF Filter)")

    # --- 2. EMA Trend & Crossover ---
    if price > last["ema200"]:
        buy_score += 15
        reasons_buy.append("قیمت بالاتر از EMA200")
    else:
        sell_score += 15
        reasons_sell.append("قیمت پایین‌تر از EMA200")

    if last["ema9"] > last["ema21"]:
        buy_score += 10
        reasons_buy.append("تقاطع EMA9 و EMA21 صعودی")
    else:
        sell_score += 10
        reasons_sell.append("تقاطع EMA9 و EMA21 نزولی")

    # --- 3. RSI Momentum ---
    if 50 <= rsi <= 68:
        buy_score += 15
        reasons_buy.append(f"مومنتوم صعودی RSI ({rsi:.1f})")
    elif rsi < 32:
        buy_score += 15
        reasons_buy.append(f"اشباع فروش RSI ({rsi:.1f})")

    if 32 <= rsi <= 50:
        sell_score += 15
        reasons_sell.append(f"مومنتوم نزولی RSI ({rsi:.1f})")
    elif rsi > 68:
        sell_score += 15
        reasons_sell.append(f"اشباع خرید RSI ({rsi:.1f})")

    # --- 4. MACD & OBV Confirmation ---
    if prev["macd"] <= prev["macd_signal"] and last["macd"] > last["macd_signal"]:
        buy_score += 15
        reasons_buy.append("تقاطع صعودی MACD")
    if prev["macd"] >= prev["macd_signal"] and last["macd"] < last["macd_signal"]:
        sell_score += 15
        reasons_sell.append("تقاطع نزولی MACD")

    if last["obv"] > last["obv_ema"]:
        buy_score += 10
        reasons_buy.append("تایید جریان پول (OBV صعودی)")
    else:
        sell_score += 10
        reasons_sell.append("تایید خروج پول (OBV نزولی)")

    # --- 5. Volume Spike & Body Strength Filter ---
    candle_body = abs(last["close"] - last["open"])
    candle_range = last["high"] - last["low"]
    body_ratio = candle_body / candle_range if candle_range > 0 else 0

    if volume_ratio >= MIN_VOLUME_RATIO and body_ratio >= 0.50:
        if last["close"] > last["open"]:
            buy_score += 20
            reasons_buy.append(f"حجم بالا ({volume_ratio:.2f}x) و بدنه کندل پرقدرت")
        else:
            sell_score += 20
            reasons_sell.append(f"حجم بالا ({volume_ratio:.2f}x) و فشار فروش سنگین")

    # --- 6. Breakout Structure Check ---
    recent_high = df["high"].iloc[-11:-1].max()
    recent_low = df["low"].iloc[-11:-1].min()

    if last["close"] > recent_high:
        buy_score += 15
        reasons_buy.append("شکست سقف ساختار (Breakout)")
    if last["close"] < recent_low:
        sell_score += 15
        reasons_sell.append("شکست کف ساختار (Breakdown)")

    # --- Decision Core ---
    direction, score, reasons = None, 0, []
    
    if buy_score >= MIN_SCORE and buy_score > sell_score:
        direction = "BUY"
        score = buy_score
        reasons = reasons_buy
        stop_loss = price - (atr * SL_ATR_MULTIPLIER)
        risk = price - stop_loss
        tp1 = price + (risk * TP1_RR)
        tp2 = price + (risk * TP2_RR)
        
    elif sell_score >= MIN_SCORE and sell_score > buy_score:
        direction = "SELL"
        score = sell_score
        reasons = reasons_sell
        stop_loss = price + (atr * SL_ATR_MULTIPLIER)
        risk = stop_loss - price
        tp1 = price - (risk * TP1_RR)
        tp2 = price - (risk * TP2_RR)
        
    else:
        return None

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
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] شروع اسکن حرفه‌ای بازار (4H + Multi-TF)...")
    
    coins = get_scan_coins()
    print(f"تعداد ارزهای قابل بررسی: {len(coins)}")
    if not coins:
        return

    state = load_state()
    sent_count = 0

    for item in coins:
        symbol = item["symbol"]
        df = get_klines(symbol, TIMEFRAME)
        if df is None:
            continue

        result = analyze_coin(df, symbol)
        if not result:
            continue

        signal_key = f"{symbol}_{result['signal']}_{result['candle_time']}"
        if signal_key in state:
            continue

        emoji = "🟢" if result["signal"] == "BUY" else "🔴"
        msg = (
            f"{emoji} <b>سیگنال حرفه‌ای ۴ ساعته (Pro)</b>\n\n"
            f"<b>نماد:</b> #{result['symbol'].replace('USDT', '')}\n"
            f"<b>جهت:</b> {result['signal']}\n"
            f"<b>نقطه ورود:</b> {result['entry']:.6g}\n\n"
            f"🛑 <b>حد زیان (SL):</b> {result['stop_loss']:.6g}\n"
            f"🎯 <b>تارگت اول (TP1):</b> {result['tp1']:.6g}\n"
            f"🎯 <b>تارگت دوم (TP2):</b> {result['tp2']:.6g}\n\n"
            f"📊 <b>امتیاز کیفیت:</b> {result['score']}/100\n"
            f"📈 <b>RSI:</b> {result['rsi']:.1f}\n"
            f"📦 <b>نسبت حجم:</b> {result['volume_ratio']:.2f}x\n\n"
            f"<b>دلایل تاییدیه:</b>\n" + "\n".join([f"• {r}" for r in result["reasons"]])
        )

        if send_telegram(msg):
            state[signal_key] = {"sent_at": datetime.now(timezone.utc).isoformat()}
            save_state(state)
            sent_count += 1
            print(f"[SENT] سیگنال حرفه‌ای {symbol} ارسال شد.")

        time.sleep(0.2)

    print(f"اسکن پایان یافت. سیگنال‌های ارسال‌شده: {sent_count}")


if __name__ == "__main__":
    run_scan()
    
