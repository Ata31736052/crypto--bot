import unittest
from unittest.mock import patch

import pandas as pd

import historical_backtest as hb


class ExitCandleExcursionTests(unittest.TestCase):
    def test_portfolio_exit_candle_is_excluded_from_mfe_mae(self):
        symbol = "AAAUSDT"
        position = {
            "symbol": symbol, "entry_index": 0, "last_checked_index": -1,
            "entry_price": 100.0, "sl": 95.0, "tp1": 110.0, "tp2": 120.0,
            "qty": 1.0, "initial_qty": 1.0, "tp1_done": False,
            "mfe_pct": 0.0, "mae_pct": 0.0, "realized_pnl": 0.0,
            "entry_fee": 0.0,
        }
        port = {
            "positions": [position], "closed": [], "cash": 0.0, "fees_paid": 0.0,
        }
        market = {symbol: {"1h": pd.DataFrame([{
            "open_time": 0, "close_time": 3_599_999,
            "high": 130.0, "low": 90.0, "close": 100.0,
        }])}}

        hb.advance_positions(port, market, 3_600_000)

        self.assertEqual(len(port["closed"]), 1)
        self.assertEqual(port["closed"][0]["exit_reason"], "SL")
        self.assertEqual(port["closed"][0]["mfe_pct"], 0.0)
        self.assertEqual(port["closed"][0]["mae_pct"], 0.0)

    def test_shadow_exit_candle_is_excluded_from_percent_and_r_excursions(self):
        symbol = "AAAUSDT"
        shadow = {
            "symbol": symbol, "status": "PENDING",
            "entry_index": 0, "last_checked_index": -1,
            "entry_price": 100.0, "sl": 95.0, "tp1": 110.0, "tp2": 120.0,
            "qty": 1.0, "tp1_done": False,
            "mfe_pct": 0.0, "mae_pct": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
        }
        market = {symbol: {"1h": pd.DataFrame([{
            "open_time": 0, "close_time": 3_599_999,
            "high": 130.0, "low": 90.0, "close": 100.0,
        }])}}

        def fake_shadow_close(p, qty, raw_price, reason, timestamp):
            p["qty"] = 0.0
            p["status"] = reason

        with patch.object(hb, "shadow_close_piece", side_effect=fake_shadow_close):
            hb.advance_shadow_ledger([shadow], market, 3_600_000)

        self.assertEqual(shadow["status"], "SL")
        self.assertEqual(shadow["mfe_pct"], 0.0)
        self.assertEqual(shadow["mae_pct"], 0.0)
        self.assertEqual(shadow["mfe_r"], 0.0)
        self.assertEqual(shadow["mae_r"], 0.0)


    def test_portfolio_partial_tp1_candle_is_excluded_from_mfe_mae(self):
        symbol = "AAAUSDT"
        position = {
            "symbol": symbol, "entry_index": 0, "last_checked_index": -1,
            "entry_price": 100.0, "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
            "qty": 1.0, "initial_qty": 1.0, "tp1_done": False,
            "mfe_pct": 0.0, "mae_pct": 0.0, "realized_pnl": 0.0,
            "entry_fee": 0.0,
        }
        port = {"positions": [position], "closed": [], "cash": 0.0, "fees_paid": 0.0}
        market = {symbol: {"1h": pd.DataFrame([{
            "open_time": 0, "close_time": 3_599_999,
            "high": 106.0, "low": 99.0, "close": 104.0,
        }])}}

        hb.advance_positions(port, market, 3_600_000)

        self.assertEqual(len(port["positions"]), 1)
        self.assertTrue(position["tp1_done"])
        self.assertLess(position["qty"], position["initial_qty"])
        self.assertEqual(position["mfe_pct"], 0.0)
        self.assertEqual(position["mae_pct"], 0.0)

    def test_shadow_partial_tp1_candle_is_excluded_from_all_excursions(self):
        symbol = "AAAUSDT"
        shadow = {
            "symbol": symbol, "status": "PENDING",
            "entry_index": 0, "last_checked_index": -1,
            "entry_price": 100.0, "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
            "initial_qty": 1.0, "qty": 1.0, "notional_usdt": 100.0,
            "risk_budget_usdt": 5.0, "entry_fee_usdt": 0.1, "fees_usdt": 0.1,
            "realized_pnl_usdt": 0.0, "net_pnl_usdt": None,
            "net_pnl_pct_notional": None, "net_pnl_r": None,
            "tp1_done": False, "tp1_hit": False, "tp2_hit": False,
            "mfe_pct": 0.0, "mae_pct": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
            "exit_time": None,
        }
        market = {symbol: {"1h": pd.DataFrame([{
            "open_time": 0, "close_time": 3_599_999,
            "high": 106.0, "low": 99.0, "close": 104.0,
        }])}}

        hb.advance_shadow_ledger([shadow], market, 3_600_000)

        self.assertEqual(shadow["status"], "PENDING")
        self.assertTrue(shadow["tp1_done"])
        self.assertTrue(shadow["tp1_hit"])
        self.assertLess(shadow["qty"], shadow["initial_qty"])
        self.assertEqual(shadow["mfe_pct"], 0.0)
        self.assertEqual(shadow["mae_pct"], 0.0)
        self.assertEqual(shadow["mfe_r"], 0.0)
        self.assertEqual(shadow["mae_r"], 0.0)


    def test_portfolio_non_exit_candle_still_updates_mfe_mae(self):
        symbol = "AAAUSDT"
        position = {
            "symbol": symbol, "entry_index": 0, "last_checked_index": -1,
            "entry_price": 100.0, "sl": 95.0, "tp1": 110.0, "tp2": 120.0,
            "qty": 1.0, "initial_qty": 1.0, "tp1_done": False,
            "mfe_pct": 0.0, "mae_pct": 0.0, "realized_pnl": 0.0,
            "entry_fee": 0.0,
        }
        port = {
            "positions": [position], "closed": [], "cash": 0.0, "fees_paid": 0.0,
        }
        market = {symbol: {"1h": pd.DataFrame([{
            "open_time": 0, "close_time": 3_599_999,
            "high": 104.0, "low": 98.0, "close": 101.0,
        }])}}

        hb.advance_positions(port, market, 3_600_000)

        self.assertEqual(len(port["positions"]), 1)
        self.assertAlmostEqual(position["mfe_pct"], 4.0, places=7)
        self.assertAlmostEqual(position["mae_pct"], -2.0, places=7)

    def test_shadow_non_exit_candle_still_updates_percent_and_r_excursions(self):
        symbol = "AAAUSDT"
        shadow = {
            "symbol": symbol, "status": "PENDING",
            "entry_index": 0, "last_checked_index": -1,
            "entry_price": 100.0, "sl": 95.0, "tp1": 110.0, "tp2": 120.0,
            "initial_qty": 1.0, "qty": 1.0, "notional_usdt": 100.0,
            "risk_budget_usdt": 5.0, "entry_fee_usdt": 0.1, "fees_usdt": 0.1,
            "realized_pnl_usdt": 0.0, "net_pnl_usdt": None,
            "net_pnl_pct_notional": None, "net_pnl_r": None,
            "tp1_done": False, "tp1_hit": False, "tp2_hit": False,
            "mfe_pct": 0.0, "mae_pct": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
            "exit_time": None,
        }
        market = {symbol: {"1h": pd.DataFrame([{
            "open_time": 0, "close_time": 3_599_999,
            "high": 104.0, "low": 98.0, "close": 101.0,
        }])}}

        hb.advance_shadow_ledger([shadow], market, 3_600_000)

        self.assertEqual(shadow["status"], "PENDING")
        self.assertAlmostEqual(shadow["mfe_pct"], 4.0, places=7)
        self.assertAlmostEqual(shadow["mae_pct"], -2.0, places=7)
        self.assertAlmostEqual(shadow["mfe_r"], 0.8, places=7)
        self.assertAlmostEqual(shadow["mae_r"], -0.4, places=7)


if __name__ == "__main__":
    unittest.main()
