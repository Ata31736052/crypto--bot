# ============================================================
# Crypto 4H Signal Bot - FINAL
# ============================================================

import json
import time
import os
import ssl
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta


# ============================================================
# SETTINGS
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "signals_state.json"

TIMEFRAME = "4h"
KLINE_LIMIT = 250

MIN_SCORE = 70
MIN_VOLUME_RATIO = 1.20

# فقط برای محاسبات مدیریت ریسک
ACCOUNT_SIZE_USDT = 1000.0
RISK_PER_TRADE = 0.01
MAX_POSITION_USDT = 1000.0

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.30

TP1_RR = 1.50
TP2_RR = 2.80

SEND_NO_SIGNAL_REPORT = False

IRAN_TZ = timezone(
    timedelta(hours=3, minutes=30)
)


# ============================================================
# COINS
# ============================================================

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
    "JUP",
]


# ============================================================
# HTTPS
# ============================================================

CTX = ssl.create_default_context()


def http_get(url, timeout=20, retries=3):

    for attempt in range(retries):

        try:

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent":
                    "Mozilla/5.0 CryptoSignalBot/1.0"
                }
            )

            with urllib.request.urlopen(
                request,
                context=CTX,
                timeout=timeout
            ) as response:

                return json.loads(
                    response.read().decode("utf-8")
                )

        except Exception as error:

            print(
                f"HTTP ERROR "
                f"({attempt + 1}/{retries}): "
                f"{error}"
            )

            if attempt < retries - 1:
                time.sleep(1)

    return None


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def get_available_usdt_symbols():

    url = (
        "https://data-api.binance.vision"
        "/api/v3/exchangeInfo"
    )

    data = http_get(url)

    if not isinstance(data, dict):
        return set()

    symbols = set()

    for item in data.get("symbols", []):

        try:

            if (
                item.get("quoteAsset") == "USDT"
                and item.get("status") == "TRADING"
                and item.get(
                    "isSpotTradingAllowed",
                    True
                )
            ):

                symbols.add(
                    item["baseAsset"]
                )

        except Exception:

            continue

    return symbols


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol,
    interval="4h",
    limit=250
):

    symbol_name = (
        symbol.upper() +
        "USDT"
    )

    encoded_symbol = urllib.parse.quote(
        symbol_name
    )

    url = (
        "https://data-api.binance.vision"
        "/api/v3/klines"
        f"?symbol={encoded_symbol}"
        f"&interval={interval}"
        f"&limit={limit}"
    )

    data = http_get(url)

    if not isinstance(data, list):
        return None

    if len(data) < 50:
        return None

    # آخرین کندل هنوز در حال تشکیل است.
    data = data[:-1]

    candles = []

    for candle in data:

        try:

            candles.append(
                {
                    "open_time":
                        int(candle[0]),

                    "open":
                        float(candle[1]),

                    "high":
                        float(candle[2]),

                    "low":
                        float(candle[3]),

                    "close":
                        float(candle[4]),

                    "volume":
                        float(candle[5]),

                    "close_time":
                        int(candle[6]),
                }
            )

        except Exception:

            continue

    if not candles:
        return None

    return candles


# ============================================================
# EMA
# ============================================================

def ema_series(values, period):

    if len(values) < period:
        return []

    multiplier = 2.0 / (
        period + 1.0
    )

    first_value = (
        sum(values[:period]) /
        period
    )

    result = [
        first_value
    ]

    for value in values[period:]:

        previous = result[-1]

        current = (
            (value - previous)
            * multiplier
            + previous
        )

        result.append(current)

    return result


def ema(values, period):

    result = ema_series(
        values,
        period
    )

    if not result:
        return None

    return result[-1]


# ============================================================
# RSI
# ============================================================

def rsi_series(
    values,
    period=14
):

    if len(values) <= period:
        return []

    gains = []
    losses = []

    for i in range(
        1,
        len(values)
    ):

        change = (
            values[i] -
            values[i - 1]
        )

        gains.append(
            max(change, 0.0)
        )

        losses.append(
            max(-change, 0.0)
        )

    avg_gain = (
        sum(gains[:period]) /
        period
    )

    avg_loss = (
        sum(losses[:period]) /
        period
    )

    result = []

    def calculate_rsi(
        gain,
        loss
    ):

        if loss == 0:
            return 100.0

        rs = gain / loss

        return (
            100.0 -
            100.0 /
            (1.0 + rs)
        )

    result.append(
        calculate_rsi(
            avg_gain,
            avg_loss
        )
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain *
                (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss *
                (period - 1)
            )
            + losses[i]
        ) / period

        result.append(
            calculate_rsi(
                avg_gain,
                avg_loss
            )
        )

    return result


# ============================================================
# MACD
# ============================================================

def macd(
    values,
    fast=12,
    slow=26,
    signal=9
):

    if len(values) < 60:
        return None

    fast_ema = ema_series(
        values,
        fast
    )

    slow_ema = ema_series(
        values,
        slow
    )

    if not fast_ema:
        return None

    if not slow_ema:
        return None

    # هم‌تراز کردن EMAها
    fast_ema = fast_ema[
        slow - fast:
    ]

    macd_line = []

    for fast_value, slow_value in zip(
        fast_ema,
        slow_ema
    ):

        macd_line.append(
            fast_value -
            slow_value
        )

    signal_line = ema_series(
        macd_line,
        signal
    )

    if len(signal_line) < 2:
        return None

    aligned_macd = macd_line[
        len(macd_line) -
        len(signal_line):
    ]

    if len(aligned_macd) < 2:
        return None

    return {
        "macd":
            aligned_macd[-1],

        "signal":
            signal_line[-1],

        "prev_macd":
            aligned_macd[-2],

        "prev_signal":
            signal_line[-2],
    }


# ============================================================
# ATR
# ============================================================

def atr(
    highs,
    lows,
    closes,
    period=14
):

    if len(closes) <= period:
        return None

    true_ranges = []

    for i in range(
        1,
        len(closes)
    ):

        previous_close = (
            closes[i - 1]
        )

        true_range = max(
            highs[i] - lows[i],

            abs(
                highs[i] -
                previous_close
            ),

            abs(
                lows[i] -
                previous_close
            ),
        )

        true_ranges.append(
            true_range
        )

    if len(true_ranges) < period:
        return None

    return (
        sum(
            true_ranges[-period:]
        ) /
        period
    )


# ============================================================
# PRICE ACTION
# ============================================================

def price_action(
    opens,
    highs,
    lows,
    closes
):

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

    body = abs(
        c - o
    )

    upper_wick = (
        h -
        max(o, c)
    )

    lower_wick = (
        min(o, c) -
        l
    )

    if (
        lower_wick > body * 2
        and
        lower_wick >
        candle_range * 0.50
    ):

        return "BULLISH_PINBAR"

    if (
        upper_wick > body * 2
        and
        upper_wick >
        candle_range * 0.50
    ):

        return "BEARISH_PINBAR"

    if (
        previous_c < previous_o
        and
        c > o
        and
        c >= previous_o
        and
        o <= previous_c
    ):

        return "BULLISH_ENGULFING"

    if (
        previous_c > previous_o
        and
        c < o
        and
        c <= previous_o
        and
        o >= previous_c
    ):

        return "BEARISH_ENGULFING"

    if c > o:
        return "BULLISH_CANDLE"

    if c < o:
        return "BEARISH_CANDLE"

    return "NEUTRAL"


# ============================================================
# RSI DIVERGENCE
# ============================================================

def rsi_divergence(
    prices,
    rsi_values
):

    if (
        len(prices) < 30
        or
        len(rsi_values) < 30
    ):

        return "NONE"

    old_price_low = min(
        prices[-20:-10]
    )

    new_price_low = min(
        prices[-10:]
    )

    old_rsi_low = min(
        rsi_values[-20:-10]
    )

    new_rsi_low = min(
        rsi_values[-10:]
    )

    if (
        new_price_low <
        old_price_low
        and
        new_rsi_low >
        old_rsi_low
    ):

        return "BULLISH_DIVERGENCE"

    old_price_high = max(
        prices[-20:-10]
    )

    new_price_high = max(
        prices[-10:]
    )

    old_rsi_high = max(
        rsi_values[-20:-10]
    )

    new_rsi_high = max(
        rsi_values[-10:]
    )

    if (
        new_price_high >
        old_price_high
        and
        new_rsi_high <
        old_rsi_high
    ):

        return "BEARISH_DIVERGENCE"

    return "NONE"


# ============================================================
# BTC DAILY TREND
# ============================================================

def get_btc_macro_trend():

    candles = get_klines(
        "BTC",
        "1d",
        100
    )

    if not candles:
        return "NEUTRAL"

    closes = [
        candle["close"]
        for candle in candles
    ]

    ema20 = ema(
        closes,
        20
    )

    ema50 = ema(
        closes,
        50
    )

    if (
        ema20 is None
        or
        ema50 is None
    ):

        return "NEUTRAL"

    price = closes[-1]

    if (
        price > ema50
        and
        ema20 > ema50
    ):

        return "BULLISH"

    if (
        price < ema50
        and
        ema20 < ema50
    ):

        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# ANALYZE COIN
# ============================================================

def analyze(
    symbol,
    btc_trend
):

    candles = get_klines(
        symbol,
        TIMEFRAME,
        KLINE_LIMIT
    )

    if not candles:
        return None

    if len(candles) < 210:
        return None

    opens = [
        candle["open"]
        for candle in candles
    ]

    highs = [
        candle["high"]
        for candle in candles
    ]

    lows = [
        candle["low"]
        for candle in candles
    ]

    closes = [
        candle["close"]
        for candle in candles
    ]

    volumes = [
        candle["volume"]
        for candle in candles
    ]

    price = closes[-1]

    ema9 = ema(
        closes,
        9
    )

    ema21 = ema(
        closes,
        21
    )

    ema50 = ema(
        closes,
        50
    )

    ema200 = ema(
        closes,
        200
    )

    rsi_values = rsi_series(
        closes,
        14
    )

    if not rsi_values:
        return None

    rsi_now = rsi_values[-1]

    macd_data = macd(
        closes
    )

    if not macd_data:
        return None

    atr_value = atr(
        highs,
        lows,
        closes,
        ATR_PERIOD
    )

    if (
        atr_value is None
        or
        atr_value <= 0
    ):

        return None

    if len(volumes) >= 21:

        average_volume = (
            sum(
                volumes[-21:-1]
            ) / 20
        )

    else:

        average_volume = (
            sum(volumes) /
            len(volumes)
        )

    if average_volume <= 0:
        volume_ratio = 1.0

    else:

        volume_ratio = (
            volumes[-1] /
            average_volume
        )

    pa = price_action(
        opens,
        highs,
        lows,
        closes
    )

    divergence = rsi_divergence(
        closes,
        rsi_values
    )

    # ========================================================
    # SCORE
    # ========================================================

    buy_score = 0
    sell_score = 0

    buy_reasons = []
    sell_reasons = []

    # EMA 9 / 21
    if ema9 > ema21:

        buy_score += 15

        buy_reasons.append(
            "EMA9 > EMA21"
        )

    elif ema9 < ema21:

        sell_score += 15

        sell_reasons.append(
            "EMA9 < EMA21"
        )

    # EMA 21 / 50
    if ema21 > ema50:

        buy_score += 10

        buy_reasons.append(
            "EMA21 > EMA50"
        )

    elif ema21 < ema50:

        sell_score += 10

        sell_reasons.append(
            "EMA21 < EMA50"
        )

    # EMA 200
    if price > ema200:

        buy_score += 10

        buy_reasons.append(
            "قیمت بالای EMA200"
        )

    elif price < ema200:

        sell_score += 10

        sell_reasons.append(
            "قیمت زیر EMA200"
        )

    # ========================================================
    # RSI
    # ========================================================

    if rsi_now < 30:

        buy_score += 15

        buy_reasons.append(
            f"RSI اشباع فروش ({rsi_now:.1f})"
        )

    elif 30 <= rsi_now <= 38:

        buy_score += 15

        buy_reasons.append(
            f"RSI مناسب برگشت ({rsi_now:.1f})"
        )

    elif 38 < rsi_now <= 55:

        buy_score += 7

        buy_reasons.append(
            f"RSI متعادل ({rsi_now:.1f})"
        )

    if rsi_now > 70:

        sell_score += 15

        sell_reasons.append(
            f"RSI اشباع خرید ({rsi_now:.1f})"
        )

    elif 62 <= rsi_now <= 70:

        sell_score += 15

        sell_reasons.append(
            f"RSI مناسب اصلاح ({rsi_now:.1f})"
        )

    elif 55 <= rsi_now < 62:

        sell_score += 7

        sell_reasons.append(
            f"RSI متعادل ({rsi_now:.1f})"
        )

    # ========================================================
    # MACD
    # ========================================================

    macd_now = macd_data["macd"]
    signal_now = macd_data["signal"]

    previous_macd = (
        macd_data["prev_macd"]
    )

    previous_signal = (
        macd_data["prev_signal"]
    )

    bullish_cross = (
        previous_macd <= previous_signal
        and
        macd_now > signal_now
    )

    bearish_cross = (
        previous_macd >= previous_signal
        and
        macd_now < signal_now
    )

    if bullish_cross:

        buy_score += 20

        buy_reasons.append(
            "MACD Bullish Cross"
        )

    elif macd_now > signal_now:

        buy_score += 10

        buy_reasons.append(
            "MACD مثبت"
        )

    if bearish_cross:

        sell_score += 20

        sell_reasons.append(
            "MACD Bearish Cross"
        )

    elif macd_now < signal_now:

        sell_score += 10

        sell_reasons.append(
            "MACD منفی"
        )

    # ========================================================
    # VOLUME
    # ========================================================

    if volume_ratio >= MIN_VOLUME_RATIO:

        if closes[-1] > opens[-1]:

            buy_score += 15

            buy_reasons.append(
                f"حجم قوی {volume_ratio:.2f}x"
            )

        elif closes[-1] < opens[-1]:

            sell_score += 15

            sell_reasons.append(
                f"حجم قوی {volume_ratio:.2f}x"
            )

    # ========================================================
    # PRICE ACTION
    # ========================================================

    if pa in (
        "BULLISH_PINBAR",
        "BULLISH_ENGULFING"
    ):

        buy_score += 10

        buy_reasons.append(
            pa
        )

    if pa in (
        "BEARISH_PINBAR",
        "BEARISH_ENGULFING"
    ):

        sell_score += 10

        sell_reasons.append(
            pa
        )

    # ========================================================
    # DIVERGENCE
    # ========================================================

    if divergence == "BULLISH_DIVERGENCE":

        buy_score += 10

        buy_reasons.append(
            "Bullish RSI Divergence"
        )

    elif divergence == "BEARISH_DIVERGENCE":

        sell_score += 10

        sell_reasons.append(
            "Bearish RSI Divergence"
        )

    # ========================================================
    # BTC FILTER
    # ========================================================

    if btc_trend == "BEARISH":

        buy_score -= 15

    elif btc_trend == "BULLISH":

        sell_score -= 15

    buy_score = max(
        0,
        buy_score
    )

    sell_score = max(
        0,
        sell_score
    )

    # ========================================================
    # FINAL DIRECTION
    # ========================================================

    direction = None
    score = 0

    if (
        buy_score >= MIN_SCORE
        and
        buy_score > sell_score
        and
        btc_trend != "BEARISH"
    ):

        direction = "BUY"
        score = buy_score

    elif (
        sell_score >= MIN_SCORE
        and
        sell_score > buy_score
        and
        btc_trend != "BULLISH"
    ):

        direction = "SELL"
        score = sell_score

    else:

        return {
            "is_signal": False,
            "symbol": symbol,
            "price": price,
            "rsi": rsi_now,
            "btc_tr
