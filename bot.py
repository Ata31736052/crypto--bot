# ==============================
# Crypto Signal Bot
# Nobitex-listed coins
# Market data: Binance
# Timeframe: 4H
# ==============================

import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta


# =========================================================
# 1. TELEGRAM
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("TELEGRAM_TOKEN یا TELEGRAM_CHAT_ID تنظیم نشده است.")


# =========================================================
# 2. SETTINGS
# =========================================================

TIMEFRAME = "4h"
KLINE_LIMIT = 250

MIN_SCORE = 70
MIN_VOLUME_RATIO = 1.20

ACCOUNT_SIZE_USDT = 1000.0
RISK_PER_TRADE = 0.01
MAX_POSITION_USDT = 1000.0

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.30

TP1_RR = 1.50
TP2_RR = 2.80

STATE_FILE = "signals_state.json"

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# =========================================================
# 3. FIXED NOBITEX COIN LIST
# =========================================================
#
# این قسمت عمداً ثابت است.
# داده‌های قیمت و کندل از Binance گرفته می‌شوند.
#
# مثال:
# BTC  -> BTCUSDT
# ETH  -> ETHUSDT
# TON  -> TONUSDT
#
# لیست نهایی باید با فهرست فعلی نوبیتکس تطبیق داده شود.
#

REQUESTED_COINS = [
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "TON",
    "ADA",
    "DOGE",
    "AVAX",
    "LINK",
    "DOT",
    "LTC",
    "BCH",
    "ETC",
    "XLM",
    "UNI",
    "FIL",
    "TRX",
    "ATOM",
    "NEAR",
    "AAVE",
    "SUI",
    "APT",
    "ARB",
    "OP",
    "SEI",
    "INJ",
    "TIA",
    "STX",
    "ALGO",
    "EGLD",
    "ROSE",
    "MINA",
    "IMX",
    "MNT",
    "RON",
    "CELO",
    "FLOW",
    "FET",
    "TAO",
    "AKT",
    "PEPE",
    "WIF",
    "BONK",
    "FLOKI",
    "SHIB",
    "MEME",
    "NOT",
    "ORDI",
    "BOME",
    "RUNE",
    "ICP",
    "KAS",
    "JUP"
]


# =========================================================
# 4. BINANCE
# =========================================================

BINANCE_BASE = "https://data-api.binance.vision"

EXCHANGE_INFO_URL = f"{BINANCE_BASE}/api/v3/exchangeInfo"
KLINES_URL = f"{BINANCE_BASE}/api/v3/klines"


# =========================================================
# 5. HTTP
# =========================================================

def http_get(url, params=None, retries=3):

    for attempt in range(retries):

        try:
            response = requests.get(
                url,
                params=params,
                timeout=20
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            print(
                f"HTTP error ({attempt + 1}/{retries}): {e}"
            )

            if attempt < retries - 1:
                time.sleep(2)

    return None


# =========================================================
# 6. TELEGRAM
# =========================================================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        response.raise_for_status()

        print("Telegram message sent.")

        return True

    except Exception as e:

        print(f"Telegram error: {e}")

        return False


# =========================================================
# 7. GET BINANCE SYMBOLS
# =========================================================

def get_binance_symbols():

    data = http_get(EXCHANGE_INFO_URL)

    if not data:
        return set()

    symbols = set()

    for item in data.get("symbols", []):

        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
        ):

            symbols.add(item["symbol"])

    return symbols


# =========================================================
# 8. BUILD SCAN LIST
# =========================================================

def get_scan_coins():

    binance_symbols = get_binance_symbols()

    result = []

    for coin in REQUESTED_COINS:

        symbol = coin.upper() + "USDT"

        if symbol in binance_symbols:

            result.append(
                {
                    "coin": coin.upper(),
                    "symbol": symbol
                }
            )

        else:

            print(
                f"[SKIP] {coin}: "
                f"{symbol} not available on Binance"
            )

    return result


# =========================================================
# 9. GET 4H CANDLES
# =========================================================

def get_klines(symbol):

    params = {
        "symbol": symbol,
        "interval": TIMEFRAME,
        "limit": KLINE_LIMIT
    }

    data = http_get(
        KLINES_URL,
        params=params
    )

    if not data:
        return None

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore"
    ]

    df = pd.DataFrame(
        data,
        columns=columns
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for col in numeric_columns:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True
    )

    # حذف کندل در حال تشکیل
    now = pd.Timestamp.now(tz="UTC")

    df = df[
        df["close_time"] <= now
    ].copy()

    return df.reset_index(drop=True)


# =========================================================
# 10. INDICATORS
# =========================================================

def add_indicators(df):

    df = df.copy()

    # EMA
    df["ema9"] = df["close"].ewm(
        span=9,
        adjust=False
    ).mean()

    df["ema21"] = df["close"].ewm(
        span=21,
        adjust=False
    ).mean()

    df["ema50"] = df["close"].ewm(
        span=50,
        adjust=False
    ).mean()

    df["ema200"] = df["close"].ewm(
        span=200,
        adjust=False
    ).mean()

    # RSI
    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # MACD
    ema12 = df["close"].ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = df["close"].ewm(
        span=26,
        adjust=False
    ).mean()

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df["macd"].ewm(
        span=9,
        adjust=False
    ).mean()

    df["macd_hist"] = (
        df["macd"] -
        df["macd_signal"]
    )

    # ATR
    high_low = df["high"] - df["low"]

    high_close = (
        df["high"] -
        df["close"].shift()
    ).abs()

    low_close = (
        df["low"] -
        df["close"].shift()
    ).abs()

    tr = pd.concat(
        [
            high_low,
            high_close,
            low_close
        ],
        axis=1
    ).max(axis=1)

    df["atr"] = tr.rolling(
        ATR_PERIOD
    ).mean()

    # Volume average
    df["volume_avg20"] = df["volume"].rolling(
        20
    ).mean()

    df["volume_ratio"] = (
        df["volume"] /
        df["volume_avg20"]
    )

    return df


# =========================================================
# 11. CANDLE PATTERNS
# =========================================================

def candle_patterns(row):

    body = abs(
        row["close"] - row["open"]
    )

    candle_range = (
        row["high"] -
        row["low"]
    )

    if candle_range <= 0:
        return False, False

    upper_wick = (
        row["high"] -
        max(row["open"], row["close"])
    )

    lower_wick = (
        min(row["open"], row["close"]) -
        row["low"]
    )

    bullish_pin = (
        lower_wick > body * 2
        and lower_wick > upper_wick
    )

    bearish_pin = (
        upper_wick > body * 2
        and upper_wick > lower_wick
    )

    return bullish_pin, bearish_pin


# =========================================================
# 12. ANALYZE
# =========================================================

def analyze_coin(df, symbol):

    if df is None or len(df) < 210:

        return None

    df = add_indicators(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    buy_score = 0
    sell_score = 0

    reasons_buy = []
    reasons_sell = []

    price = float(last["close"])

    # -----------------------------------------------------
    # EMA
    # -----------------------------------------------------

    if last["ema9"] > last["ema21"]:

        buy_score += 15
        reasons_buy.append("EMA9>EMA21")

    else:

        sell_score += 15
        reasons_sell.append("EMA9<EMA21")

    if last["ema21"] > last["ema50"]:

        buy_score += 10
        reasons_buy.append("EMA21>EMA50")

    else:

        sell_score += 10
        reasons_sell.append("EMA21<EMA50")

    if last["close"] > last["ema200"]:

        buy_score += 10
        reasons_buy.append("Price>EMA200")

    else:

        sell_score += 10
        reasons_sell.append("Price<EMA200")

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    rsi = float(last["rsi"])

    if rsi < 30:

        buy_score += 15
        reasons_buy.append(
            f"RSI oversold ({rsi:.1f})"
        )

    elif rsi <= 38:

        buy_score += 15
        reasons_buy.append(
            f"RSI recovery zone ({rsi:.1f})"
        )

    elif rsi <= 55:

        buy_score += 7

    if rsi > 70:

        sell_score += 15
        reasons_sell.append(
            f"RSI overbought ({rsi:.1f})"
        )

    elif rsi >= 62:

        sell_score += 15
        reasons_sell.append(
            f"RSI high ({rsi:.1f})"
        )

    elif rsi >= 55:

        sell_score += 7

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    macd = float(last["macd"])
    signal = float(last["macd_signal"])

    prev_macd = float(prev["macd"])
    prev_signal = float(prev["macd_signal"])

    bullish_cross = (
        prev_macd <= prev_signal
        and macd > signal
    )

    bearish_cross = (
        prev_macd >= prev_signal
        and macd < signal
    )

    if bullish_cross:

        buy_score += 20
        reasons_buy.append("MACD bullish cross")

    elif macd > signal:

        buy_score += 10
        reasons_buy.append("MACD bullish")

    if bearish_cross:

        sell_score += 20
        reasons_sell.append("MACD bearish cross")

    elif macd < signal:

        sell_score += 10
        reasons_sell.append("MACD bearish")

    # -----------------------------------------------------
    # VOLUME
    # -----------------------------------------------------

    volume_ratio = float(
        last["volume_ratio"]
    )

    bullish_candle = (
        last["close"] > last["open"]
    )

    bearish_candle = (
        last["close"] < last["open"]
    )

    if volume_ratio >= MIN_VOLUME_RATIO:

        if bullish_candle:

            buy_score += 15

            reasons_buy.append(
                f"Volume {volume_ratio:.2f}x"
            )

        elif bearish_candle:

            sell_score += 15

            reasons_sell.append(
                f"Volume {volume_ratio:.2f}x"
            )

    # -----------------------------------------------------
    # CANDLE PATTERN
    # -----------------------------------------------------

    bullish_pin, bearish_pin = candle_patterns(last)

    if bullish_pin:

        buy_score += 10
        reasons_buy.append("Bullish pinbar")

    if bearish_pin:

        sell_score += 10
        reasons_sell.append("Bearish pinbar")

    # -----------------------------------------------------
    # FINAL DECISION
    # -----------------------------------------------------

    direction = None
    score = 0
    reasons = []

    if (
        buy_score >= MIN_SCORE
        and buy_score > sell_score
    ):

        direction = "BUY"
        score = buy_score
        reasons = reasons_buy

    elif (
        sell_score >= MIN_SCORE
        and sell_score > buy_score
    ):

        direction = "SELL"
        score = sell_score
        reasons = reasons_sell

    else:

        return {
            "signal": None,
            "symbol": symbol,
            "price": price,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "buy_score": buy_score,
            "sell_score": sell_score
        }

    # -----------------------------------------------------
    # SL / TP
    # -----------------------------------------------------

    atr = float(last["atr"])

    if not np.isfinite(atr) or atr <= 0:
        return None

    if direction == "BUY":

        stop_loss = (
            price -
            atr * SL_ATR_MULTIPLIER
        )

        risk = price - stop_loss

        tp1 = price + risk * TP1_RR
        tp2 = price + risk * TP2_RR

    else:

        stop_loss = (
            price +
            atr * SL_ATR_MULTIPLIER
        )

        risk = stop_loss - price

        tp1 = price - risk * TP1_RR
        tp2 = price - risk * TP2_RR

    return {
        "signal": direction,
        "symbol": symbol,
        "price": price,
        "score": score,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "entry": price,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "reasons": reasons,
        "candle_time": last["close_time"].isoformat()
    }


# =========================================================
# 13. STATE
# =========================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# 14. FORMAT TELEGRAM
# =========================================================

def format_signal(result):

    direction = result["signal"]

    emoji = "🟢" if direction == "BUY" else "🔴"

    symbol = result["symbol"]

    message = f"""
{emoji} <b>CRYPTO SIGNAL</b>

<b>{symbol}</b>
<b>Signal:</b> {direction}

⏱ <b>Timeframe:</b> 4H

💰 <b>Entry:</b> {result["entry"]:.8g}

🛑 <b>Stop Loss:</b> {result["stop_loss"]:.8g}

🎯 <b>TP1:</b> {result["tp1"]:.8g}

🎯 <b>TP2:</b> {result["tp2"]:.8g}

📊 <b>Score:</b> {result["score"]}/100

📈 <b>RSI:</b> {result["rsi"]:.2f}

📦 <b>Volume:</b> {result["volume_ratio"]:.2f}x

<b>Reasons:</b>
"""

    for reason in result["reasons"]:

        message += f"• {reason}\n"

    message += (
        "\n⚠️ این پیام فقط خروجی تحلیلی ربات است "
        "و اجرای معامله خودکار انجام نمی‌دهد."
    )

    return message


# =========================================================
# 15. MAIN
# =========================================================

def main():

    print("=" * 60)
    print("CRYPTO SIGNAL BOT")
    print("Data source: Binance")
    print("Timeframe: 4H")
    print("=" * 60)

    coins = get_scan_coins()

    print(
        f"Coins to scan: {len(coins)}"
    )

    if not coins:

        raise RuntimeError(
            "هیچ ارز مشترکی بین لیست و Binance پیدا نشد."
        )

    state = load_state()

    sent_count = 0

    for item in coins:

        coin = item["coin"]
        symbol = item["symbol"]

        print(
            f"\nScanning {symbol}..."
        )

        try:

            df = get_klines(symbol)

            if df is None:

                print("No data.")

                continue

            result = analyze_coin(
                df,
                symbol
            )

            if result is None:

                print("Analysis failed.")

                continue

            print(
                f"RSI={result.get('rsi', 0):.2f} | "
                f"BUY={result.get('buy_score', result.get('score', 0))} | "
                f"SELL={result.get('sell_score', 0)}"
            )

            if not result["signal"]:

                print("No signal.")

                continue

            signal_key = (
                f"{symbol}_"
                f"{result['signal']}_"
                f"{result['candle_time']}"
            )

            if signal_key in state:

                print(
                    "Signal already sent."
                )

                continue

            message = format_signal(
                result
            )

            if send_telegram(message):

                state[signal_key] = {
                    "sent_at": datetime.now(
                        timezone.utc
                    ).isoformat()
                }

                save_state(state)

                sent_count += 1

        except Exception as e:

            print(
                f"ERROR {symbol}: {e}"
            )

    print("\n" + "=" * 60)

    print(
        f"Signals sent: {sent_count}"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
