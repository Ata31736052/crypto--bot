# ============================================
# 🤖 ربات جامع سیگنال‌دهی کریپتو - تایم‌فریم روزانه (1D)
# 🎯 نسخه اصلاح‌شده با دریافت مطمئن داده‌ها
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

COINS = [
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 
    'ADA', 'DOGE', 'AVAX', 'LINK', 'DOT', 
    'NEAR', 'SUI', 'APT', 'ARB', 'OP',
    'PEPE', 'FET', 'RNDR', 'INJ', 'MATIC'
]

def http(url, t=10):
    try:
        req = urllib.request.Request(
            url, 
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
            }
        )
        with urllib.request.urlopen(req, context=CTX, timeout=t) as x:
            return json.loads(x.read().decode())
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def klines(sym, tf='1d'):
    # آدرس اصلی بایننس
    url = f"https://api.binance.com/api/v3/klines?symbol={sym}USDT&interval={tf}&limit=60"
    d = http(url)
    
    # در صورت عدم پاسخ از بایننس اصلی، استفاده از API دامنه جایگزین
    if not d or not isinstance(d, list):
        url_alt = f"https://api1.binance.com/api/v3/klines?symbol={sym}USDT&interval={tf}&limit=60"
        d = http(url_alt)

    if not d or not isinstance(d, list):
        return None

    opens = [float(c[1]) for c in d]
    highs = [float(c[2]) for c in d]
    lows = [float(c[3]) for c in d]
    closes = [float(c[4]) for c in d]
    vols = [float(c[5]) for c in d]
    return {
        'o': opens, 'h': highs, 'l': lows, 'p': closes, 'v': vols, 
        'price': closes[-1]
    }

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

def analyze(sym):
    k1d = klines(sym, '1d')
    if not k1d:
        return None

    p1d, h1d, l1d, o1d, v1d = k1d['p'], k1d['h'], k1d['l'], k1d['o'], k1d['v']
    curr_price = k1d['price']
    
    e9 = ema(p1d, 9)
    e21 = ema(p1d, 21)
    e50 = ema(p1d, 50)
    rsi1d = rsi(p1d)
    atr1d = atr(h1d, l1d, p1d, 14)

    if not (e9 and e21 and e50 and atr1d):
        return None

    score_buy = 0
    score_sell = 0

    if e9 > e21: score_buy += 3
    if e21 > e50: score_buy += 2
    if e9 < e21: score_sell += 3
    if e21 < e50: score_sell += 2

    if 45 < rsi1d < 68: score_buy += 3
    if 32 < rsi1d < 55: score_sell += 3

    avg_vol = sum(v1d[-21:-1]) / 20 if len(v1d) >= 21 else sum(v1d) / len(v1d)
    if v1d[-1] > avg_vol:
        score_buy += 1
        score_sell += 1

    body = abs(p1d[-1] - o1d[-1])
    candle_range = h1d[-1] - l1d[-1]
    if candle_range > 0:
        lower_shadow = min(p1d[-1], o1d[-1]) - l1d[-1]
        upper_shadow = h1d[-1] - max(p1d[-1], o1d[-1])
        if lower_shadow > (2 * body) and lower_shadow > (0.5 * candle_range):
            score_buy += 2
        if upper_shadow > (2 * body) and upper_shadow > (0.5 * candle_range):
            score_sell += 2

    direction = None
    if score_buy >= 7 and score_buy > score_sell:
        direction = 'buy'
        final_score = score_buy
    elif score_sell >= 7 and score_sell > score_buy:
        direction = 'sell'
        final_score = score_sell

    trend_icon = "🟢" if e9 > e21 else "🔴"

    base_info = {
        'sym': sym, 
        'price': curr_price, 
        'rsi1d': round(rsi1d, 1),
        'icon': trend_icon
    }

    if not direction:
        return {'is_signal': False, **base_info}

    if direction == 'buy':
        sl = curr_price - (1.5 * atr1d)
        tp1 = curr_price + (2.0 * atr1d)
        tp2 = curr_price + (4.0 * atr1d)
        sig_text = "خرید (LONG)"
    else:
        sl = curr_price + (1.5 * atr1d)
        tp1 = curr_price - (2.0 * atr1d)
        tp2 = curr_price - (4.0 * atr1d)
        sig_text = "فروش (SHORT)"

    risk = abs(curr_price - sl)
    reward = abs(tp2 - curr_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.0

    return {
        'is_signal': True,
        'sym': sym, 
        'price': curr_price, 
        'dir': direction,
        'sig': sig_text, 
        'score': final_score,
        'rsi1d': round(rsi1d, 1),
        'atr1d': fp(atr1d),
        'sl': sl, 
        'tp1': tp1, 
        'tp2': tp2,
        'rr': rr_ratio,
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
    except Exception as e:
        print(f"Error sending message: {e}")
        return False

def fp(n):
    if n >= 1000: return f"{n:,.2f}"
    if n >= 1: return f"{n:,.3f}"
    return f"{n:.5f}"

def fmt(a):
    icon = "🟢" if a['dir'] == 'buy' else "🔴"
    tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{a['sym']}USDT"
    
    return (
        f"{icon} <b>#سیگنال_روزانه_{a['sym']} | USDT</b>\n\n"
        f"🎯 جهت معامله: <b>{a['sig']}</b>\n"
        f"📊 قدرت سیگنال: <b>{a['score']} / 11</b>\n"
        f"💰 قیمت ورود: <b>{fp(a['price'])} $</b>\n"
        f"⚖️ نسبت ریسک به ریوارد (R/R): <b>1:{a['rr']}</b>\n"
        f"📏 دامنه نوسان (ATR 14): <b>{a['atr1d']} $</b>\n\n"
        f"🛑 حد زیان (1.5x ATR): <b>{fp(a['sl'])} $</b>\n"
        f"🎯 حد سود اول (2x ATR): <b>{fp(a['tp1'])} $</b>\n"
        f"🚀 حد سود دوم (4x ATR): <b>{fp(a['tp2'])} $</b>\n\n"
        f"📈 RSI روزانه: <b>{a['rsi1d']}</b>\n"
        f"🔗 <a href='{tv_link}'>مشاهده نمودار در TradingView</a>\n"
        f"📅 زمان ثبت: {a['time']}"
    )

if __name__ == "__main__":
    print("شروع اسکن جامع بازار در تایم‌فریم روزانه...")
    
    signals = []
    market_summary = []
    
    for s in COINS:
        a = analyze(s)
        if a:
            if a['is_signal']:
                signals.append(a)
            market_summary.append(a)
        time.sleep(0.2)

    if signals:
        for sig in signals:
            send(fmt(sig))
            time.sleep(0.3)
    else:
        now = datetime.now().strftime('%H:%M')
        
        if market_summary:
            avg_rsi_1d = round(sum(item['rsi1d'] for item in market_summary) / len(market_summary), 1)
            
            msg = f"📊 <b>گزارش روزانه بازار کریپتو ({now})</b>\n\n"
            msg += f"• میانگین RSI روزانه بازار: <b>{avg_rsi_1d}</b>\n"
            msg += "• وضعیت سیگنال: <i>هیچ ارزی تمام شرایط ورود معتبر در تایم روزانه را احراز نکرد.</i>\n\n"
            
            msg += "<pre>"
            msg += f"{'نماد':<8} {'قیمت ($)':<13} {'RSI روزانه':<10}\n"
            msg += "─" * 32 + "\n"
            
            for item in market_summary:
                price_str = fp(item['price'])
                msg += f"{item['sym']:<8} {price_str:<13} {item['rsi1d']:<10}\n"
                
            msg += "</pre>\n"
            msg += "🔍 اسکن بعدی انجام خواهد شد."
        else:
            msg = f"⚠️ <b>خطا در دریافت داده‌ها ({now})</b>\n\nاتصال به API بایننس برقرار نشد. اسکن بعدی انجام خواهد شد."

        send(msg)

    print("پایان اسکن.")
    
