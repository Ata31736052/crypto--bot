"""Multi-symbol, shared-capital Binance Spot portfolio replay.

Each symbol maps to a signal CSV with timestamp, OHLC, signal, stop_pct,
target_pct. ENTRY signals execute at the next available bar open. WATCH is
never traded. This remains a research simulator, not an exchange executor.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

def read_signal_csv(path):
    with open(path,newline="",encoding="utf-8") as f: rows=list(csv.DictReader(f))
    required={"timestamp","open","high","low","close","signal","stop_pct","target_pct"}
    if not rows or not required.issubset(rows[0]): raise ValueError(f"{path}: missing columns")
    last=None
    for r in rows:
        t=int(r["timestamp"])
        if last is not None and t<=last: raise ValueError("timestamps must increase")
        last=t
        o,h,l,c=(float(r[k]) for k in ("open","high","low","close"))
        if min(o,h,l,c)<=0 or h<max(o,l,c) or l>min(o,h,c): raise ValueError("invalid OHLC")
        if r["signal"].upper() not in {"NONE","WATCH","ENTRY",""}: raise ValueError("invalid signal")
    return rows

def portfolio_replay(symbol_rows, starting_cash=1000.0, risk_pct=1.0,
                     max_position=1000.0, max_total_exposure=1000.0,
                     max_positions=10, fee_pct=0.1, slippage_pct=0.05):
    if not symbol_rows or starting_cash<=0 or not 0<risk_pct<=100:
        raise ValueError("invalid portfolio/capital/risk settings")
    if max_position<=0 or max_total_exposure<=0 or max_positions<1:
        raise ValueError("invalid exposure/position limits")
    if fee_pct<0 or slippage_pct<0: raise ValueError("costs cannot be negative")
    for symbol,rows in symbol_rows.items():
        if not rows: raise ValueError(f"{symbol}: empty series")
    timeline={}
    for symbol,rows in symbol_rows.items():
        for i,row in enumerate(rows):
            timeline.setdefault(int(row["timestamp"]),[]).append((symbol,i,row))
    cash=starting_cash; active={}; pending={}; trades=[]
    peak=starting_cash; max_dd=0.0; max_deployed=0.0; max_open=0
    last_prices={}
    for timestamp in sorted(timeline):
        events=sorted(timeline[timestamp],key=lambda x:x[0])
        # First process exits using only this candle's OHLC. Stop wins collisions.
        for symbol,i,row in events:
            o,h,l,c=(float(row[k]) for k in ("open","high","low","close"))
            last_prices[symbol]=c
            pos=active.get(symbol)
            if pos is None: continue
            pos["mae"]=min(pos["mae"],(l/pos["entry"]-1)*100)
            pos["mfe"]=max(pos["mfe"],(h/pos["entry"]-1)*100)
            stop_hit=l<=pos["stop"]; target_hit=h>=pos["target"]
            reason="STOP" if stop_hit else "TARGET" if target_hit else None
            is_last=i==len(symbol_rows[symbol])-1
            if reason or is_last:
                raw=pos["stop"] if reason=="STOP" else pos["target"] if reason=="TARGET" else c
                exit_price=raw*(1-slippage_pct/100)
                exit_fee=exit_price*pos["qty"]*fee_pct/100
                gross=(exit_price-pos["entry"])*pos["qty"]
                fees=pos["entry_fee"]+exit_fee
                net=gross-fees
                cash+=exit_price*pos["qty"]-exit_fee
                trades.append({**pos,"symbol":symbol,"exit_time":str(timestamp),
                    "exit":exit_price,"gross_pnl":gross,"fees":fees,"net_pnl":net,
                    "reason":reason or "END_OF_DATA"})
                del active[symbol]
        # Fill pending entries at the next available candle open.
        for symbol,i,row in events:
            if symbol not in pending: continue
            p=pending.pop(symbol)
            if symbol in active or len(active)>=max_positions: continue
            entry=float(row["open"])*(1+slippage_pct/100)
            sp,tp=p["stop_pct"],p["target_pct"]
            if not (0<sp<100 and tp>0): raise ValueError("invalid stop/target percentages")
            stop=entry*(1-sp/100); target=entry*(1+tp/100)
            equity=cash+sum(x["qty"]*last_prices.get(s, x["entry"]) for s,x in active.items())
            risk_cash=max(0,equity)*risk_pct/100
            deployed=sum(x["qty"]*x["entry"] for x in active.values())
            capacity=max(0,min(max_position,max_total_exposure-deployed))
            qty=min(risk_cash/(entry-stop),capacity/entry,cash/(entry*(1+fee_pct/100)))
            if qty>0:
                entry_fee=entry*qty*fee_pct/100
                cash-=entry*qty+entry_fee
                active[symbol]={"signal_time":p["signal_time"],"entry_time":str(timestamp),
                    "entry":entry,"stop":stop,"target":target,"qty":qty,
                    "entry_fee":entry_fee,"mae":0.0,"mfe":0.0}
        # Newly opened positions are exposed to the fill candle's full range.
        for symbol,i,row in events:
            pos=active.get(symbol)
            if pos is None or pos["entry_time"]!=str(timestamp): continue
            h,l=float(row["high"]),float(row["low"])
            pos["mae"]=min(pos["mae"],(l/pos["entry"]-1)*100)
            pos["mfe"]=max(pos["mfe"],(h/pos["entry"]-1)*100)
            stop_hit=l<=pos["stop"]; target_hit=h>=pos["target"]
            reason="STOP" if stop_hit else "TARGET" if target_hit else None
            if reason:
                raw=pos["stop"] if reason=="STOP" else pos["target"]
                exit_price=raw*(1-slippage_pct/100)
                exit_fee=exit_price*pos["qty"]*fee_pct/100
                gross=(exit_price-pos["entry"])*pos["qty"]
                fees=pos["entry_fee"]+exit_fee
                cash+=exit_price*pos["qty"]-exit_fee
                trades.append({**pos,"symbol":symbol,"exit_time":str(timestamp),
                    "exit":exit_price,"gross_pnl":gross,"fees":fees,"net_pnl":gross-fees,
                    "reason":reason})
                del active[symbol]
        # Signals at this close can only fill on a later bar.
        for symbol,i,row in events:
            if symbol not in active and symbol not in pending and row["signal"].upper()=="ENTRY" and i<len(symbol_rows[symbol])-1:
                pending[symbol]={"signal_time":str(timestamp),
                    "stop_pct":float(row["stop_pct"]),"target_pct":float(row["target_pct"])}
        deployed=sum(x["qty"]*x["entry"] for x in active.values())
        max_deployed=max(max_deployed,deployed);max_open=max(max_open,len(active))
        equity=cash+sum(x["qty"]*last_prices.get(s,x["entry"]) for s,x in active.items())
        peak=max(peak,equity)
        if peak>0: max_dd=max(max_dd,(peak-equity)/peak*100)
    wins=sum(t["net_pnl"]>0 for t in trades)
    profit=sum(max(0,t["net_pnl"]) for t in trades)
    loss=-sum(min(0,t["net_pnl"]) for t in trades)
    ending=cash+sum(x["qty"]*last_prices.get(s,x["entry"]) for s,x in active.items())
    return {"starting_cash":starting_cash,"ending_equity":ending,"net_pnl":ending-starting_cash,
        "return_pct":(ending/starting_cash-1)*100,"trade_count":len(trades),
        "win_rate_pct":wins/len(trades)*100 if trades else 0,
        "profit_factor":profit/loss if loss else None,"max_drawdown_pct":max_dd,
        "fees_total":sum(t["fees"] for t in trades),"max_deployed":max_deployed,
        "max_open_positions":max_open,"open_positions_at_end":len(active),"trades":trades}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("inputs",nargs="+",help="SYMBOL=signal.csv (one engine per run)")
    p.add_argument("--out",default="earlyedge-portfolio-report.json")
    p.add_argument("--cash",type=float,default=1000);p.add_argument("--risk",type=float,default=1)
    p.add_argument("--max-position",type=float,default=1000);p.add_argument("--max-exposure",type=float,default=1000)
    p.add_argument("--max-positions",type=int,default=10);p.add_argument("--fee",type=float,default=.1)
    p.add_argument("--slippage",type=float,default=.05)
    a=p.parse_args();data={}
    for spec in a.inputs:
        if "=" not in spec:p.error("inputs must use SYMBOL=path")
        symbol,path=spec.split("=",1);data[symbol.upper()]=read_signal_csv(path)
    report=portfolio_replay(data,a.cash,a.risk,a.max_position,a.max_exposure,
                            a.max_positions,a.fee,a.slippage)
    Path(a.out).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="trades"},indent=2))
if __name__=="__main__":main()
