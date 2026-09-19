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

ACCOUNT_SIZE_USDT = 1000.0
RISK_PER_TRADE = 0.01
MAX_POSITION_USDT = 1000.0

ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.30

TP1_RR = 1.50
TP2_RR = 2.80

IRAN_TZ = timezone(
    timedelta(hours=3, minutes=30)
)

CTX = ssl.create_default_context()

NOBITEX_STATS_URL = (
    "https://api.nobitex.ir/market/stats"
)

BINANCE_EXCHANGE_URL = (
    "https://data-api.binance.vision"
    "/api/v3/exchangeInfo"
)

BINANCE_KLINES_URL = (
    "https://data-api.binance.vision"
    "/api/v3/klines"
)


# ============================================================
# HTTP
# ============================================================

def http_get(url, timeout=20, retries=3):

    for attempt in range(retries):

        try:

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent":
                    "Mozilla/5.0 CryptoSignalBot/3.0"
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
# NOBITEX COINS
# ============================================================

def get_nobitex_usdt_coins():

    print("Getting active USDT markets from Nobitex...")

    data = http_get(
        NOBITEX_STATS_URL
    )

    if not isinstance(data, dict):
        print("Nobitex response invalid.")
        return []

    stats = data.get("stats")

    if not isinstance(stats, dict):
        print("Nobitex stats not found.")
        return []

    coins = []

    for market, info in stats.items():

        try:

            market = market.lower()

            # فقط بازارهای USDT
            if not market.endswith("-usdt"):
                continue

            parts = market.split("-")

            if len(parts) != 2:
                continue

            coin = parts[0].upper()

            if coin == "USDT":
                continue

            if isinstance(info, dict):

                # بازار بسته نباشد
                if info.get(
                    "isClosed",
                    False
                ):
                    continue

            coins.append(coin)

        except Exception:

            continue

    coins = sorted(
        list(set(coins))
    )

    print(
        f"Nobitex active USDT coins: "
        f"{len(coins)}"
    )

    print(
        ", ".join(coins)
    )

    return coins


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def get_binance_usdt_symbols():

    print("Getting Binance symbols...")

    data = http_get(
        BINANCE_EXCHANGE_URL
    )

    if not isinstance(data, dict):
        print("Binance exchangeInfo invalid.")
        return set()

    result = set()

    for item in data.get(
        "symbols",
        []
    ):

        try:

            if (
                item.get("quoteAsset")
                == "USDT"
                and
                item.get("status")
                == "TRADING"
            ):

                base = item.get(
                    "baseAsset"
                )

                symbol = item.get(
                    "symbol"
                )

                if base and symbol:
                    result.add(
                        base.upper()
                    )

        except Exception:

            continue

    print(
        f"Binance USDT symbols: "
        f"{len(result)}"
    )

    return result


# ============================================================
# FINAL COIN LIST
# ============================================================

def get_scan_coins():

    nobitex_coins = (
        get_nobitex_usdt_coins()
    )

    if not nobitex_coins:
        print(
            "ERROR: No Nobitex coins found."
        )
        return []

    binance_coins = (
        get_binance_usdt_symbols()
    )

    if not binance_coins:
        print(
            "ERROR: No Binance symbols found."
        )
        return []

    coins = sorted(
        set(nobitex_coins)
        &
        binance_coins
    )

    print(
        f"Final scan list: "
        f"{len(coins)} coins"
    )

    print(
        ", ".join(coins)
    )

    return coins


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

    params = urllib.parse.urlencode({
        "symbol": symbol_name,
        "interval": interval,
        "limit": limit
    })

    url = (
        BINANCE_KLINES_URL
        + "?"
        + params
    )

    data = http_get(url)

    if not isinstance(data, list):
        return None

    if len(data) < 50:
        return None

    now_ms = int(
        time.time() * 1000
    )

    # حذف کندل در حال تشکیل
    if data:

        try:

            last_close_time = int(
                data[-1][6]
            )

            if last_close_time > now_ms:
                data = data[:-1]

        except Exception:
            pass

    candles = []

    for candle in data:

        try:

            candles.append({
                "open_time": int(candle[0]),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
                "close_time": int(candle[6])
            })

        except Exception:
            continue

    if len(candles) < 50:
        return None

    return candles


# ============================================================
# EMA
# ============================================================

def ema_series(values, period):

    if len(values) < period:
        return []

    multiplier = (
        2.0 / (period + 1.0)
    )

    first_value = (
        sum(values[:period])
        / period
    )

    result = [
        first_value
    ]

    for value in values[period:]:

        current = (
            (value - result[-1])
            * multiplier
            + result[-1]
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

def rsi_series(values, period=14):

    if len(values) <= period:
        return []

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(change, 0.0)
        )

        losses.append(
            max(-change, 0.0)
        )

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    def calc(gain, loss):

        if loss == 0:
            return 100.0

        rs = gain / loss

        return (
            100.0
            - 100.0 / (1.0 + rs)
        )

    result = [
        calc(
            avg_gain,
            avg_loss
        )
    ]

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

        result.append(
            calc(
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

    fast_values = ema_series(
        values,
        fast
    )

    slow_values = ema_series(
        values,
        slow
    )

    if not fast_values or not slow_values:
        return None

    fast_values = fast_values[
        slow - fast:
    ]

    macd_line = []

    for f, s in zip(
        fast_values,
        slow_values
    ):

        macd_line.append(
            f - s
        )

    signal_line = ema_series(
        macd_line,
        signal
    )

    if len(signal_line) < 2:
        return None

    aligned_macd = macd_line[
        -len(signal_line):
    ]

    return {
        "macd":
            aligned_macd[-1],

        "signal":
            signal_line[-1],

        "prev_macd":
            aligned_macd[-2],

        "prev_signal":
            signal_line[-2]
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
                highs[i]
                - previous_close
            ),

            abs(
                lows[i]
                - previous_close
            )
        )

        true_ranges.append(
            true_range
        )

    if len(true_ranges) < period:
        return None

    return (
        sum(
            true_ranges[-period:]
        )
        / period
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

    po = opens[-2]
    pc = closes[-2]

    candle_range = h - l

    if candle_range <= 0:
        return "NEUTRAL"

    body = abs(c - o)

    upper_wick = (
        h - max(o, c)
    )

    lower_wick = (
        min(o, c) - l
    )

    if (
        lower_wick > body * 2
        and
        lower_wick
        > candle_range * 0.50
    ):

        return "BULLISH_PINBAR"

    if (
        upper_wick > body * 2
        and
        upper_wick
        > candle_range * 0.50
    ):

        return "BEARISH_PINBAR"

    if (
        pc < po
        and c > o
        and c >= po
        and o <= pc
    ):

        return "BULLISH_ENGULFING"

    if (
        pc > po
        and c < o
        and c <= po
        and o >= pc
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

    n = min(
        len(prices),
        len(rsi_values)
    )

    prices = prices[-n:]
    rsi_values = rsi_values[-n:]

    old_price = prices[-20:-10]
    new_price = prices[-10:]

    old_rsi = rsi_values[-20:-10]
    new_rsi = rsi_values[-10:]

    if (
        min(new_price)
        < min(old_price)
        and
        min(new_rsi)
        > min(old_rsi)
    ):

        return "BULLISH_DIVERGENCE"

    if (
        max(new_price)
        > max(old_price)
        and
        max(new_rsi)
        < max(old_rsi)
    ):

        return "BEARISH_DIVERGENCE"

    return "NONE"


# ============================================================
# BTC TREND
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
        x["close"]
        for x in candles
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
# ANALYZE
# ============================================================

def analyze(symbol, btc_trend):

    candles = get_klines(
        symbol,
        TIMEFRAME,
        KLINE_LIMIT
    )

    if not candles:
        print(
            f"{symbol}: no candle data"
        )
        return None

    if len(candles) < 210:
        print(
            f"{symbol}: not enough candles "
            f"({len(candles)})"
        )
        return None

    opens = [
        x["open"]
        for x in candles
    ]

    highs = [
        x["high"]
        for x in candles
    ]

    lows = [
        x["low"]
        for x in candles
    ]

    closes = [
        x["close"]
        for x in candles
    ]

    volumes = [
        x["volume"]
        for x in candles
    ]

    price = closes[-1]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    rsi_values = rsi_series(
        closes,
        14
    )

    macd_data = macd(
        closes
    )

    atr_value = atr(
        highs,
        lows,
        closes,
        ATR_PERIOD
    )

    if (
        ema9 is None
        or ema21 is None
        or ema50 is None
        or ema200 is None
        or atr_value is None
        or not rsi_values
        or not macd_data
    ):

        return None

    rsi_now = rsi_values[-1]

    average_volume = (
        sum(volumes[-21:-1])
        / 20
    )

    volume_ratio = (
        volumes[-1]
        / average_volume
        if average_volume > 0
        else 1.0
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

    buy_score = 0
    sell_score = 0

    buy_reasons = []
    sell_reasons = []

    # EMA 9/21
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

    # EMA 21/50
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

    # EMA200
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

    # RSI
    if rsi_now < 30:

        buy_score += 15
        buy_reasons.append(
            f"RSI اشباع فروش {rsi_now:.1f}"
        )

    elif rsi_now <= 38:

        buy_score += 15
        buy_reasons.append(
            f"RSI برگشتی {rsi_now:.1f}"
        )

    elif rsi_now <= 55:

        buy_score += 7
        buy_reasons.append(
            f"RSI {rsi_now:.1f}"
        )

    if rsi_now > 70:

        sell_score += 15
        sell_reasons.append(
            f"RSI اشباع خرید {rsi_now:.1f}"
        )

    elif rsi_now >= 62:

        sell_score += 15
        sell_reasons.append(
            f"RSI اصلاحی {rsi_now:.1f}"
        )

    elif rsi_now >= 55:

        sell_score += 7
        sell_reasons.append(
            f"RSI {rsi_now:.1f}"
        )

    # MACD
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
            "MACD بالای Signal"
        )

    if bearish_cross:

        sell_score += 20
        sell_reasons.append(
            "MACD Bearish Cross"
        )

    elif macd_now < signal_now:

        sell_score += 10
        sell_reasons.append(
            "MACD زیر Signal"
        )

    # Volume
    if volume_ratio >= MIN_VOLUME_RATIO:

        if closes[-1] > opens[-1]:

            buy_score += 15
            buy_reasons.append(
                f"حجم {volume_ratio:.2f}x"
            )

        elif closes[-1] < opens[-1]:

            sell_score += 15
            sell_reasons.append(
                f"حجم {volume_ratio:.2f}x"
            )

    # Price Action
    if pa in (
        "BULLISH_PINBAR",
        "BULLISH_ENGULFING"
    ):

        buy_score += 10
        buy_reasons.append(pa)

    elif pa in (
        "BEARISH_PINBAR",
        "BEARISH_ENGULFING"
    ):

        sell_score += 10
        sell_reasons.append(pa)

    # Divergence
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

    # BTC filter
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

    # Debug
    print(
        f"{symbol}: "
        f"RSI={rsi_now:.1f} "
        f"Volume={volume_ratio:.2f} "
        f"BUY={buy_score} "
        f"SELL={sell_score}"
    )

    # ========================================================
    # SIGNAL
    # ========================================================

    if (
        buy_score >= MIN_SCORE
        and
        buy_score > sell_score
    ):

        signal = "BUY"
        score = buy_score
        reasons = buy_reasons

    elif (
        sell_score >= MIN_SCORE
        and
        sell_score > buy_score
    ):

        signal = "SELL"
        score = sell_score
        reasons = sell_reasons

    else:

        return None

    # ========================================================
    # ENTRY / SL / TP
    # ========================================================

    entry = price

    if signal == "BUY":

        stop_loss = (
            entry
      
