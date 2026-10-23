import copy
import unittest

import pandas as pd

import historical_backtest as hb


def shadow_trade(entry_index=0):
    entry = 100.0
    notional = 500.0
    qty = notional / entry
    return {
        "symbol": "TESTUSDT", "status": "PENDING", "entry_index": entry_index,
        "last_checked_index": entry_index - 1, "entry_price": entry,
        "entry_raw": entry, "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
        "initial_qty": qty, "qty": qty, "notional_usdt": notional,
        "risk_budget_usdt": 25.0, "entry_fee_usdt": notional * hb.FEE_RATE,
        "fees_usdt": notional * hb.FEE_RATE, "realized_pnl_usdt": 0.0,
        "net_pnl_usdt": None, "net_pnl_pct_notional": None, "net_pnl_r": None,
        "tp1_done": False, "tp1_hit": False, "tp2_hit": False,
        "mfe_pct": 0.0, "mae_pct": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
        "exit_time": None, "rejection_reason": "test"
    }


def market_with(bars):
    rows = []
    for i, (high, low, close) in enumerate(bars):
        rows.append({"open_time": i * 1000, "close_time": i * 1000 + 999,
                     "open": 100.0, "high": high, "low": low, "close": close})
    return {"TESTUSDT": {"1h": pd.DataFrame(rows)}}


class ShadowLedgerTests(unittest.TestCase):
    def advance(self, trade, bars, entry_index=0, decision_ms=10**9):
        trade["entry_index"] = entry_index
        trade["last_checked_index"] = entry_index - 1
        hb.advance_shadow_ledger([trade], market_with(bars), decision_ms)
        return trade

    def test_rejected_trade_stops_out(self):
        p = self.advance(shadow_trade(), [(103, 94, 96)])
        self.assertEqual(p["status"], "SL")
        self.assertIsNotNone(p["net_pnl_usdt"])

    def test_tp1_and_tp2_events_are_recorded(self):
        p = self.advance(shadow_trade(), [(106, 99, 105), (112, 101, 111)])
        self.assertTrue(p["tp1_hit"])
        self.assertTrue(p["tp2_hit"])
        self.assertEqual(p["status"], "TP2")

    def test_same_candle_sl_and_tp_uses_stop_first(self):
        p = self.advance(shadow_trade(), [(112, 94, 100)])
        self.assertEqual(p["status"], "SL")
        self.assertFalse(p["tp1_hit"])
        self.assertFalse(p["tp2_hit"])

    def test_no_candle_before_entry_index_is_used(self):
        p = self.advance(shadow_trade(entry_index=1),
                         [(120, 80, 100), (103, 99, 101)], entry_index=1)
        self.assertEqual(p["status"], "PENDING")
        self.assertEqual(p["last_checked_index"], 1)
        self.assertAlmostEqual(p["mfe_pct"], 3.0, places=7)

    def test_end_of_replay_processes_last_completed_candle_before_mark(self):
        p = shadow_trade()
        market = market_with([(103, 94, 96)])
        # The normal scan loop has not yet processed this candle.
        hb.advance_shadow_ledger([p], market, decision_ms=0)
        self.assertEqual(p["status"], "PENDING")
        hb.advance_shadow_ledger([p], market, decision_ms=2000)
        hb.mark_shadow_open_at_end([p], market, end_ms=2000)
        self.assertEqual(p["status"], "SL")
        self.assertAlmostEqual(p["mfe_pct"], 3.0, places=7)
        self.assertIsNotNone(p["net_pnl_usdt"])

    def test_shadow_ledger_does_not_mutate_main_portfolio(self):
        port = {"cash": 1000.0, "positions": [], "closed": [], "signals": 7}
        before = copy.deepcopy(port)
        self.advance(shadow_trade(), [(106, 99, 105), (112, 101, 111)])
        self.assertEqual(port, before)


if __name__ == "__main__":
    unittest.main()
