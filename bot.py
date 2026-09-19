# ============================================================
# 🤖 Crypto 4H Signal Bot - Final Pro
# ============================================================
# Strategy:
# 4H closed candle + RSI + MACD + EMA + Volume + ATR
# + Price Action + BTC Daily Trend + Duplicate Protection
#
# Telegram:
# TELEGRAM_TOKEN
# TELEGRAM_CHAT_ID
#
# Risk settings:
# ACCOUNT_SIZE_USDT = 1000
# RISK_PER_TRADE = 0.01   # 1%
#
# IMPORTANT:
# This bot generates analytical signals.
# It does NOT place trades automatically.
# ============================================================

import json
import time
import os
import ssl
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "signals_state.json"

TIMEFRAME = "4h"
KLINE_LIMIT = 250

# مدیریت ریسک
ACCOUNT_SIZE_USDT = 1000.0
RISK_PER_TRADE = 0.01       # 1 درصد
MAX_POSITION_USDT = 1000.0

# حداقل امتیاز لازم
MIN_SCORE = 70

# حداقل حجم نسبت به میانگین
MIN_VOLUME_RATIO = 1.20

# ATR
ATR_PERIOD = 14

# نسبت‌های حد ضرر و سود
SL_ATR_MULTIPLIER = 1.30
TP1_RR = 1.50
TP2_RR = 2.80

# اگر True باشد در صورت نبود سیگنال گزارش تلگرام می‌فرستد
SEND_NO_SIGNAL_REPORT = False

# منطقه زمانی ایران
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

# ------------------------------------------------------------
# COINS
# ------------------------------------------------------------

REQUESTED_COINS = [
    "BTC", "ETH", "SOL", "BNB", "XRP",
    "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "ETC", "XLM", "UNI",
    "FIL", "TRX", "ATOM", "NEAR", "AAVE",
    "SUI", "APT", "ARB", "OP", "SEI",
    "INJ", "TIA", "STX", "ALGO", "EGLD",
    "ROSE", "MINA", "IMX", "MNT", "RON",
    "CELO", "FLOW", "FET", "TAO", "AKT",
    "PEPE", "WIF", "BONK", "FLOKI", "SHIB",
    "MEME", "NOT", "ORDI", "BOME",
    "RUNE", "ICP", "KAS", "JUP"
]

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def http_get(url, timeout=15, retries=3):

    for attempt in range(retries):

        try:

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 CryptoSignalBot/1.0"
                }
            )

            with urllib.request.urlopen(
                req,
                context=CTX,
                timeout=timeout
            ) as response:

                raw = response.read().decode("utf-8")
                return json.loads(raw)

        except Exception as e:

            if attempt == retries - 1:
                print(f"HTTP ERROR: {url}")
                print(e)

            time.sleep(0.7)

    return None


# ------------------------------------------------------------
# BINANCE SYMBOLS
# ------------------------------------------------------------

def get_available_usdt_symbols():

    url = "https://data-api.binance.vision/api/v3/exchangeInfo"

    data = http_get(url)

    if not data:
        return set()

    symbols = set()

    for item in data.get("symbols", []):

        try:

            if (
                item.get("quoteAsset") == "USDT"
                and item.get("status") == "TRADING"
                and item.get("isSpotTradingAllowed", True)
            ):
                symbols.add(item["baseAsset"])

        except Exception:
            continue

    return symbols


# ------------------------------------------------------------
# KLINES
# ------------------------------------------------------------

def get_klines(symbol, interval="4h", limit=250):

    encoded_symbol = urllib.parse.quote(symbol + "USDT")

    url = (
        "https://data-api.binance.vision/api/v3/klines"
        f"?symbol={encoded_symbol}"
        f"&interval={interval}"
        f"&limit={limit}"
    )

    data = http_get(url)

    if not isinstance(data, list):
        return None

    if len(data) < 50:
        return None

    # حذف آخرین کندل چون هنوز ممکن است بسته نشده باشد
    data = data[:-1]

    candles = []

    for c in data:

        try:

            candles.append({
                "open_time": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "close_time": int(c[6])
            })

        except Exception:
            continue

    return candles


# ------------------------------------------------------------
# INDICATORS
# ------------------------------------------------------------

def ema_series(values, period):

    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)

    result = [
        sum(values[:period]) / period
    ]

    for value in values[period:]:

        result.append(
            (value - result[-1]) * multiplier + result[-1]
        )

    return result


def ema(values, period):

    result = ema_series(values, period)

    return result[-1] if result else None


def rsi_series(values, period=14):

    if len(values) <= period:
        return []

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    result = []

    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(100 - (100 / (1 + rs)))

    for i in range(period, len(gains)):

        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - (100 / (1 + rs)))

    return result


def rsi(values, period=14):

    result = rsi_series(values, period)

    return result[-1] if result else 50.0


def macd(values, fast=12, slow=26, signal=9):

    if len(values) < 60:
        return None

    fast_ema = ema_series(values, fast)
    slow_ema = ema_series(values, slow)

    if not fast_ema or not slow_ema:
        return None

    # هم‌تراز کردن EMA سریع و کند
    fast_ema = fast_ema[slow - fast:]

    macd_line = []

    for f, s in zip(fast_ema, slow_ema):
        macd_line.append(f - s)

    signal_line = ema_series(macd_line, signal)

    if not signal_line:
        return None

    macd_now = macd_line[-1]
    signal_now = signal_line[-1]

    # مقدار قبلی برای تشخیص کراس
    if len(macd_line) >= 2 and len(signal_line) >= 2:

        macd_prev = macd_line[-2]
        signal_prev = signal_line[-2]

    else:

        macd_prev = macd_now
        signal_prev = signal_now

    histogram = macd_now - signal_now

    return {
        "macd": macd_now,
        "signal": signal_now,
        "hist": histogram,
        "prev_macd": macd_prev,
        "prev_signal": signal_prev
    }


def atr(highs, lows, closes, period=14):

    if len(closes) <= period:
        return None

    true_ranges = []

    for i in range(1, len(closes)):

        previous_close = closes[i - 1]

        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - previous_close),
            abs(lows[i] - previous_close)
        )

        true_ranges.append(tr)

    return sum(true_ranges[-period:]) / period


# ------------------------------------------------------------
# PRICE ACTION
# ------------------------------------------------------------

def price_action(opens, highs, lows, closes):

    if len(closes) < 3:
        return "NEUTRAL"

    o = opens[-1]
    h = highs[-1]
    l = lows[-1]
    c = closes[-1]

    previous_o = opens[-2]
    previous_c = closes[-2]

    candle_range = h - l

    if candle_range <= 0:
        return "NEUTRAL"

    body = abs(c - o)

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Bullish pin bar
    if (
        lower_wick > body * 2
        and lower_wick > candle_range * 0.50
    ):
        return "BULLISH_PINBAR"

    # Bearish pin bar
    if (
        upper_wick > body * 2
        and upper_wick > candle_range * 0.50
    ):
        return "BEARISH_PINBAR"

    # Bullish engulfing
    if (
        previous_c < previous_o
        and c > o
        and c >= previous_o
        and o <= previous_c
    ):
        return "BULLISH_ENGULFING"

    # Bearish engulfing
    if (
        previous_c > previous_o
        and c < o
        and c <= previous_o
        and o >= previous_c
    ):
        return "BEARISH_ENGULFING"

    if c > o:
        return "BULLISH_CANDLE"

    if c < o:
        return "BEARISH_CANDLE"

    return "NEUTRAL"


# ------------------------------------------------------------
# RSI DIVERGENCE - SIMPLE PIVOT METHOD
# ------------------------------------------------------------

def rsi_divergence(prices, rsi_values):

    if len(prices) < 30 or len(rsi_values) < 30:
        return "NONE"

    # دو بازه اخیر
    price_a = min(prices[-20:-10])
    price_b = min(prices[-10:])

    rsi_a = min(rsi_values[-20:-10])
    rsi_b = min(rsi_values[-10:])

    # Bullish divergence
    if price_b < price_a and rsi_b > rsi_a:
        return "BULLISH_DIVERGENCE"

    price_a = max(prices[-20:-10])
    price_b = max(prices[-10:])

    rsi_a = max(rsi_values[-20:-10])
    rsi_b = max(rsi_values[-10:])

    # Bearish divergence
    if price_b > price_a and rsi_b < rsi_a:
        return "BEARISH_DIVERGENCE"

    return "NONE"


# ------------------------------------------------------------
# BTC MACRO TREND
# ------------------------------------------------------------

def get_btc_macro_trend():

    candles = get_klines("BTC", "1d", 100)

    if not candles:
        return "NEUTRAL"

    closes = [x["close"] for x in candles]

    e50 = ema(closes, 50)

    if e50 is None:
        return "NEUTRAL"

    last_price = closes[-1]

    # روند قوی‌تر با EMA20 و EMA50
    e20 = ema(closes, 20)

    if e20 is None:
        return "NEUTRAL"

    if last_price > e50 and e20 > e50:
        return "BULLISH"

    if last_price < e50 and e20 < e50:
        return "BEARISH"

    return "NEUTRAL"


# ------------------------------------------------------------
# ANALYSIS
# ------------------------------------------------------------

def analyze(symbol, btc_trend):

    candles = get_klines(
        symbol,
        TIMEFRAME,
        KLINE_LIMIT
    )

    if not candles:
        return None

    opens = [x["open"] for x in candles]
    highs = [x["high"] for x in candles]
    lows = [x["low"] for x in candles]
    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]

    if len(closes) < 210:
        return None

    price = closes[-1]

    # -------------------------
    # EMA
    # -------------------------

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    # -------------------------
    # RSI
    # -------------------------

    rsi_values = rsi_series(closes, 14)

    if not rsi_values:
        return None

    rsi_now = rsi_values[-1]

    # -------------------------
    # MACD
    # -------------------------

    macd_data = macd(closes)

    if not macd_data:
        return None

    # -------------------------
    # ATR
    # -------------------------

    atr_value = atr(
        highs,
        lows,
        closes,
        ATR_PERIOD
    )

    if not atr_value or atr_value <= 0:
        return None

    # -------------------------
    # VOLUME
    # -------------------------

    if len(volumes) >= 21:

        avg_volume = sum(
            volumes[-21:-1]
        ) / 20

    else:

        avg_volume = sum(volumes) / len(volumes)

    volume_ratio = (
        volumes[-1] / avg_volume
        if avg_volume > 0
        else 1
    )

    # -------------------------
    # PRICE ACTION
    # -------------------------

    pa = price_action(
        opens,
        highs,
        lows,
        closes
    )

    # -------------------------
    # DIVERGENCE
    # -------------------------

    div = rsi_divergence(
        closes,
        rsi_values
    )

    # ========================================================
    # SCORE
    # ========================================================

    buy_score = 0
    sell_score = 0

    reasons_buy = []
    reasons_sell = []

    # --------------------------------------------------------
    # EMA TREND
    # --------------------------------------------------------

    if e9 > e21:
        buy_score += 15
        reasons_buy.append("EMA9 > EMA21")

    elif e9 < e21:
        sell_score += 15
        reasons_sell.append("EMA9 < EMA21")

    if e21 > e50:
        buy_score += 10
        reasons_buy.append("EMA21 > EMA50")

    elif e21 < e50:
        sell_score += 10
        reasons_sell.append("EMA21 < EMA50")

    # --------------------------------------------------------
    # EMA200
    # --------------------------------------------------------

    if price > e200:
        buy_score += 10
        reasons_buy.append("قیمت بالای EMA200")

    elif price < e200:
        sell_score += 10
        reasons_sell.append("قیمت زیر EMA200")

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    # خرید:
    # RSI زیر 35 + برگشت = شرایط مطلوب
    # RSI بین 40 تا 60 = روند سالم
    if 30 <= rsi_now <= 38:

        buy_score += 15
        reasons_buy.append(
            f"RSI مناسب برای برگشت ({rsi_now:.1f})"
        )

    elif 38 < rsi_now <= 55:

        buy_score += 7
        reasons_buy.append(
            f"RSI صعودی/متعادل ({rsi_now:.1f})"
        )

    # فروش
    if 62 <= rsi_now <= 70:

        sell_score += 15
        reasons_sell.append(
            f"RSI مناسب برای اصلاح ({rsi_now:.1f})"
        )

    elif 55 <= rsi_now < 62:

        sell_score += 7
        reasons_sell.append(
            f"RSI نزولی/متعادل ({rsi_now:.1f})"
        )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    macd_now = macd_data["macd"]
    signal_now = macd_data["signal"]

    macd_prev = macd_data["prev_macd"]
    signal_prev = macd_data["prev_signal"]

    bullish_cross = (
        macd_prev <= signal_prev
        and macd_now > signal_now
    )

    bearish_cross = (
        macd_prev >= signal_prev
        and macd_now < signal_now
    )

    if bullish_cross:

        buy_score += 20
        reasons_buy.append("MACD Bullish Cross")

    elif macd_now > signal_now:

        buy_score += 10
        reasons_buy.append("MACD مثبت")

    if bearish_cross:

        sell_score += 20
        reasons_sell.append("MACD Bearish Cross")

    elif macd_now < signal_now:

        sell_score += 10
        reasons_sell.append("MACD منفی")

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume_ratio >= MIN_VOLUME_RATIO:

        if closes[-1] > opens[-1]:

            buy_score += 15
            reasons_buy.append(
                f"حجم قوی {volume_ratio:.2f}x"
            )

        elif closes[-1] < opens[-1]:

            sell_score += 15
            reasons_sell.append(
                f"حجم قوی {volume_ratio:.2f}x"
            )

    # --------------------------------------------------------
    # PRICE ACTION
    # --------------------------------------------------------

    if pa in (
        "BULLISH_PINBAR",
        "BULLISH_ENGULFING"
    ):

        buy_score += 10
        reasons_buy.append(pa)

    if pa in (
        "BEARISH_PINBAR",
        "BEARISH_ENGULFING"
    ):

        sell_score += 10
        reasons_sell.append(pa)

    # --------------------------------------------------------
    # RSI DIVERGENCE
    # --------------------------------------------------------

    if div == "BULLISH_DIVERGENCE":

        buy_score += 10
        reasons_buy.append("Bullish RSI Divergence")

    elif div == "BEARISH_DIVERGENCE":

        sell_score += 10
        reasons_sell.append("Bearish RSI Divergence")

    # ========================================================
    # BTC FILTER
    # ========================================================

    # اگر BTC نزولی باشد، BUY خیلی سخت‌تر می‌شود
    # اگر BTC صعودی باشد، SELL خیلی سخت‌تر می‌شود

    if btc_trend == "BEARISH":

        buy_score -= 15

    elif btc_trend == "BULLISH":

        sell_score -= 15

    buy_score = max(0, buy_score)
    sell_score = max(0, sell_score)

    # ========================================================
    # FINAL DECISION
    # ========================================================

    direction = None
    score = 0

    if (
        buy_score >= MIN_SCORE
        and buy_score > sell_score
        and btc_trend != "BEARISH"
    ):

        direction = "BUY"
        score = buy_score

    elif (
        sell_score >= MIN_SCORE
        and sell_score > buy_score
        and btc_trend != "BULLISH"
    ):

        direction = "SELL"
        score = sell_score

    else:

        return {
            "is_signal": False,
            "symbol": symbol,
            "price": price,
            "rsi": rsi_now,
            "btc_trend": btc_trend,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "candle_time": candles[-1]["close_time"]
        }

    # ========================================================
    # SL / TP
    # ========================================================

    recent_high = max(highs[-5:])
    recent_low = min(lows[-5:])

    if direction == "BUY":

        sl_by_atr = price - (
            SL_ATR_MULTIPLIER * atr_value
        )

        sl = min(
            sl_by_atr,
            recent_low
        )

        risk = price - sl

        tp1 = price + (
            risk * TP1_RR
        )

        tp2 = price + (
            risk * TP2_RR
        )

    else:

        sl_by_atr = price + (
            SL_ATR_MULTIPLIER * atr_value
        )

        sl = max(
            sl_by_atr,
            recent_high
        )

        risk = sl - price

        tp1 = price - (
            risk * TP1_RR
        )

        tp2 = price - (
            risk * TP2_RR
        )

    if risk <= 0:
        return None

    # ========================================================
    # POSITION SIZE
    # ========================================================

    risk_amount = (
        ACCOUNT_SIZE_USDT *
        RISK_PER_TRADE
    )

    stop_percent = risk / price

    position_size = (
        risk_amount / stop_percent
        if stop_percent > 0
        else 0
    )

    position_size = min(
        position_size,
        MAX_POSITION_USDT
    )

    stop_percent_display = (
        stop_percent * 100
    )

    # R/R واقعی TP2
    rr = (
        abs(tp2 - price) / risk
    )

    return {

        "is_signal": True,

        "symbol": symbol,
        "timeframe": TIMEFRAME,

        "direction": direction,

        "score": score,

        "buy_score": buy_score,
        "sell_score": sell_score,

        "price": price,

        "rsi": rsi_now,

        "macd": macd_now,
        "macd_signal": signal_now,

        "volume_ratio": volume_ratio,

        "price_action": pa,

        "divergence": div,

        "btc_trend": btc_trend,

        "atr": atr_value,

        "sl": sl,

        "tp1": tp1,

        "tp2": tp2,

        "rr": rr,

        "position_size": position_size,

        "risk_amount": risk_
