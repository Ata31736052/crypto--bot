"""Conservative, causal EarlyEdge signal engine.

Only the strongest research candidate is enabled: a confirmed bottom reversal.
Breakout, trend-pullback and range-bounce were negative after costs in the
180-day ETHUSDT smoke and are disabled pending independent re-validation.
WATCH-only setups are intentionally suppressed; this module emits ENTRY only.
"""
from __future__ import annotations
from math import isfinite

ENGINES=("BOTTOM_REVERSAL",)

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

def _context_1h(bars1h, decision_close):
    eligible=[b for b in bars1h if int(b["close_time"])<=decision_close]
    closes=[_v(b,"close") for b in eligible]
    if len(closes)<50: return None
    e21,e50=_ema(closes,21),_ema(closes,50)
    if e21 is None or e50 is None: return None
    return {"close":closes[-1],"ema21":e21,"ema50":e50,
            "bull":closes[-1]>e21 and e21>e50,
            "bear":closes[-1]<e21 and e21<e50}

def _signal(bar,atr):
    close=_v(bar,"close")
    stop_pct=max(0.8,min(3.0,1.5*atr/close*100))
    return {"engine":"BOTTOM_REVERSAL","state":"ENTRY",
            "timestamp":str(bar["close_time"]),"price":close,
            "stop_pct":stop_pct,"target_pct":stop_pct*1.8,
            "reason":"bottom_reversal_confirmed_high_volume"}

def evaluate_engines(bars15,bars1h,index):
    """Return only a high-confirmation bottom-reversal ENTRY, never WATCH."""
    if index<0 or index>=len(bars15): raise IndexError("15m index out of range")
    hist=bars15[:index+1]
    if len(hist)<30: return []
    bar=hist[-1]; decision=int(bar["close_time"])
    ctx=_context_1h(bars1h,decision)
    if ctx is None: return []
    closes=[_v(x,"close") for x in hist]
    c=closes[-1]; prev=hist[-2]
    rsi=_rsi(closes); prior_rsi=_rsi(closes[:-1])
    atr=_atr(hist); vr=_volume_ratio(hist)
    if None in (rsi,prior_rsi,atr,vr) or atr<=0: return []

    # Require a rising RSI, bullish candle, reclaim above prior high and
    # exceptional relative volume. Countertrend entries demand still more.
    bearish=ctx["bear"]
    min_volume=2.0 if bearish else 1.8
    confirmed=(rsi<43 and rsi>prior_rsi and c>_v(bar,"open")
               and c>_v(prev,"high") and vr>=min_volume)
    if not confirmed:
        return []
    return [_signal(bar,atr)]
