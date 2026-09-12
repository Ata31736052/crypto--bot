# ============================================
# 🤖 ربات سیگنال ارز دیجیتال - همراه با گزارش وضعیت بازار
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# ۲۰ ارز برتر
COINS = [
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 
    'ADA', 'DOGE', 'AVAX', 'LINK', 'DOT', 
    'NEAR', 'SUI', 'APT', 'ARB', 'OP',
    'PEPE', 'FET', 'RNDR', 'INJ', 'MATIC'
]

def http(url, t=5):
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(req, context=CTX, timeout=t) as x:
            return json.loads(x.read().decode())
    except Exception:
        return None

def klines(sym, tf):
    url = f"https://api.binance.com/api/v3/klines?symbol={sym}USDT&interval={tf}&limit=50"
    d = http(url)
    if not d or not isinstance(d, list):
        return None
    closes = [float(c[4]) for c in d]
    highs = [float(c[2]) for c in d]
    lows = [float(c[3]) for c in d]
    return {'p': closes, 'h': highs, 'l': lows, 'price': closes[-1]}

def ema(p, n):
    if len(p) < n: return None
    k = 2 / (n + 1)
    e = sum(p[:n]) / n
    for x in p[n:]:
        e = x * k + e * (1 - k)
    return e

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

def analyze(sym):
    k4 = klines(sym, '4h')
    k1d = klines(sym, '1d')
    
    if not k4 or not k1d:
        return None

    p4, p1d = k4['p'], k1d['p']
    
    e9_4 = ema(p4, 9)
    e21_4 = ema(p4, 21)
    e50_4 = ema(p4, 50)
    rsi4 = rsi(p4)
    
    e9_1d = ema(p1d, 9)
    e21_1d = ema(p1d, 21)
    rsi1d = rsi(p1d)

    if not (e9_4 and e21_4 and e50_4):
        return None

    score_buy = 0
    score_sell = 0

    if e9_4 > e21_4: score_buy += 3
    if e21_4 > e50_4: score_buy += 2
    if e9_4 < e21_4: score_sell += 3
    if e21_4 < e50_4: score_sell += 2

    if 40 < rsi4 < 70: score_buy += 2
    if 30 < rsi4 < 60: score_sell += 2

    if e9_1d and e21_1d:
        if e9_1d > e21_1d: score_buy += 3
        if e9_1d < e21_1d: score_sell += 3

    direction = None
    final_score = 0
    
    if score_buy >= 5 and score_buy > score_sell:
        direction = 'buy'
        final_score = score_buy
    elif score_sell >= 5 and score_sell > score_buy:
        direction = 'sell'
        final_score = score_sell

    curr_price = k4['price']

    # بازگرداندن خلاصه اطلاعات حتی بدون سیگنال
    base_info = {
        'sym': sym, 'price': curr_price, 'rsi4': round(rsi4, 1),
        'trend4': 'صعودی' if e9_4 > e21_4 else 'نزولی'
    }

    if not direction:
        return {'is_signal': False, **base_info}

    if direction == 'buy':
        sl = curr_price * 0.975
        tp1 = curr_price * 1.025
        tp2 = curr_price * 1.05
        sig_text = "خرید (LONG)"
    else:
        sl = curr_price * 1.025
        tp1 = curr_price * 0.975
        tp2 = curr_price * 0.95
        sig_text = "فروش (SHORT)"

    return {
        'is_signal': True,
        'sym': sym, 'price': curr_price, 'dir': direction,
        'sig': sig_text, 'score': final_score,
        'rsi4': round(rsi4, 1), 'rsi1d': round(rsi1d, 1),
        'sl': sl, 'tp1': tp1, 'tp2': tp2,
        'time': datetime.now().strftime('%H:%M')
    }

def send(msg):
    if not TOKEN or not CHAT: return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        d = json.dumps({"chat_id": CHAT, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=d, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, context=CTX, timeout=10) as x:
            return x.status == 200
    except Exception:
        return False

def fp(n):
    if n >= 1000: return f"{n:,.2f}"
    if n >= 1: return f"{n:,.4f}"
    return f"{n:.6f}"

def fmt(a):
    icon = "🟢" if a['dir'] == 'buy' else "🔴"
    return (
        f"{icon} <b>#سیگنال_{a['sym']} | USDT</b>\n\n"
        f"🎯 جهت: <b>{a['sig']}</b>\n"
        f"📊 قدرت سیگنال: <b>{a['score']} / 10</b>\n"
        f"💰 قیمت ورود: <b>{fp(a['price'])} $</b>\n"
        f"🛑 حد زیان (SL): <b>{fp(a['sl'])} $</b>\n"
        f"🎯 حد سود ۱ (TP1): <b>{fp(a['tp1'])} $</b>\n"
        f"🚀 حد سود ۲ (TP2): <b>{fp(a['tp2'])} $</b>\n\n"
        f"📈 RSI ۴ساعته: {a['rsi4']} | روزانه: {a['rsi1d']}\n"
        f"📅 زمان: {a['time']}"
    )

if __name__ == "__main__":
    print("شروع اسکن...")
    
    signals = []
    market_summary = []
    
    for s in COINS:
        a = analyze(s)
        if a:
            if a['is_signal']:
                signals.append(a)
            else:
                market_summary.append(a)
        time.sleep(0.1)

    # اگر سیگنالی پیدا شد
    if signals:
        for sig in signals:
            send(fmt(sig))
            time.sleep(0.3)
    else:
        # اگر سیگنالی پیدا نشد، ارسال گزارش خلاصه بازار
        btc_info = next((item for item in market_summary if item['sym'] == 'BTC'), None)
        eth_info = next((item for item in market_summary if item['sym'] == 'ETH'), None)
        
        avg_rsi = round(sum(item['rsi4'] for item in market_summary) / len(market_summary), 1) if market_summary else 50
        
        now = datetime.now().strftime('%H:%M')
        msg = (
            f"ℹ️ <b>گزارش دوره ای بازار کریپتو ({now})</b>\n\n"
            f"در این اسکن نقطه ورود معتبری برای ۲۰ ارز اصلی یافت نشد (بازار رنج یا بدون روند قوی است).\n\n"
            f"📊 <b>وضعیت کلی بازار:</b>\n"
            f"• میانگین شاخص RSI بازار: <b>{avg_rsi}</b>\n"
        )
        if btc_info:
            msg += f"• بیت‌کوین (BTC): <b>{fp(btc_info['price'])} $</b> (روند ۴ساعته: {btc_info['trend4']})\n"
        if eth_info:
            msg += f"• اتریوم (ETH): <b>{fp(eth_info['price'])} $</b> (روند ۴ساعته: {eth_info['trend4']})\n"
            
        msg += "\n🔍 اسکن بعدی ۳۰ دقیقه دیگر انجام خواهد شد."
        send(msg)

    print("پایان اسکن.")
