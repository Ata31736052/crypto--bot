# ============================================
# 🤖 ربات سیگنال ارز دیجیتال - نسخه پرسیگنال و بهینه‌شده
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# ۲۰ ارز برتر و نقدشونده
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

    # بررسی میانگین‌های متحرک در 4h
    if e9_4 > e21_4: score_buy += 3
    if e21_4 > e50_4: score_buy += 2
    if e9_4 < e21_4: score_sell += 3
    if e21_4 < e50_4: score_sell += 2

    # بررسی RSI
    if 40 < rsi4 < 70: score_buy += 2
    if 30 < rsi4 < 60: score_sell += 2

    # تأییدیه روند روزانه (امتیاز کمکی)
    if e9_1d and e21_1d:
        if e9_1d > e21_1d: score_buy += 3
        if e9_1d < e21_1d: score_sell += 3

    # تعیین جهت معامله براساس حداقل امتیاز
    direction = None
    final_score = 0
    
    if score_buy >= 5 and score_buy > score_sell:
        direction = 'buy'
        final_score = score_buy
    elif score_sell >= 5 and score_sell > score_buy:
        direction = 'sell'
        final_score = score_sell

    if not direction:
        return None

    curr_price = k4['price']
    
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
    send("🤖 <b>اسکن بازار برای یافتن سیگنال‌های جدید شروع شد...</b>")
    
    count = 0
    for s in COINS:
        a = analyze(s)
        if a:
            if send(fmt(a)):
                print(f"سیگنال فرستاده شد: {s}")
                count += 1
        time.sleep(0.1)
        
    print(f"پایان اسکن. تعداد سیگنال: {count}")
