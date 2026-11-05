"""Differential regression: excursion-only changes must not change portfolio accounting.

The baseline helper intentionally preserves the pre-change advance_positions loop.
It shares close_piece with the production module so the comparison isolates the
control-flow change under test; fills, fee calculation, and portfolio accounting
remain the same implementation.
"""
import copy
import unittest

import numpy as np
import pandas as pd

import historical_backtest as hb


def baseline_advance_positions(port, market, decision_ms):
    """Frozen pre-excursion-change position-advance logic."""
    for p in list(port["positions"]):
        h1 = market[p["symbol"]]["1h"]
        end_idxs = np.flatnonzero(
            (h1["close_time"].to_numpy(dtype=np.int64) < decision_ms)
            & (np.arange(len(h1)) > p["last_checked_index"])
        )
        for ix in end_idxs:
            bar = h1.iloc[int(ix)]
            hi, lo = float(bar["high"]), float(bar["low"])
            p["mfe_pct"] = max(
                p["mfe_pct"], (hi / p["entry_price"] - 1) * 100
            )
            p["mae_pct"] = min(
                p["mae_pct"], (lo / p["entry_price"] - 1) * 100
            )
            ts = int(bar["close_time"])
            if lo <= p["sl"]:
                hb.close_piece(port, p, p["qty"], p["sl"], "SL", ts)
            else:
                if not p["tp1_done"] and hi >= p["tp1"]:
                    hb.close_piece(port, p, p["qty"] * 0.5, p["tp1"], "TP1", ts)
                    if p["qty"] > 0 and hi >= p["tp2"]:
                        hb.close_piece(port, p, p["qty"], p["tp2"], "TP2", ts)
                elif p["tp1_done"] and hi >= p["tp2"]:
                    hb.close_piece(port, p, p["qty"], p["tp2"], "TP2", ts)
            if p not in port["positions"]:
                break
            p["last_checked_index"] = int(ix)
            if int(ix) - p["entry_index"] >= hb.MAX_HOLD_HOURS:
                hb.close_piece(
                    port, p, p["qty"], float(bar["close"]), "TIME", ts
                )
                break
        if p in port["positions"] and len(end_idxs):
            p["last_checked_index"] = int(end_idxs[-1])


class ExitFinancialParityTests(unittest.TestCase):
    def make_position(self, sl=95.0, tp1=105.0, tp2=110.0):
        return {
            "symbol": "AAAUSDT", "entry_index": 0, "last_checked_index": -1,
            "entry_price": 100.0, "sl": sl, "tp1": tp1, "tp2": tp2,
            "qty": 1.0, "initial_qty": 1.0, "tp1_done": False,
            "mfe_pct": 0.0, "mae_pct": 0.0, "realized_pnl": 0.0,
            "entry_fee": 0.0,
        }

    def run_case(self, rows, advance):
        position = self.make_position()
        port = {
            "positions": [position], "closed": [], "cash": 0.0, "fees_paid": 0.0,
        }
        market = {"AAAUSDT": {"1h": pd.DataFrame(rows)}}
        advance(port, market, int(rows[-1]["close_time"]) + 1)
        return port

    def assert_financial_parity(self, rows):
        baseline = self.run_case(rows, baseline_advance_positions)
        current = self.run_case(rows, hb.advance_positions)

        self.assertEqual(
            [p["exit_reason"] for p in baseline["closed"]],
            [p["exit_reason"] for p in current["closed"]],
        )
        self.assertEqual(
            [p["exit_time"] for p in baseline["closed"]],
            [p["exit_time"] for p in current["closed"]],
        )
        self.assertEqual(len(baseline["positions"]), len(current["positions"]))
        self.assertEqual(len(baseline["closed"]), len(current["closed"]))
        for field in ("cash", "fees_paid"):
            self.assertAlmostEqual(baseline[field], current[field], places=10)
        for old, new in zip(baseline["closed"], current["closed"]):
            for field in (
                "qty", "realized_pnl", "final_pnl", "entry_price",
                "sl", "tp1", "tp2", "tp1_done",
            ):
                if isinstance(old[field], bool):
                    self.assertEqual(old[field], new[field])
                else:
                    self.assertAlmostEqual(old[field], new[field], places=10)
        # Excursion values are deliberately excluded from parity: their purpose
        # is to change when the candle itself triggers an exit.

    def test_stop_exit_financial_parity(self):
        self.assert_financial_parity([{
            "open_time": 0, "close_time": 3_599_999,
            "high": 130.0, "low": 90.0, "close": 100.0,
        }])

    def test_partial_tp1_then_tp2_financial_parity(self):
        self.assert_financial_parity([
            {"open_time": 0, "close_time": 3_599_999,
             "high": 106.0, "low": 99.0, "close": 104.0},
            {"open_time": 3_600_000, "close_time": 7_199_999,
             "high": 112.0, "low": 101.0, "close": 111.0},
        ])

    def test_time_exit_financial_parity(self):
        rows = []
        for i in range(hb.MAX_HOLD_HOURS + 1):
            is_exit = i == hb.MAX_HOLD_HOURS
            rows.append({
                "open_time": i * 3_600_000,
                "close_time": (i + 1) * 3_600_000 - 1,
                "high": 130.0 if is_exit else 104.0,
                "low": 91.0 if is_exit else 98.0,
                "close": 100.0,
            })
        self.assert_financial_parity(rows)


if __name__ == "__main__":
    unittest.main()
