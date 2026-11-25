"""Causal, completed-candle EarlyEdge signal engines.

Input bars are Binance kline dictionaries with open_time, close_time, open,
high, low, close, volume. A decision is made at the close of a 15m candle;
only 1h candles whose close_time is <= that decision time are eligible.
This module emits research signals only; it never places orders.
"""
from __future__ import annotations
from math import isfinite

ENGINES=("BOTTOM_REVERSAL","BREAKOUT","TREND_PULLBACK","RANGE_BOUNCE")

def _v(bar,key):
    x=float(bar[key])
    if not isfinite(x): raise ValueError(f"non-finite {key}")
    return x

def _ema(values,period):
    if len(values)<period: return None
    alpha=2/(period+1)
    value=sum(values[:period])/period
    for x in values[period:]: value=alpha*x+(1-alpha)*value
    return value

def _rsi(values,period=14):
    if len(values)<period+1: return None
    changes=[values[i]-values[i-1] for i in range(1,len(values))]
    gains=[max(0,x) for x in changes[-period:]]
    losses=[max(0,-x) for x in changes[-period:]]
    ag=sum(gains)/period; al=sum(losses)/period
    if al==0: return 100.0 if ag>0 else 50.0
    return 100-100/(1+ag/al)

def _atr(bars,period=14):
    if len(bars)<period+1: return None
    trs=[]
    for i in range(1,len(bars)):
        h,l,c0=_v(bars[i],"high"),_v(bars[i],"low"),_v(bars[i-1],"close")
        trs.append(max(h-l,abs(h-c0),abs(l-c0)))
    return sum(trs[-period:])/period

def _volume_ratio(bars,period=20):
    if len(bars)<period+1: return None
    avg=sum(_v(x,"volume") for x in bars[-period-1:-1])/period
    return _v(bars[-1],"volume")/avg if avg>0 else None

def _near(a,b,pct): return abs(a/b-1)*100 <= pct if b>0 else False

def _context_1h(bars1h, decision_close):
    eligible=[b for b in bars1h if int(b["close_time"])<=decision_close]
    closes=[_v(b,"close") for b in eligible]
    if len(closes)<30: return None
    e21,e50=_ema(closes,21),_ema(closes,50)
    if e21 is None or e50 is None: return None
    return {"bars":eligible,"close":closes[-1],"ema21":e21,"ema50":e50,
            "bull":closes[-1]>e21 and e21>e50,
            "bear":closes[-1]<e21 and e21<e50,
            "flat":abs(e21/e50-1)*100<1.0}

def _signal(engine,state,bar,atr):
    close=_v(bar,"close")
    stop_pct=max(0.8,min(3.0,1.5*atr/close*100))
    return {"engine":engine,"state":state,"timestamp":str(bar["close_time"]),
            "price":close,"stop_pct":stop_pct,"target_pct":stop_pct*1.8,
            "reason":engine.lower()+"_"+state.lower()}

def evaluate_engines(bars15,bars1h,index):
    """Return one candidate per eligible engine at index (WATCH or ENTRY).

    Historical callers must invoke this sequentially. No future 15m/1h candle
    is consulted; the 1h context is explicitly close-time filtered.
    """
    if index<0 or index>=len(bars15): raise IndexError("15m index out of range")
    hist=bars15[:index+1]
    if len(hist)<30: return []
    bar=hist[-1]; decision=int(bar["close_time"])
    ctx=_context_1h(bars1h,decision)
    if ctx is None: return []
    closes=[_v(x,"close") for x in hist]
    c=closes[-1]; prev=hist[-2]
    e9,e21=_ema(closes,9),_ema(closes,21)
    rsi=_rsi(closes); atr=_atr(hist); vr=_volume_ratio(hist)
    if None in (e9,e21,rsi,atr,vr) or atr<=0: return []
    high=max(_v(x,"high") for x in hist[-21:-1])
    support=min(_v(x,"low") for x in hist[-21:-1])
    results=[]

    # 1) Bottom reversal: wait for a local reclaim, not a falling-knife RSI print.
    bottom_watch=(rsi<43 and c>_v(prev,"close") and c>_v(bar,"open"))
    bottom_entry=(bottom_watch and rsi>_rsi(closes[:-1]) and c>_v(prev,"high")
                  and vr>=1.15 and not ctx["bear"])
    if bottom_entry: results.append(_signal("BOTTOM_REVERSAL","ENTRY",bar,atr))
    elif bottom_watch: results.append(_signal("BOTTOM_REVERSAL","WATCH",bar,atr))

    # 2) Breakout: close beyond prior 20 completed bars with participation.
    breakout_watch=c>=high*0.997 and vr>=1.0
    breakout_entry=c>high and vr>=1.5 and ctx["bull"]
    if breakout_entry: results.append(_signal("BREAKOUT","ENTRY",bar,atr))
    elif breakout_watch: results.append(_signal("BREAKOUT","WATCH",bar,atr))

    # 3) Trend pullback: higher-timeframe trend intact; reclaim short EMA.
    trend_watch=ctx["bull"] and _near(c,e21,0.8) and rsi>=42
    trend_entry=(ctx["bull"] and c>e9 and _v(prev,"close")<=e9
                 and 45<=rsi<=68 and vr>=0.9)
    if trend_entry: results.append(_signal("TREND_PULLBACK","ENTRY",bar,atr))
    elif trend_watch: results.append(_signal("TREND_PULLBACK","WATCH",bar,atr))

    # 4) Range support bounce: only in flat 1h context, not in a confirmed downtrend.
    range_watch=ctx["flat"] and _near(c,support,0.8) and c>_v(bar,"open")
    range_entry=(range_watch and c>_v(prev,"high") and rsi>=35 and vr>=1.0)
    if range_entry: results.append(_signal("RANGE_BOUNCE","ENTRY",bar,atr))
    elif range_watch: results.append(_signal("RANGE_BOUNCE","WATCH",bar,atr))
    return results
