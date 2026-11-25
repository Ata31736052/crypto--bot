"""Deterministic, single-symbol OHLCV replay for EarlyEdge research.

Input CSV columns: timestamp,open,high,low,close,signal,stop_pct,target_pct
Signals are evaluated at candle close and filled at the NEXT candle open.
WATCH rows are ignored. One position per symbol; stop wins ambiguous same-bar
stop/target collisions. This is a research harness, not a portfolio simulator.
"""
from __future__ import annotations
import argparse, csv, json
from dataclasses import asdict, dataclass
from pathlib import Path

@dataclass
class Trade:
    signal_time: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    stop: float
    target: float
    qty: float
    gross_pnl: float
    fees: float
    net_pnl: float
    reason: str
    mae_pct: float
    mfe_pct: float

def _num(row, key):
    value = float(row[key])
    if not (value == value and abs(value) != float("inf")):
        raise ValueError(f"non-finite {key}")
    return value

def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    required = {"timestamp","open","high","low","close","signal","stop_pct","target_pct"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"CSV requires columns: {sorted(required)}")
    for i, row in enumerate(rows):
        for k in ("open","high","low","close"):
            _num(row,k)
        if min(_num(row,k) for k in ("open","high","low","close")) <= 0:
            raise ValueError("OHLC prices must be positive")
        if _num(row,"high") < max(_num(row,"open"),_num(row,"close"),_num(row,"low")):
            raise ValueError("invalid high")
        if _num(row,"low") > min(_num(row,"open"),_num(row,"close"),_num(row,"high")):
            raise ValueError("invalid low")
        if i and row["timestamp"] <= rows[i-1]["timestamp"]:
            raise ValueError("timestamps must be strictly increasing")
        if row["signal"].upper() not in {"", "NONE", "WATCH", "ENTRY"}:
            raise ValueError("signal must be NONE, WATCH, or ENTRY")
    return rows

def replay(rows, starting_cash=1000.0, risk_pct=1.0, max_position=1000.0,
           fee_pct=0.1, slippage_pct=0.05):
    if starting_cash <= 0 or not 0 < risk_pct <= 100 or max_position <= 0:
        raise ValueError("invalid capital/risk settings")
    if fee_pct < 0 or slippage_pct < 0:
        raise ValueError("costs cannot be negative")
    cash = starting_cash
    trades, pending = [], None
    active = None
    for i, bar in enumerate(rows):
        o,h,l,c = (_num(bar,k) for k in ("open","high","low","close"))
        # Fill only on a later bar than the signal bar.
        if pending is not None and active is None:
            entry = o * (1 + slippage_pct/100)
            stop_pct, target_pct = pending["stop_pct"], pending["target_pct"]
            if not (0 < stop_pct < 100 and target_pct > 0):
                raise ValueError("stop_pct must be in (0,100), target_pct > 0")
            stop, target = entry*(1-stop_pct/100), entry*(1+target_pct/100)
            risk_cash = cash * risk_pct/100
            qty = min(risk_cash/(entry-stop), max_position/entry, cash/(entry*(1+fee_pct/100)))
            if qty > 0:
                active = {"signal_time":pending["signal_time"],"entry_time":bar["timestamp"],
                          "entry":entry,"stop":stop,"target":target,"qty":qty,
                          "mae":0.0,"mfe":0.0}
            pending = None
        if active is not None:
            active["mae"] = min(active["mae"], (l/active["entry"]-1)*100)
            active["mfe"] = max(active["mfe"], (h/active["entry"]-1)*100)
            stop_hit, target_hit = l <= active["stop"], h >= active["target"]
            reason = "STOP" if stop_hit else "TARGET" if target_hit else None
            if reason or i == len(rows)-1:
                raw_exit = active["stop"] if reason == "STOP" else active["target"] if reason == "TARGET" else c
                exit_price = raw_exit * (1-slippage_pct/100)
                gross = (exit_price-active["entry"])*active["qty"]
                fees = (active["entry"]+exit_price)*active["qty"]*fee_pct/100
                net = gross-fees
                cash += net
                trades.append(Trade(active["signal_time"],active["entry_time"],bar["timestamp"],
                    active["entry"],exit_price,active["stop"],active["target"],active["qty"],
                    gross,fees,net,reason or "END_OF_DATA",active["mae"],active["mfe"]))
                active = None
        if active is None and pending is None and bar["signal"].upper() == "ENTRY" and i < len(rows)-1:
            pending = {"signal_time":bar["timestamp"],"stop_pct":_num(bar,"stop_pct"),
                       "target_pct":_num(bar,"target_pct")}
    wins = sum(t.net_pnl > 0 for t in trades)
    gross_profit = sum(max(0,t.net_pnl) for t in trades)
    gross_loss = -sum(min(0,t.net_pnl) for t in trades)
    return {"starting_cash":starting_cash,"ending_cash":cash,"net_pnl":cash-starting_cash,
            "return_pct":(cash/starting_cash-1)*100,"trade_count":len(trades),
            "win_rate_pct":wins/len(trades)*100 if trades else 0,
            "profit_factor":gross_profit/gross_loss if gross_loss else None,
            "fees_total":sum(t.fees for t in trades),
            "trades":[asdict(t) for t in trades]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("csv"); p.add_argument("--out",default="earlyedge-report.json")
    p.add_argument("--cash",type=float,default=1000); p.add_argument("--risk",type=float,default=1)
    p.add_argument("--max-position",type=float,default=1000); p.add_argument("--fee",type=float,default=.1)
    p.add_argument("--slippage",type=float,default=.05)
    a=p.parse_args()
    report=replay(load_rows(a.csv),a.cash,a.risk,a.max_position,a.fee,a.slippage)
    Path(a.out).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="trades"},indent=2))
if __name__=="__main__": main()
