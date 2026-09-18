# ============================================
# 🤖 ربات جامع و هوشمند سیگنال‌دهی کریپتو - نسخه Ultima 8-in-1 (60 Coins)
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "signals_state.json"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

COINS = [
    # Top Market Cap & Major Coins
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'AVAX', 'LINK', 'DOT',
    'LTC', 'BCH', 'ETC', 'XLM', 'UNI', 'FIL', 'TRX', 'ATOM', 'NEAR', 'AAVE',
    
    # Layer 1 & Layer 2 Ecosystems
    'SUI', 'APT', 'ARB', 'OP', 'MATIC', 'SEI', 'INJ', 'TIA', 'STX', 'SDA',
    'FTM', 'ALGO', 'EGLD', 'ROSE', 'MINA', 'IMX', 'MNT', 'RON', 'CELO', 'FLOW',
    
    # AI & Big Data
    'FET', 'RNDR', 'TAO', 'AGIX', 'OCEAN', 'AKT',
    
    # Popular Meme Coins & High Momentum
    'PEPE', 'WIF', 'BONK', 'FLOKI', 'SHIB', 'MEME', 'NOT', 'ORDI', 'SATS', 'BOME',
    
    # Other Solid Altcoins
    'RUNE', 'ICP', 'KAS', 'JUP'
]

def http(url, t=10):
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, context=CTX, timeout=t) as x:
            return json.loads(x.read().decode())
    except Exception:
        return None

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def get_ticker_price(sym):
    url_bn = f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}USDT"
    d = http(url_bn)
    if d and isinstance(d, dict) and 'price' in d:
        return float(d['price'])
    
    url_kc = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={sym}-USDT"
    d_kc = http(url_kc)
    if d_kc and d_kc.get('code') == '200000' and isinstance(d_kc.get('data'), dict) and 'price' in d_kc['data']:
        return float(d_kc['data']['price'])
        
    return None

def klines(sym, tf='4h', limit=220):
    live_price = get_ticker_price(sym) if tf == '4h' else None
    
    url_bn = f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval={tf}&limit={limit}"
    d = http(url_bn)
    if d and isinstance(d, list) and len(d) > 1:
        if not live_price:
            live_price = float(d[-1][4])
            
        d = d[:-1]
        opens = [float(c[1]) for c in d]
        highs = [float(c[2]) for c in d]
        lows = [float(c[3]) for c in d]
        closes = [float(c[4]) for c in d]
        vols = [float(c[5]) for c in d]
        return {'o': opens, 'h': highs, 'l': lows, 'p': closes, 'v': vols, 'price': live_price}

    url_kc = f"https://api.kucoin.com/api/v1/market/candles?symbol={sym}-USDT&type={tf if tf!='1d' else '1day'}"
    d = http(url_kc)
    if d and d.get('code') == '200000' and isinstance(d.get('data'), list) and len(d['data']) > 1:
        data = d['data'][1:limit]
        data.reverse()
        
        if not live_price:
            live_price = float(data[-1][2])
            
        opens = [float(c[1]) for c in data]
        closes = [float(c[2]) for c in data]
        highs = [float(c[3]) for c in data]
        lows = [float(c[4]) for c in data]
        vols = [float(c[5]) for c in data]
        return {'o': opens, 'h': highs, 'l': lows, 'p': closes, 'v': vols, 'price': live_price}

    return None

def ema_series(p, n):
    if len(p) < n: return []
    k = 2 / (n + 1)
    emas = [sum(p[:n]) / n]
    for x in p[n:]:
        emas.append(x * k + emas[-1] * (1 - k))
    return emas

def ema(p, n):
    s = ema_series(p, n)
    return s[-1] if s else None

def macd(p, fast=12, slow=26, signal=9):
    if len(p) < slow + signal: return None, None, None
    ema_fast = ema_series(p, fast)
    ema_slow = ema_series(p, slow)
    ema_fast = ema_fast[slow - fast:]
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema_series(macd_line, signal)
    if not signal_line: return None, None, None
    curr_macd = macd_line[-1]
    curr_signal = signal_line[-1]
    curr_hist = curr_macd - curr_signal
    return curr_macd, curr_signal, curr_hist

def rsi(p, n=14):
    if len(p) < n + 1: return 50.0
    gains, losses = [], []
    for i in range(1, len(p)):
        chg = p[i] - p[i - 1]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_g / avg_l)))

def atr(highs, lows, closes, n=14):
    if len(closes) < n + 1: return None
    tr_list = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    return sum(tr_list[-n:]) / n

def get_btc_macro_trend():
    k1d = klines('BTC', '1d', limit=60)
    if not k1d:
        return 'NEUTRAL'
    e50 = ema(k1d['p'], 50)
    if not e50:
        return 'NEUTRAL'
    return 'BULLISH' if k1d['price'] > e50 else 'BEARISH'

def analyze(sym, btc_trend):
    k4h = klines(sym, '4h', limit=220)
    if not k4h:
        return None

    p4h, h4h, l4h, o4h, v4h = k4h['p'], k4h['h'], k4h['l'], k4h['o'], k4h['v']
    curr_price = k4h['price']
    
    e9 = ema(p4h, 9)
    e21 = ema(p4h, 21)
    e50 = ema(p4h, 50)
    e200 = ema(p4h, 200)
    rsi4h = rsi(p4h)
    atr4h = atr(h4h, l4h, p4h, 14)
    macd_val, signal_val, hist_val = macd(p4h)

    if not (e9 and e21 and e50 and e200 and atr4h and macd_val is not None):
        return None

    body = abs(p4h[-1] - o4h[-1])
    candle_range = h4h[-1] - l4h[-1]
    if candle_range > 0 and (body / candle_range) < 0.15:
        trend_icon = "🟢" if e9 > e21 else "🔴"
        return {'is_signal': False, 'sym': sym, 'price': curr_price, 'rsi4h': round(rsi4h, 1), 'icon': trend_icon}

    score_buy = 0
    score_sell = 0

    if e9 > e21: score_buy += 3
    if e21 > e50: score_buy += 2
    if e9 < e21: score_sell += 3
    if e21 < e50: score_sell += 2

    if curr_price > e200:
        score_buy += 2
    else:
        score_sell += 2

    if 45 < rsi4h < 68: score_buy += 3
    if 32 < rsi4h < 55: score_sell += 3

    macd_status = "خنثی"
    if hist_val > 0:
        score_buy += 2
        macd_status = "صعودی 🟢"
    elif hist_val < 0:
        score_sell += 2
        macd_status = "نزولی 🔴"

    avg_vol = sum(v4h[-21:-1]) / 20 if len(v4h) >= 21 else sum(v4h) / len(v4h)
    is_green_candle = p4h[-1] > o4h[-1]
    if v4h[-1] > (1.5 * avg_vol):
        if is_green_candle: score_buy += 2
        else: score_sell += 2
    elif v4h[-1] > avg_vol:
        if is_green_candle: score_buy += 1
        else: score_sell += 1

    if candle_range > 0:
        lower_shadow = min(p4h[-1], o4h[-1]) - l4h[-1]
        upper_shadow = h4h[-1] - max(p4h[-1], o4h[-1])
        if lower_shadow > (2 * body) and lower_shadow > (0.5 * candle_range):
            score_buy += 2
        if upper_shadow > (2 * body) and upper_shadow > (0.5 * candle_range):
            score_sell += 2

    direction = None
    if score_buy >= 8 and score_buy > score_sell and btc_trend != 'BEARISH' and curr_price > e200:
        direction = 'buy'
        final_score = score_buy
    elif score_sell >= 8 and score_sell > score_buy and btc_trend != 'BULLISH' and curr_price < e200:
        direction = 'sell'
        final_score = score_sell

    trend_icon = "🟢" if e9 > e21 else "🔴"
    base_info = {'sym': sym, 'price': curr_price, 'rsi4h': round(rsi4h, 1), 'icon': trend_icon}

    if not direction:
        return {'is_signal': False, **base_info}

    recent_high = max(h4h[-20:])
    recent_low = min(l4h[-20:])
    if direction == 'buy' and (recent_high - curr_price) < atr4h:
        return {'is_signal': False, **base_info}
    if direction == 'sell' and (curr_price - recent_low) < atr4h:
        return {'is_signal': False, **base_info}

    if direction == 'buy':
        sl = curr_price - (1.5 * atr4h)
        tp1 = curr_price + (2.0 * atr4h)
        tp2 = curr_price + (4.0 * atr4h)
        sig_text = "خرید (LONG)"
    else:
        sl = curr_price + (1.5 * atr4h)
        tp1 = curr_price - (2.0 * atr4h)
        tp2 = curr_price - (4.0 * atr4h)
        sig_text = "فروش (SHORT)"

    risk = abs(curr_price - sl)
    reward = abs(tp2 - curr_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.0

    account_size = 1000.0
    risk_amount = account_size * 0.01
    stop_pct = risk / curr_price
    position_value = risk_amount / stop_pct if stop_pct > 0 else account_size
    leverage = max(1, min(10, int(position_value / account_size)))

    return {
        'is_signal': True,
        'sym': sym, 
        'price': curr_price, 
        'dir': direction,
        'sig': sig_text, 
        'score': final_score,
        'rsi4h': round(rsi4h, 1),
        'macd_status': macd_status,
        'atr4h': fp(atr4h),
        'sl': sl, 
        'tp1': tp1, 
        'tp2': tp2,
        'rr': rr_ratio,
        'pos_size': round(position_value, 1),
        'leverage': leverage,
        'time': datetime.now().strftime('%H:%M'),
        'icon': trend_icon
    }

def send(msg):
    if not TOKEN or not CHAT: return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        d = json.dumps({
            "chat_id": CHAT, 
            "text": msg, 
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }).encode()
        req = urllib.request.Request(url, data=d, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, context=CTX, timeout=10) as x:
            return x.status == 200
    except Exception:
        return False

def fp(n):
    if n >= 1000: return f"{n:,.2f}"
    if n >= 1: return f"{n:,.3f}"
    return f"{n:.5f}"

def fmt(a):
    tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{a['sym']}USDT"
    
    return (
        f"{a['icon']} <b>#سیگنال_4ساعته_{a['sym']} | USDT</b>\n\n"
        f"🎯 جهت معامله: <b>{a['sig']}</b>\n"
        f"📊 قدرت سیگنال: <b>{a['score']} / 15</b>\n"
        f"💰 قیمت ورود: <b>{fp(a['price'])} $</b>\n"
        f"⚖️ نسبت R/R: <b>1:{a['rr']}</b>\n"
        f"📐 اهرم پیشنهادی: <b>{a['leverage']}x</b> | حجم: <b>{a['pos_size']} $</b>\n\n"
        f"🛑 حد زیان (1.5x ATR): <b>{fp(a['sl'])} $</b>\n"
        f"🎯 حد سود اول: <b>{fp(a['tp1'])} $</b>\n"
        f"🚀 حد سود دوم: <b>{fp(a['tp2'])} $</b>\n\n"
        f"📈 وضعیت RSI: <b>{a['rsi4h']}</b> | MACD: <b>{a['macd_status']}</b>\n"
        f"🔗 <a href='{tv_link}'>مشاهده نمودار در TradingView</a>\n"
        f"📅 زمان ثبت: {a['time']}"
    )

if __name__ == "__main__":
    print("شروع اسکن پیشرفته بازار...")
    state = load_state()
    btc_trend = get_btc_macro_trend()
    
    signals = []
    market_summary = []
    
    for s in COINS:
        a = analyze(s, btc_trend)
        if a:
            if a['is_signal']:
                last_dir = state.get(s, {}).get('dir')
                if last_dir != a['dir']:
                    signals.append(a)
                    state[s] = {'dir': a['dir'], 'time': a['time']}
            market_summary.append(a)
        time.sleep(0.1)

    save_state(state)

    if signals:
        for sig in signals:
            send(fmt(sig))
            time.sleep(0.3)
    else:
        now = datetime.now().strftime('%H:%M')
        if market_summary:
            avg_rsi_4h = round(sum(item['rsi4h'] for item in market_summary) / len(market_summary), 1)
            btc_icon = "🟢 صعودی" if btc_trend == 'BULLISH' else ("🔴 نزولی" if btc_trend == 'BEARISH' else "⚪️ خنثی")
            
            msg = f"📊 <b>گزارش بازار کریپتو (۴ ساعته - {now})</b>\n\n"
            msg += f"🌐 روند کلان بیت‌کوین (1D): <b>{btc_icon}</b>\n"
            msg += f"• میانگین RSI بازار: <b>{avg_rsi_4h}</b>\n"
            msg += "• وضعیت: <i>سیگنال جدید و تاییدشده‌ای یافت نشد.</i>\n\n"
            
            for item in market_summary:
                msg += f"{item['icon']} <b>{item['sym']}</b>: {fp(item['price'])}$ | RSI: {item['rsi4h']}\n"
                
            msg += "\n🔍 اسکن بعدی سر ۴ ساعت انجام می‌شود."
        else:
            msg = f"⚠️ <b>خطا در دریافت داده‌ها ({now})</b>\n\nاتصال به سرورهای بازار برقرار نشد."

        send(msg)

    print("پایان اسکن.")
                
