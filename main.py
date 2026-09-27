# ============================================================
# CRYPTO SIGNAL BOT
# Nobitex-listed USDT coins
# Market data: Binance
# Timeframe: 4H
# ============================================================

import os
import json
import time
import math
import logging
import tempfile

from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
import numpy as np


# ============================================================
# 1. CONFIG
# ============================================================

class Config:

    telegram_token = os.getenv(
        "TELEGRAM_TOKEN",
        ""
    ).strip()

    telegram_chat_id = os.getenv(
        "TELEGRAM_CHAT_ID",
        ""
    ).strip()

    # Binance
    # از این دامنه استفاده می‌کنیم تا مشکل 451
    # api.binance.com در GitHub Actions نداشته باشیم.
    binance_base = "https://data-api.binance.vision"

    # Nobitex
    nobitex_urls = [
        "https://apiv2.nobitex.ir/market/stats",
        "https://api.nobitex.ir/market/stats"
    ]

    # Timeframe
    timeframe = "4h"
    kline_limit = 300

    # Signal
    min_score = 65
    strong_score = 78

    # Volume
    min_volume_ratio = 1.10
    min_24h_usdt_volume = 5_000_000

    # Indicators
    rsi_period = 14

    macd_fast = 12
    macd_slow = 26
    macd_signal = 9

    atr_period = 14

    # Risk
    account_size_usdt = 1000.0
    risk_percent = 1.0
    max_position_usdt = 1000.0

    # Stop Loss / Take Profit
    sl_atr_multiplier = 1.30
    tp1_rr = 1.50
    tp2_rr = 2.80
    max_sl_percent = 8.0

    # State
    state_file = "signals_state.json"
    dedup_hours = 8

    # Scanner
    max_workers = 10
    max_signals_per_run = 6

    # Telegram
    send_no_signal_report = True

    # Iran timezone
    iran_timezone = timezone(
        timedelta(
            hours=3,
            minutes=30
        )
    )


CFG = Config()


# ============================================================
# 2. LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger(
    "crypto-signal-bot"
)


# ============================================================
# 3. HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 CryptoSignalBot/3.0"
})


def http_get(
    url,
    params=None,
    timeout=20,
    retries=3
):

    last_error = None

    for attempt in range(
        retries
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=timeout
            )

            if response.status_code == 429:

                wait_seconds = (
                    3 + attempt * 3
                )

                log.warning(
                    f"Rate limit: {url} | "
                    f"waiting {wait_seconds}s"
                )

                time.sleep(
                    wait_seconds
                )

                continue

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            if attempt < retries - 1:

                time.sleep(
                    1.5 * (attempt + 1)
                )

            else:

                log.error(
                    f"GET failed: {url} | "
                    f"{last_error}"
                )

    return None


# ============================================================
# 4. TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if not CFG.telegram_token:

        log.error(
            "TELEGRAM_TOKEN is missing"
        )

        return False

    if not CFG.telegram_chat_id:

        log.error(
            "TELEGRAM_CHAT_ID is missing"
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        + CFG.telegram_token
        + "/sendMessage"
    )

    payload = {
        "chat_id": CFG.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    for attempt in range(3):

        try:

            response = SESSION.post(
                url,
                json=payload,
                timeout=15
            )

            if response.status_code == 429:

                wait_seconds = (
                    5 + attempt * 5
                )

                log.warning(
                    "Telegram rate limit"
                )

                time.sleep(
                    wait_seconds
                )

                continue

            response.raise_for_status()

            result = response.json()

            if result.get("ok"):

                return True

            log.error(
                f"Telegram error: {result}"
            )

        except Exception as exc:

            log.error(
                f"Telegram send error: {exc}"
            )

            time.sleep(2)

    return False


# ============================================================
# 5. SAFE FLOAT
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:

        number = float(value)

        if not math.isfinite(
            number
        ):

            return default

        return number

    except Exception:

        return default# ============================================================
# 6. STATE MANAGEMENT
# ============================================================

def load_state():

    if not os.path.exists(
        CFG.state_file
    ):
        return {}

    try:

        with open(
            CFG.state_file,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(
            data,
            dict
        ):
            return data

    except Exception as exc:

        log.warning(
            f"Could not load state: {exc}"
        )

    return {}


def save_state(
    state
):

    temp_path = None

    try:

        directory = os.path.dirname(
            os.path.abspath(
                CFG.state_file
            )
        )

        file_descriptor, temp_path = (
            tempfile.mkstemp(
                dir=directory,
                prefix="signals_state_",
                suffix=".tmp"
            )
        )

        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                state,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_path,
            CFG.state_file
        )

        return True

    except Exception as exc:

        log.error(
            f"Could not save state: {exc}"
        )

        if temp_path:

            try:
                os.remove(
                    temp_path
                )
            except Exception:
                pass

        return False


def cleanup_state(
    state
):

    current_time = time.time()

    max_age = (
        CFG.dedup_hours * 3600
    )

    cleaned = {}

    for key, value in state.items():

        try:

            timestamp = float(
                value.get(
                    "timestamp",
                    0
                )
            )

            if (
                current_time - timestamp
                < max_age
            ):

                cleaned[key] = value

        except Exception:

            continue

    return cleaned


def make_signal_key(
    symbol,
    direction,
    candle_time
):

    return (
        f"{symbol}_"
        f"{direction}_"
        f"{candle_time}"
    )


def signal_already_sent(
    state,
    symbol,
    direction,
    candle_time
):

    key = make_signal_key(
        symbol,
        direction,
        candle_time
    )

    return key in state


def mark_signal_sent(
    state,
    symbol,
    direction,
    candle_time
):

    key = make_signal_key(
        symbol,
        direction,
        candle_time
    )

    state[key] = {
        "timestamp": time.time(),
        "symbol": symbol,
        "direction": direction,
        "candle_time": candle_time
    }


# ============================================================
# 7. NOBITEX MARKET LIST
# ============================================================

def get_nobitex_coins():

    data = None

    for url in CFG.nobitex_urls:

        log.info(
            f"Trying Nobitex API: {url}"
        )

        result = http_get(
            url,
            timeout=20,
            retries=2
        )

        if not isinstance(
            result,
            dict
        ):
            continue

        if result.get(
            "status"
        ) == "ok":

            data = result

            log.info(
                f"Nobitex API connected: "
                f"{url}"
            )

            break

    if data is None:

        log.error(
            "All Nobitex API endpoints failed"
        )

        return set()

    stats = data.get(
        "stats",
        {}
    )

    if not isinstance(
        stats,
        dict
    ):

        log.error(
            "Nobitex stats is invalid"
        )

        return set()

    coins = set()

    for market, info in stats.items():

        try:

            if not isinstance(
                market,
                str
            ):
                continue

            market = market.lower()

            if not market.endswith(
                "-usdt"
            ):
                continue

            if not isinstance(
                info,
                dict
            ):
                continue

            if info.get(
                "isClosed"
            ) is True:
                continue

            coin = market.split(
                "-"
            )[0].upper()

            if coin:

                coins.add(
                    coin
                )

        except Exception:

            continue

    log.info(
        f"Nobitex active USDT markets: "
        f"{len(coins)}"
    )

    return coins


# ============================================================
# 8. BINANCE EXCHANGE INFO
# ============================================================

def get_binance_symbols():

    url = (
        CFG.binance_base
        + "/api/v3/exchangeInfo"
    )

    data = http_get(
        url,
        timeout=20,
        retries=3
    )

    if not isinstance(
        data,
        dict
    ):

        log.error(
            "Binance exchangeInfo unavailable"
        )

        return set()

    symbols = set()

    for item in data.get(
        "symbols",
        []
    ):

        try:

            symbol = item.get(
                "symbol"
            )

            status = item.get(
                "status"
            )

            quote_asset = item.get(
                "quoteAsset"
            )

            if not symbol:
                continue

            if status != "TRADING":
                continue

            if quote_asset != "USDT":
                continue

            symbols.add(
                symbol.upper()
            )

        except Exception:

            continue

    log.info(
        f"Binance active USDT symbols: "
        f"{len(symbols)}"
    )

    return symbols


# ============================================================
# 9. BINANCE 24H VOLUME
# ============================================================

def get_binance_volumes():

    url = (
        CFG.binance_base
        + "/api/v3/ticker/24hr"
    )

    data = http_get(
        url,
        timeout=20,
        retries=3
    )

    if not isinstance(
        data,
        list
    ):

        log.error(
            "Binance 24h ticker unavailable"
        )

        return {}

    volumes = {}

    for item in data:

        try:

            symbol = item.get(
                "symbol"
            )

            if not symbol:
                continue

            symbol = symbol.upper()

            if not symbol.endswith(
                "USDT"
            ):
                continue

            volume = safe_float(
                item.get(
                    "quoteVolume",
                    0
                )
            )

            volumes[symbol] = volume

        except Exception:

            continue

    return volumes


# ============================================================
# 10. FINAL SCAN LIST
# ============================================================

def get_scan_coins():

    nobitex_coins = (
        get_nobitex_coins()
    )

    if not nobitex_coins:

        return []

    binance_symbols = (
        get_binance_symbols()
    )

    if not binance_symbols:

        return []

    volumes = (
        get_binance_volumes()
    )

    if not volumes:

        return []

    result = []

    for coin in sorted(
        nobitex_coins
    ):

        symbol = (
            coin + "USDT"
        )

        if symbol not in binance_symbols:

            continue

        volume = volumes.get(
            symbol,
            0.0
        )

        if (
            volume
            < CFG.min_24h_usdt_volume
        ):

            continue

        result.append({
            "coin": coin,
            "symbol": symbol,
            "volume_24h": volume
        })

    result.sort(
        key=lambda item:
        item["volume_24h"],
        reverse=True
    )

    log.info(
        f"Nobitex + Binance markets: "
        f"{len(result)}"
    )

    if result:

        preview = ", ".join(
            item["coin"]
            for item in result[:30]
        )

        log.info(
            f"Scan preview: {preview}"
        )

    else:

        log.error(
            "No common Nobitex/Binance "
            "markets passed volume filter"
        )

    return result# ============================================================
# 11. BINANCE KLINES
# ============================================================

def get_klines(symbol):

    url = (
        CFG.binance_base
        + "/api/v3/klines"
    )

    data = http_get(
        url,
        params={
            "symbol": symbol,
            "interval": CFG.timeframe,
            "limit": CFG.kline_limit
        },
        timeout=20,
        retries=3
    )

    if not isinstance(
        data,
        list
    ):
        return None

    if len(data) < 100:
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
        "taker_base",
        "taker_quote",
        "ignore"
    ]

    try:

        df = pd.DataFrame(
            data,
            columns=columns
        )

        numeric_columns = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume"
        ]

        for column in numeric_columns:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        df["open_time"] = pd.to_numeric(
            df["open_time"],
            errors="coerce"
        )

        df["close_time"] = pd.to_numeric(
            df["close_time"],
            errors="coerce"
        )

        # فقط کندل‌های بسته‌شده
        now_ms = int(
            time.time() * 1000
        )

        df = df[
            df["close_time"] <= now_ms
        ].copy()

        df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
                "volume"
            ],
            inplace=True
        )

        if len(df) < 100:
            return None

        return df.reset_index(
            drop=True
        )

    except Exception as exc:

        log.error(
            f"Kline parsing error "
            f"{symbol}: {exc}"
        )

        return None


# ============================================================
# 12. RSI
# ============================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    average_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    average_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = (
        average_gain
        / average_loss.replace(
            0,
            np.nan
        )
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi.fillna(50)


# ============================================================
# 13. ATR
# ============================================================

def calculate_atr(
    df,
    period=14
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(
        axis=1
    )

    return true_range.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()


# ============================================================
# 14. INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()

    close = df["close"]

    # EMA
    df["ema9"] = close.ewm(
        span=9,
        adjust=False
    ).mean()

    df["ema21"] = close.ewm(
        span=21,
        adjust=False
    ).mean()

    df["ema50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    df["ema200"] = close.ewm(
        span=200,
        adjust=False
    ).mean()

    # RSI
    df["rsi"] = calculate_rsi(
        close,
        CFG.rsi_period
    )

    # MACD
    fast_ema = close.ewm(
        span=CFG.macd_fast,
        adjust=False
    ).mean()

    slow_ema = close.ewm(
        span=CFG.macd_slow,
        adjust=False
    ).mean()

    df["macd"] = (
        fast_ema - slow_ema
    )

    df["macd_signal"] = (
        df["macd"].ewm(
            span=CFG.macd_signal,
            adjust=False
        ).mean()
    )

    df["macd_hist"] = (
        df["macd"]
        - df["macd_signal"]
    )

    # ATR
    df["atr"] = calculate_atr(
        df,
        CFG.atr_period
    )

    # Volume
    df["volume_ma20"] = (
        df["volume"]
        .rolling(20)
        .mean()
    )

    df["volume_ratio"] = (
        df["volume"]
        / df["volume_ma20"]
    )

    return df


# ============================================================
# 15. BTC CONTEXT
# ============================================================

def get_btc_context():

    df = get_klines(
        "BTCUSDT"
    )

    if df is None:

        return {
            "trend": "UNKNOWN",
            "bullish": False,
            "bearish": False
        }

    df = add_indicators(
        df
    )

    row = df.iloc[-1]

    close = safe_float(
        row["close"]
    )

    ema50 = safe_float(
        row["ema50"]
    )

    ema200 = safe_float(
        row["ema200"]
    )

    bullish = (
        close > ema50
        and ema50 > ema200
    )

    bearish = (
        close < ema50
        and ema50 < ema200
    )

    if bullish:

        trend = "BULLISH"

    elif bearish:

        trend = "BEARISH"

    else:

        trend = "NEUTRAL"

    return {
        "trend": trend,
        "bullish": bullish,
        "bearish": bearish
    }


# ============================================================
# 16. RSI DIVERGENCE
# ============================================================

def detect_rsi_divergence(df):

    if len(df) < 30:

        return "NONE"

    prices = df["close"].values
    rsi = df["rsi"].values

    lookback = min(
        40,
        len(df) - 5
    )

    recent_prices = (
        prices[-lookback:]
    )

    recent_rsi = (
        rsi[-lookback:]
    )

    half = lookback // 2

    # --------------------------------------------------------
    # Bullish divergence
    # --------------------------------------------------------

    first_low_index = np.argmin(
        recent_prices[:half]
    )

    second_low_index = np.argmin(
        recent_prices[half:]
    )

    first_price = recent_prices[
        first_low_index
    ]

    second_price = recent_prices[
        half + second_low_index
    ]

    first_rsi = recent_rsi[
        first_low_index
    ]

    second_rsi = recent_rsi[
        half + second_low_index
    ]

    if (
        second_price < first_price
        and second_rsi > first_rsi
    ):

        return "BULLISH"

    # --------------------------------------------------------
    # Bearish divergence
    # --------------------------------------------------------

    first_high_index = np.argmax(
        recent_prices[:half]
    )

    second_high_index = np.argmax(
        recent_prices[half:]
    )

    first_high = recent_prices[
        first_high_index
    ]

    second_high = recent_prices[
        half + second_high_index
    ]

    first_high_rsi = recent_rsi[
        first_high_index
    ]

    second_high_rsi = recent_rsi[
        half + second_high_index
    ]

    if (
        second_high > first_high
        and second_high_rsi < first_high_rsi
    ):

        return "BEARISH"

    return "NONE"# ============================================================
# 17. ANALYZE COIN
# ============================================================

def analyze_coin(
    item,
    btc_context
):

    symbol = item["symbol"]
    coin = item["coin"]

    df = get_klines(
        symbol
    )

    if df is None:
        return None

    df = add_indicators(
        df
    )

    if len(df) < 100:
        return None

    row = df.iloc[-1]
    previous = df.iloc[-2]

    close = safe_float(
        row["close"]
    )

    ema9 = safe_float(
        row["ema9"]
    )

    ema21 = safe_float(
        row["ema21"]
    )

    ema50 = safe_float(
        row["ema50"]
    )

    ema200 = safe_float(
        row["ema200"]
    )

    rsi = safe_float(
        row["rsi"]
    )

    macd = safe_float(
        row["macd"]
    )

    macd_signal = safe_float(
        row["macd_signal"]
    )

    macd_hist = safe_float(
        row["macd_hist"]
    )

    previous_macd = safe_float(
        previous["macd"]
    )

    previous_macd_signal = safe_float(
        previous["macd_signal"]
    )

    atr = safe_float(
        row["atr"]
    )

    volume_ratio = safe_float(
        row["volume_ratio"],
        1.0
    )

    if (
        close <= 0
        or atr <= 0
    ):

        return None

    # ========================================================
    # 18. MARKET STRUCTURE
    # ========================================================

    bullish_structure = (
        ema9 > ema21
        and ema21 > ema50
    )

    bearish_structure = (
        ema9 < ema21
        and ema21 < ema50
    )

    if bullish_structure:

        direction = "LONG"

    elif bearish_structure:

        direction = "SHORT"

    else:

        return None

    # ========================================================
    # 19. SCORE
    # ========================================================

    score = 0

    reasons = []

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    if direction == "LONG":

        if close > ema9:

            score += 12

            reasons.append(
                "قیمت بالای EMA9"
            )

        if ema9 > ema21:

            score += 10

            reasons.append(
                "EMA9 > EMA21"
            )

        if ema21 > ema50:

            score += 8

            reasons.append(
                "EMA21 > EMA50"
            )

        if close > ema200:

            score += 8

            reasons.append(
                "قیمت بالای EMA200"
            )

    else:

        if close < ema9:

            score += 12

            reasons.append(
                "قیمت زیر EMA9"
            )

        if ema9 < ema21:

            score += 10

            reasons.append(
                "EMA9 < EMA21"
            )

        if ema21 < ema50:

            score += 8

            reasons.append(
                "EMA21 < EMA50"
            )

        if close < ema200:

            score += 8

            reasons.append(
                "قیمت زیر EMA200"
            )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if direction == "LONG":

        if 45 <= rsi <= 65:

            score += 12

            reasons.append(
                "RSI مناسب خرید"
            )

        elif 35 <= rsi < 45:

            score += 7

            reasons.append(
                "RSI پایین و قابل بررسی"
            )

        elif rsi < 30:

            score += 9

            reasons.append(
                "RSI اشباع فروش"
            )

        elif rsi > 70:

            score -= 5

    else:

        if 35 <= rsi <= 55:

            score += 12

            reasons.append(
                "RSI مناسب فروش"
            )

        elif 55 < rsi <= 65:

            score += 7

            reasons.append(
                "RSI نسبتاً بالا"
            )

        elif rsi > 70:

            score += 9

            reasons.append(
                "RSI اشباع خرید"
            )

        elif rsi < 30:

            score -= 5

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    if direction == "LONG":

        if macd > macd_signal:

            score += 12

            reasons.append(
                "MACD مثبت"
            )

        if macd_hist > 0:

            score += 5

        if (
            previous_macd <= previous_macd_signal
            and macd > macd_signal
        ):

            score += 6

            reasons.append(
                "کراس صعودی MACD"
            )

    else:

        if macd < macd_signal:

            score += 12

            reasons.append(
                "MACD منفی"
            )

        if macd_hist < 0:

            score += 5

        if (
            previous_macd >= previous_macd_signal
            and macd < macd_signal
        ):

            score += 6

            reasons.append(
                "کراس نزولی MACD"
            )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    if (
        volume_ratio
        >= CFG.min_volume_ratio
    ):

        score += 10

        reasons.append(
            f"حجم {volume_ratio:.2f}x"
        )

    elif volume_ratio >= 0.90:

        score += 4

    # --------------------------------------------------------
    # BTC Context
    # --------------------------------------------------------

    if direction == "LONG":

        if btc_context["bullish"]:

            score += 8

            reasons.append(
                "روند BTC صعودی"
            )

        elif btc_context["bearish"]:

            score -= 8

            reasons.append(
                "روند BTC نزولی"
            )

    else:

        if btc_context["bearish"]:

            score += 8

            reasons.append(
                "روند BTC نزولی"
            )

        elif btc_context["bullish"]:

            score -= 8

            reasons.append(
                "روند BTC صعودی"
            )

    # --------------------------------------------------------
    # RSI Divergence
    # --------------------------------------------------------

    divergence = detect_rsi_divergence(
        df
    )

    if (
        direction == "LONG"
        and divergence == "BULLISH"
    ):

        score += 8

        reasons.append(
            "واگرایی مثبت RSI"
        )

    elif (
        direction == "SHORT"
        and divergence == "BEARISH"
    ):

        score += 8

        reasons.append(
            "واگرایی منفی RSI"
        )

    # ========================================================
    # 20. FINAL SCORE
    # ========================================================

    score = max(
        0,
        min(
            100,
            score
        )
    )

    if score < CFG.min_score:

        return None

    # ========================================================
    # 21. STOP LOSS
    # ========================================================

    if direction == "LONG":

        stop_loss = (
            close
            - atr * CFG.sl_atr_multiplier
        )

        risk_distance = (
            close - stop_loss
        )

    else:

        stop_loss = (
            close
            + atr * CFG.sl_atr_multiplier
        )

        risk_distance = (
            stop_loss - close
        )

    if risk_distance <= 0:

        return None

    sl_percent = (
        risk_distance
        / close
        * 100
    )

    if (
        sl_percent
        > CFG.max_sl_percent
    ):

        return None

    # ========================================================
    # 22. TAKE PROFIT
    # ========================================================

    if direction == "LONG":

        tp1 = (
            close
            + risk_distance * CFG.tp1_rr
        )

        tp2 = (
            close
            + risk_distance * CFG.tp2_rr
        )

    else:

        tp1 = (
            close
            - risk_distance * CFG.tp1_rr
        )

        tp2 = (
            close
            - risk_distance * CFG.tp2_rr
        )

    # ========================================================
    # 23. RISK / POSITION SIZE
    # ========================================================

    risk_usdt = (
        CFG.account_size_usdt
        * CFG.risk_percent
        / 100
    )

    position_usdt = (
        risk_usdt
        / (sl_percent / 100)
    )

    position_usdt = min(
        position_usdt,
        CFG.max_position_usdt,
        CFG.account_size_usdt
    )

    if position_usdt <= 0:

        return None

    quantity = (
        position_usdt
        / close
    )

    # ========================================================
    # 24. CANDLE TIME
    # ========================================================

    candle_time = int(
        row["open_time"]
    )

    # ========================================================
    # 25. RESULT
    # ========================================================

    return {

        "coin": coin,

        "symbol": symbol,

        "direction": direction,

        "score": int(score),

        "entry": close,

        "sl": stop_loss,

        "tp1": tp1,

        "tp2": tp2,

        "sl_percent": sl_percent,

        "risk_usdt": risk_usdt,

        "position_usdt": position_usdt,

        "quantity": quantity,

        "rsi": rsi,

        "macd": macd,

        "macd_signal": macd_signal,

        "macd_hist": macd_hist,

        "atr": atr,

        "volume_ratio": volume_ratio,

        "divergence": divergence,

        "btc_trend": btc_context["trend"],

        "candle_time": candle_time,

        "reasons": reasons

    }# ============================================================
# 26. FORMAT SIGNAL
# ============================================================

def format_signal(signal):

    if signal["direction"] == "LONG":
        title = "🟢 <b>LONG SIGNAL</b>"
    else:
        title = "🔴 <b>SHORT SIGNAL</b>"

    if signal["score"] >= CFG.strong_score:
        quality = "🔥 STRONG"
    else:
        quality = "⚡ NORMAL"

    candle_dt = datetime.fromtimestamp(
        signal["candle_time"] / 1000,
        tz=CFG.iran_timezone
    )

    reasons = signal["reasons"][:7]

    reasons_text = "\n".join(
        f"• {reason}"
        for reason in reasons
    )

    return f"""
{title}
<b>{signal["coin"]}/USDT</b>

کیفیت: <b>{quality}</b>
📊 Score: <b>{signal["score"]}/100</b>

💰 Entry:
<b>{signal["entry"]:.8g}</b>

🛑 Stop Loss:
<b>{signal["sl"]:.8g}</b>
({signal["sl_percent"]:.2f}%)

🎯 TP1:
<b>{signal["tp1"]:.8g}</b>

🎯 TP2:
<b>{signal["tp2"]:.8g}</b>

📈 RSI:
<b>{signal["rsi"]:.2f}</b>

📊 Volume:
<b>{signal["volume_ratio"]:.2f}x</b>

₿ BTC Trend:
<b>{signal["btc_trend"]}</b>

🔄 RSI Divergence:
<b>{signal["divergence"]}</b>

💵 Risk:
<b>${signal["risk_usdt"]:.2f}</b>

📦 Position:
<b>${signal["position_usdt"]:.2f}</b>

🔢 Quantity:
<b>{signal["quantity"]:.8g}</b>

<b>دلایل:</b>
{reasons_text}

⏰ Candle:
{candle_dt.strftime("%Y-%m-%d %H:%M")}

⚠️ فقط سیگنال و محاسبه ریسک؛ معامله خودکار انجام نمی‌شود.
""".strip()


# ============================================================
# 27. SCAN REPORT
# ============================================================

def format_scan_report(
    total_coins,
    analyzed,
    candidates,
    sent_count,
    btc_context
):

    now = datetime.now(
        CFG.iran_timezone
    )

    return f"""
📊 <b>گزارش اسکن 4H</b>

⏰ {now.strftime("%Y-%m-%d %H:%M")}

🪙 ارزهای قابل اسکن:
<b>{total_coins}</b>

🔎 بررسی‌شده:
<b>{analyzed}</b>

📌 کاندیدا:
<b>{candidates}</b>

🚨 سیگنال جدید:
<b>{sent_count}</b>

₿ روند BTC:
<b>{btc_context["trend"]}</b>

📊 حداقل Score:
<b>{CFG.min_score}/100</b>

ℹ️ این ربات فقط تحلیل و محاسبه ریسک انجام می‌دهد و معامله خودکار ندارد.
""".strip()


# ============================================================
# 28. MAIN
# ============================================================

def main():

    log.info(
        "======================================"
    )

    log.info(
        "Starting Crypto Signal Bot"
    )

    log.info(
        f"Timeframe: {CFG.timeframe}"
    )

    log.info(
        "======================================"
    )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    if not CFG.telegram_token:

        log.error(
            "TELEGRAM_TOKEN is missing"
        )

        return

    if not CFG.telegram_chat_id:

        log.error(
            "TELEGRAM_CHAT_ID is missing"
        )

        return

    # --------------------------------------------------------
    # State
    # --------------------------------------------------------

    state = load_state()

    state = cleanup_state(
        state
    )

    # --------------------------------------------------------
    # Get coins
    # --------------------------------------------------------

    coins = get_scan_coins()

    if not coins:

        log.error(
            "No coins available for scanning"
        )

        send_telegram(
            "⚠️ <b>Crypto Bot</b>\n\n"
            "لیست ارزهای قابل اسکن دریافت نشد."
        )

        return

    # --------------------------------------------------------
    # BTC context
    # --------------------------------------------------------

    btc_context = get_btc_context()

    log.info(
        f"BTC trend: "
        f"{btc_context['trend']}"
    )

    # --------------------------------------------------------
    # Analyze coins
    # --------------------------------------------------------

    results = []

    analyzed = 0

    with ThreadPoolExecutor(
        max_workers=CFG.max_workers
    ) as executor:

        futures = {
            executor.submit(
                analyze_coin,
                item,
                btc_context
            ): item
            for item in coins
        }

        for future in as_completed(
            futures
        ):

            item = futures[future]

            try:

                result = future.result()

                analyzed += 1

                if result is not None:

                    results.append(
                        result
                    )

            except Exception as exc:

                log.error(
                    f"Analysis error "
                    f"{item.get('symbol')}: "
                    f"{exc}"
                )

    # --------------------------------------------------------
    # Sort by score
    # --------------------------------------------------------

    results.sort(
        key=lambda item:
        item["score"],
        reverse=True
    )

    log.info(
        f"Analysis finished. "
        f"Candidates: {len(results)}"
    )

    # --------------------------------------------------------
    # Send new signals
    # --------------------------------------------------------

    sent_count = 0

    for signal in results:

        if (
            sent_count
            >= CFG.max_signals_per_run
        ):
            break

        if signal_already_sent(
            state,
            signal["symbol"],
            signal["direction"],
            signal["candle_time"]
        ):

            log.info(
                f"Duplicate ignored: "
                f"{signal['symbol']} "
                f"{signal['direction']}"
            )

            continue

        message = format_signal(
            signal
        )

        success = send_telegram(
            message
        )

        if success:

            mark_signal_sent(
                state,
                signal["symbol"],
                signal["direction"],
                signal["candle_time"]
            )

            sent_count += 1

            log.info(
                f"Signal sent: "
                f"{signal['symbol']} "
                f"{signal['direction']} "
                f"score={signal['score']}"
            )

        else:

            log.error(
                f"Signal NOT sent: "
                f"{signal['symbol']}"
            )

    # --------------------------------------------------------
    # Save state
    # --------------------------------------------------------

    save_state(
        state
    )

    # --------------------------------------------------------
    # Telegram summary
    # --------------------------------------------------------

    if CFG.send_no_signal_report:

        report = format_scan_report(
            len(coins),
            analyzed,
            len(results),
            sent_count,
            btc_context
        )

        send_telegram(
            report
        )

    # --------------------------------------------------------
    # Final log
    # --------------------------------------------------------

    log.info(
        "======================================"
    )

    log.info(
        f"Coins: {len(coins)}"
    )

    log.info(
        f"Analyzed: {analyzed}"
    )

    log.info(
        f"Candidates: {len(results)}"
    )

    log.info(
        f"New signals sent: {sent_count}"
    )

    log.info(
        "Bot finished successfully"
    )

    log.info(
        "======================================"
    )


# ============================================================
# 29. START
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        log.info(
            "Bot stopped."
        )

    except Exception as exc:

        log.exception(
            f"Fatal error: {exc}"
        )

        try:

            send_telegram(
                "🚨 <b>Crypto Bot Error</b>\n\n"
                f"<code>{str(exc)[:1000]}</code>"
            )

        except Exception:

            pass

        raise
