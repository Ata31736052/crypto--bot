# =========================================================
# Crypto Signal Bot - 4H Strategy for Nobitex-listed Coins
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
# 1. SETTINGS & TELEGRAM CONFIG
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME = "4h"
KLINE_LIMIT = 300  # تعداد کافی کندل برای محاسبه دقیق EMA200

MIN_SCORE = 75           # حداقل امتیاز برای صدور سیگنال
MIN_VOLUME_RATIO = 1.30  # حداقل ۳۰٪ افزایش حجم نسبت به میانگین ۲۰ کندل

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.50
TP1_RR = 1.50
TP2_RR = 2.80

STATE_FILE = "signals_state.json"
BINANCE_BASE = "https://data-api.binance.vision"


# =========================================================
# 2. HTTP & TELEGRAM UTILS
# =========================================================

def http_get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
    return None


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] کلیدهای تلگرام (TELEGRAM_TOKEN یا TELEGRAM_CHAT_ID) تنظیم نشده‌اند.")
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
        print(f"[ERROR] Telegram send failed: {e}")
        return False


# =========================================================
# 3. NOBITEX COINS & BINANCE SYMBOLS (EXPANDED LIST)
# =========================================================

def get_nobitex_coins():
    """دریافت لیست جامع ارزهای فعال نوبیتکس با پشتیبانی کامل از لیست پیش‌فرض گسترش‌یافته"""
    url = "https://api.nobitex.ir/v2/market/stats"
    data = http_get(url)
    
    # لیست پیش‌فرض گسترش‌یافته به ۶۰ ارز اصلی نوبیتکس
    default_coins = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "TON", "ADA", "DOGE", "AVAX", "LINK",
        "DOT", "LTC", "BCH", "ETC", "XLM", "UNI", "FIL", "TRX", "ATOM", "NEAR",
        "AAVE", "SUI", "APT", "ARB", "OP", "SEI", "INJ", "TIA", "STX", "ALGO",
        "EGLD", "ROSE", "MINA", "IMX", "MNT", "RON", "CELO", "FLOW", "TAO", "AKT",
        "PEPE", "WIF", "BONK", "FLOKI", "SHIB", "MEME", "NOT", "ORDI", "BOME", "RUNE",
        "ICP", "JUP", "POL", "ASI", "KSM", "SAND", "MANA", "CRV", "LDO", "FET"
    ]
    
    if not data or "stats" not in data:
        print(f"[WARNING] عدم اتصال به API نوبیتکس. استفاده از لیست پیش‌فرض گسترش‌یافته ({len(default_coins)} ارز).")
        return default_coins

    coins = set()
    for market in data["stats"].keys():
        if market.endswith("-usdt") or market.endswith("-rls"):
            coins.add(market.split("-")[0].upper())

    # نگاشت تغییر نام برندها در بایننس
    mapping = {"MATIC": "POL", "FET": "ASI"}
    updated_coins = [mapping.get(c, c) for c in coins]

    # در صورت کوچک بودن لیست دریافتی، از لیست کامل استفاده کن
    if len(updated_coins) < 20:
        return default_coins

    return list(set(updated_coins))


def get_scan_coins():
    info = http_get(f"{BINANCE_BASE}/api/v3/exchangeInfo")
    if not info:
        print("[ERROR] امکان دریافت اطلاعات بازار از بایننس وجود ندارد.")
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
# 4. KLINES & INDICATORS
# =========================================================

def get_klines(symbol):
    params = {
        "symbol": symbol,
        "interval": TIMEFRAME,
        "limit": KLINE_LIMIT
    }
    
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

    # حذف کندل آخری که هنوز بسته نشده است
    df = df.iloc[:-1].copy()
    return df.reset_index(drop=True)


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

    return df


# =========================================================
# 5. STRATEGY ANALYSIS WITH ADVANCED FILTERS
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

    # --- EMA Trend Filters ---
    if price > last["ema200"]:
        buy_score += 20
        reasons_buy.append("روند کلی صعودی (Price > EMA200)")
    else:
        sell_score += 20
        reasons_sell.append("روند کلی نزولی (Price < EMA200)")

    if last["ema9"] > last["ema21"]:
        buy_score += 15
        reasons_buy.append("تقاطع کوتاه‌مدت صعودی (EMA9 > EMA21)")
    else:
        sell_score += 15
        reasons_sell.append("تقاطع کوتاه‌مدت نزولی (EMA9 < EMA21)")

    # --- RSI Logic ---
    if 48 <= rsi <= 65:
        buy_score += 20
        reasons_buy.append(f"قدرت مناسب خریداران (RSI: {rsi:.1f})")
    elif rsi < 30:
        buy_score += 15
        reasons_buy.append(f"اشباع فروش (RSI: {rsi:.1f})")

    if 35 <= rsi <= 52:
        sell_score += 20
        reasons_sell.append(f"تسلط فروشندگان (RSI: {rsi:.1f})")
    elif rsi > 70:
        sell_score += 15
        reasons_sell.append(f"اشباع خرید (RSI: {rsi:.1f})")

    # --- MACD Cross ---
    if prev["macd"] <= prev["macd_signal"] and last["macd"] > last["macd_signal"]:
        buy_score += 25
        reasons_buy.append("تقاطع صعودی MACD")
    elif last["macd"] > last["macd_signal"]:
        buy_score += 10

    if prev["macd"] >= prev["macd_signal"] and last["macd"] < last["macd_signal"]:
        sell_score += 25
        reasons_sell.append("تقاطع نزولی MACD")
    elif last["macd"] < last["macd_signal"]:
        sell_score += 10

    # --- Volume Confirmation ---
    if volume_ratio >= MIN_VOLUME_RATIO:
        if last["close"] > last["open"]:
            buy_score += 20
            reasons_buy.append(f"افزایش حجم صعودی ({volume_ratio:.2f}x)")
        else:
            sell_score += 20
            reasons_sell.append(f"افزایش حجم نزولی ({volume_ratio:.2f}x)")

    # =========================================================
    # ADVANCED CANDLE & OVEREXTENSION FILTERS
    # =========================================================

    bullish_candle = last["close"] > last["open"]
    bearish_candle = last["close"] < last["open"]

    # ۱. فیلتر هم‌جهتی رنگ کندل با سیگنال
    if not bullish_candle:
        buy_score -= 15  # کسر امتیاز در صورت قرمز بودن کندل پایانی در خرید
    if not bearish_candle:
        sell_score -= 15 # کسر امتیاز در صورت سبز بودن کندل پایانی در فروش

    # ۲. فیلتر نسبت بدنه کندل به کل رنج (جلوگیری از ورود روی کندل‌های بلاتکلیف)
    candle_range = last["high"] - last["low"]
    candle_body = abs(last["close"] - last["open"])

    if candle_range > 0 and (candle_body / candle_range) < 0.35:
        buy_score -= 10
        sell_score -= 10

    # ۳. فیلتر فاصله بیش از حد قیمت از EMA200 (Overextended Risk)
    ema200_dist = abs(price - last["ema200"])
    if ema200_dist > (atr * 3.5):
        if price > last["ema200"]:
            buy_score -= 15
        else:
            sell_score -= 15

    # =========================================================
    # FINAL DECISION & TARGET CALCULATION
    # =========================================================

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
# 6. STATE MANAGEMENT
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


# =========================================================
# 7. MAIN RUNNER
# =========================================================

def run_scan():
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] شروع اسکن بازار...")
    
    coins = get_scan_coins()
    print(f"تعداد ارزهای مشترک با بایننس: {len(coins)}")
    
    if not coins:
        print("[ERROR] هیچ ارزی برای اسکن پیدا نشد.")
        return

    state = load_state()
    sent_count = 0

    for item in coins:
        symbol = item["symbol"]
        df = get_klines(symbol)
        
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
            f"{emoji} <b>سیگنال جدید ۴ ساعته (4H)</b>\n\n"
            f"<b>نماد:</b> #{result['symbol'].replace('USDT', '')}\n"
            f"<b>جهت:</b> {result['signal']}\n"
            f"<b>نقطه ورود:</b> {result['entry']:.6g}\n\n"
            f"🛑 <b>حد زیان (SL):</b> {result['stop_loss']:.6g}\n"
            f"🎯 <b>تارگت اول (TP1):</b> {result['tp1']:.6g}\n"
            f"🎯 <b>تارگت دوم (TP2):</b> {result['tp2']:.6g}\n\n"
            f"📊 <b>امتیاز استراتژی:</b> {result['score']}/100\n"
            f"📈 <b>شاخص RSI:</b> {result['rsi']:.1f}\n"
            f"📦 <b>نسبت حجم:</b> {result['volume_ratio']:.2f}x\n\n"
            f"<b>دلایل ورود:</b>\n" + "\n".join([f"• {r}" for r in result["reasons"]])
        )

        if send_telegram(msg):
            state[signal_key] = {"sent_at": datetime.now(timezone.utc).isoformat()}
            save_state(state)
            sent_count += 1
            print(f"[SENT] سیگنال {symbol} ارسال شد.")

        time.sleep(0.1)

    print(f"اسکن پایان یافت. سیگنال‌های جدید فرستاده‌شده: {sent_count}")


if __name__ == "__main__":
    run_scan()
        
