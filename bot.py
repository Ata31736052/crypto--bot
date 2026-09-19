# ============================================
# 🤖 ربات هوشمند و حرفه‌ای سیگنال‌دهی کریپتو (نسخه Pro - تایم ۴ ساعته)
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime, timezone, timedelta
warnings.filterwarnings('ignore')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "signals_state.json"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# تنظیم منطقه زمانی رسمی ایران (UTC+3:30)
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

COINS = [
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'AVAX', 'LINK', 'DOT',
    'LTC', 'BCH', 'ETC', 'XLM', 'UNI', 'FIL', 'TRX', 'ATOM', 'NEAR', 'AAVE',
    'SUI', 'APT', 'ARB', 'OP', 'MATIC', 'SEI', 'INJ', 'TIA', 'STX',
    'FTM', 'ALGO', 'EGLD', 'ROSE', 'MINA', 'IMX', 'MNT', 'RON', 'CELO', 'FLOW',
    'FET', 'RNDR', 'TAO', 'AGIX', 'OCEAN', 'AKT',
    'PEPE', 'WIF', 'BONK', 'FLOKI', 'SHIB', 'MEME', 'NOT', 'ORDI', 'SATS', 'BOME',
    'RUNE', 'ICP', 'KAS', 'JUP'
]

def http(url, t=10, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, context=CTX, timeout=t) as x:
                return json.loads(x.read().decode())
        except Exception:
            if i < retries - 1:
                time.sleep(0.5)
            continue
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
    live_price = get_ticker_price(sym)
    
    url_bn = f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval={tf}&limit={limit}"
    d = http(url_bn)
    if d and isinstance(d, list) and len(d) > 1:
        if not live_price:
            live_price = float(d[-1][4])
            
        opens = [float(c[1]) for c in d]
        highs = [float(c[2]) for c in d]
        lows = [float(c[3]) for c in d]
        closes = [float(c[4]) for c in d]
        vols = [float(c[5]) for c in d]
        return {'o': opens, 'h': highs, 'l': lows, 'p': closes, 'v': vols, 'price': live_price}

    url_kc = f"https://api.kucoin.com/api/v1/market/candles?symbol={sym}-USDT&type={tf if tf!='1d' else '1day'}"
    d = http(url_kc)
    if d and d.get('code') == '200000' and isinstance(d.get('data'), list) and len(d['data']) > 1:
        data = d['data'][:limit]
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

def rsi_series(p, n=14):
    if len(p) < n + 1: return []
    gains, losses = [], []
    for i in range(1, len(p)):
        chg = p[i] - p[i - 1]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    rsis = []
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        if avg_l == 0:
            rsis.append(100.0)
        else:
            rsis.append(100.0 - (100.0 / (1.0 + (avg_g / avg_l))))
    return rsis

def rsi(p, n=14):
    s = rsi_series(p, n)
    return s[-1] if s else 50.0

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

def check_price_action(o, h, l, c):
    if len(c) < 2: return "خنثی"
    body = abs(c[-1] - o[-1])
    candle_range = h[-1] - l[-1]
    if candle_range == 0: return "خنثی"
    
    upper_wick = h[-1] - max(o[-1], c[-1])
    lower_wick = min(o[-1], c[-1]) - l[-1]

    if lower_wick > (2.0 * body) and lower_wick > (0.5 * candle_range):
        return "پین‌بار صعودی 🟢"
    if upper_wick > (2.0 * body) and upper_wick > (0.5 * candle_range):
        return "پین‌بار نزولی 🔴"

    if c[-1] > o[-1] and c[-2] < o[-2] and c[-1] > o[-2] and o[-1] < c[-2]:
        return "انگالفینگ صعودی 🟢"
    if c[-1] < o[-1] and c[-2] > o[-2] and c[-1] < o[-2] and o[-1] > c[-2]:
        return "انگالفینگ نزولی 🔴"

    return "تایید عادی"

def check_rsi_divergence(p, rsi_vals):
    if len(p) < 15 or len(rsi_vals) < 15: return "بدون واگرایی"
    if p[-1] < min(p[-10:-1]) and rsi_vals[-1] > min(rsi_vals[-10:-1]):
        return "واگرایی صعودی RSI 🟢"
    if p[-1] > max(p[-10:-1]) and rsi_vals[-1] < max(rsi_vals[-10:-1]):
        return "واگرایی نزولی RSI 🔴"
    return "بدون واگرایی"

def get_btc_macro_trend():
    k1d = klines('BTC', '1d', limit=60)
    if not k1d: return 'NEUTRAL'
    e50 = ema(k1d['p'], 50)
    if not e50: return 'NEUTRAL'
    return 'BULLISH' if k1d['price'] > e50 else 'BEARISH'

def analyze_tf(sym, tf='4h', btc_trend='NEUTRAL'):
    k_data = klines(sym, tf, limit=220)
    if not k_data: return None

    p, h, l, o, v = k_data['p'], k_data['h'], k_data['l'], k_data['o'], k_data['v']
    curr_price = k_data['price']
    
    e9, e21, e50, e200 = ema(p, 9), ema(p, 21), ema(p, 50), ema(p, 200)
    rsi_vals = rsi_series(p)
    rsi_val = rsi_vals[-1] if rsi_vals else 50.0
    atr_val = atr(h, l, p, 14)
    macd_val, signal_val, hist_val = macd(p)
    pa_status = check_price_action(o, h, l, p)
    div_status = check_rsi_divergence(p, rsi_vals)

    if not (e9 and e21 and e50 and e200 and atr_val and macd_val is not None):
        return None

    trend_icon = "🟢" if e9 > e21 else "🔴"
    base_info = {'sym': sym, 'tf': tf.upper(), 'price': curr_price, 'rsi': round(rsi_val, 1), 'icon': trend_icon}

    score_buy, score_sell = 0, 0

    # ۱. فیلتر میانگین‌های متحرک
    if e9 > e21: score_buy += 3
    if e21 > e50: score_buy += 2
    if e9 < e21: score_sell += 3
    if e21 < e50: score_sell += 2

    if curr_price > e200: score_buy += 2
    else: score_sell += 2

    # ۲. فیلتر RSI
    if 40 <= rsi_val <= 68: score_buy += 2
    if 32 <= rsi_val <= 60: score_sell += 2

    # ۳. فیلتر MACD
    macd_status = "خنثی"
    if hist_val > 0:
        score_buy += 2
        macd_status = "صعودی 🟢"
    elif hist_val < 0:
        score_sell += 2
        macd_status = "نزولی 🔴"

    # ۴. فیلتر حجم هوشمند
    avg_vol = sum(v[-21:-1]) / 20 if len(v) >= 21 else sum(v) / len(v)
    vol_ratio = round(v[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
    if vol_ratio >= 1.3:
        if p[-1] >= o[-1]: score_buy += 2
        else: score_sell += 2

    # ۵. فیلتر پرایس اکشن و واگرایی
    if "صعودی" in pa_status: score_buy += 2
    if "نزولی" in pa_status: score_sell += 2

    if "صعودی" in div_status: score_buy += 2
    if "نزولی" in div_status: score_sell += 2

    # 🎯 تنظیم آستانه به ۱۰ برای تعادل دقیق بین دقت و تعداد سیگنال
    min_score = 10

    direction = None
    if score_buy >= min_score and score_buy > score_sell and btc_trend != 'BEARISH':
        direction = 'buy'
        final_score = score_buy
    elif score_sell >= min_score and score_sell > score_buy and btc_trend != 'BULLISH':
        direction = 'sell'
        final_score = score_sell

    if not direction:
        return {'is_signal': False, **base_info}

    recent_high = max(h[-5:])
    recent_low = min(l[-5:])

    if direction == 'buy':
        sl = min(curr_price - (1.3 * atr_val), recent_low)
        tp1 = curr_price + (1.5 * abs(curr_price - sl))
        tp2 = curr_price + (2.8 * abs(curr_price - sl))
        sig_text = "خرید (LONG)"
    else:
        sl = max(curr_price + (1.3 * atr_val), recent_high)
        tp1 = curr_price - (1.5 * abs(sl - curr_price))
        tp2 = curr_price - (2.8 * abs(sl - curr_price))
        sig_text = "فروش (SHORT)"

    risk = abs(curr_price - sl)
    reward = abs(tp2 - curr_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.5

    account_size = 1000.0
    risk_amount = account_size * 0.01
    stop_pct = risk / curr_price
    position_value = risk_amount / stop_pct if stop_pct > 0 else account_size
    
    max_lev = 10
    leverage = max(1, min(max_lev, int(position_value / account_size)))

    now_ir = datetime.now(IRAN_TZ).strftime('%H:%M')

    return {
        'is_signal': True,
        'sym': sym,
        'tf': tf.upper(),
        'price': curr_price, 
        'dir': direction,
        'sig': sig_text, 
        'score': final_score,
        'rsi': round(rsi_val, 1),
        'macd_status': macd_status,
        'pa_status': pa_status,
        'div_status': div_status,
        'vol_ratio': vol_ratio,
        'atr': fp(atr_val),
        'sl': sl, 
        'tp1': tp1, 
        'tp2': tp2,
        'rr': rr_ratio,
        'pos_size': round(position_value, 1),
        'leverage': leverage,
        'time': now_ir,
        'icon': trend_icon
    }

def send(msg):
    if not TOKEN or not CHAT: 
        print("❌ Error: Telegram Token or Chat ID not found.")
        return False
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
    except Exception as e:
        print(f"❌ Telegram Send Error: {e}")
        return False

def fp(n):
    if n >= 1000: return f"{n:,.2f}"
    if n >= 1: return f"{n:,.4f}"
    return f"{n:.6f}"

def fmt(a):
    tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{a['sym']}USDT"
    tf_tag = "🚀 [سیگنال ۴ ساعته Pro - پایش ساعتی]"
    
    badge = ""
    if a['score'] >= 12:
        badge = "\n🔥 <b>سیگنال VIP (امتیاز بالای ۱۲ - فوق‌العاده قوی)</b>"

    return (
        f"{a['icon']} <b>#سیگنال_{a['tf']}_{a['sym']} | USDT</b>\n"
        f"<i>{tf_tag}</i>{badge}\n\n"
        f"🎯 جهت معامله: <b>{a['sig']}</b>\n"
        f"📊 قدرت کل سیگنال: <b>{a['score']} / 18</b>\n"
        f"💰 قیمت ورود: <b>{fp(a['price'])} $</b>\n"
        f"⚖️ نسبت R/R: <b>1:{a['rr']}</b>\n"
        f"📐 اهرم پیشنهادی: <b>{a['leverage']}x</b> | حجم پوزیشن: <b>{a['pos_size']} $</b>\n\n"
        f"🛑 حد زیان (SL): <b>{fp(a['sl'])} $</b>\n"
        f"🎯 حد سود اول (TP1): <b>{fp(a['tp1'])} $</b>\n"
        f"🚀 حد سود دوم (TP2): <b>{fp(a['tp2'])} $</b>\n\n"
        f"🔍 <b>تاییدکننده‌های حرفه‌ای:</b>\n"
        f"• پرایس اکشن: <b>{a['pa_status']}</b>\n"
        f"• واگرایی: <b>{a['div_status']}</b>\n"
        f"• حجم معاملات: <b>{a['vol_ratio']}x نسبت به میانگین</b>\n"
        f"• شاخص RSI: <b>{a['rsi']}</b> | مکدی: <b>{a['macd_status']}</b>\n\n"
        f"🔗 <a href='{tv_link}'>مشاهده نمودار زنده در TradingView</a>\n"
        f"📅 زمان ثبت (ایران): {a['time']}"
    )

if __name__ == "__main__":
    print("شروع اسکن ساعتی بازار ۴ ساعته کریپتو...")
    state = load_state()
    btc_trend = get_btc_macro_trend()
    
    signals = []
    
    for s in COINS:
        a4h = analyze_tf(s, '4h', btc_trend)
        state_key = f"{s}_4h"
        
        if a4h and a4h['is_signal']:
            # ثبت سیگنال جدید در صورت عدم وجود سیگنال هم‌جهت قبلی
            if state.get(state_key, {}).get('dir') != a4h['dir']:
                signals.append(a4h)
                state[state_key] = {'dir': a4h['dir'], 'time': a4h['time']}
        else:
            # پاک کردن وضعیت قبلی در صورت خروج از شرایط سیگنال برای پذیرش سیگنال‌های بعدی
            if state_key in state:
                del state[state_key]

        time.sleep(0.08)

    save_state(state)

    if signals:
        for sig in signals:
            send(fmt(sig))
            time.sleep(0.3)
        print(f"تعداد {len(signals)} سیگنال جدید ارسال شد.")
    else:
        now_ir = datetime.now(IRAN_TZ).strftime('%H:%M')
        btc_icon = "🟢 صعودی" if btc_trend == 'BULLISH' else ("🔴 نزولی" if btc_trend == 'BEARISH' else "⚪️ خنثی")
        msg = f"📊 <b>گزارش اسکن ساعتی ۴H ({now_ir} به وقت ایران)</b>\n\n"
        msg += f"🌐 روند کلان بیت‌کوین (روزانه): <b>{btc_icon}</b>\n"
        msg += "• وضعیت ۴ ساعته: <i>در این ساعت موقعیت جدیدی با شرایط حد نصاب ۱۰ احراز نگردید.</i>\n\n"
        msg += "🔍 اسکن بعدی سر ساعت بعدی انجام خواهد شد."
        send(msg)
        print("سیگنال جدیدی یافت نشد؛ گزارش ساعتی به تلگرام ارسال گردید.")

    print("پایان اسکن.")
