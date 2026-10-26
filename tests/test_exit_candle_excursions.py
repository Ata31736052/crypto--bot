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


if __name__ == "__main__":
    unittest.main()
