"""No-lookahead checks for 1H setup candles and 15m execution candles."""
import unittest
import pandas as pd
import historical_backtest as hb


class MTFConfirmationCutoffTests(unittest.TestCase):
    def candles(self, interval_ms, count=20):
        rows = []
        for i in range(count):
            start = i * interval_ms
            rows.append({"open_time": start, "close_time": start + interval_ms - 1,
                         "open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.5, "volume": 1000.0, "quote_volume": 100000.0,
                         "trades": 10, "taker_base": 500.0, "taker_quote": 50000.0,
                         "ignore": 0})
        return pd.DataFrame(rows)

    def test_1h_setup_and_15m_execution_frames_exclude_exact_cutoff_candles(self):
        decision_ms = 35 * 60_000
        for interval_ms in (60 * 60_000, 15 * 60_000):
            with self.subTest(interval_ms=interval_ms):
                visible = hb.frame_at(self.candles(interval_ms), decision_ms)
                self.assertGreater(len(visible), 0)
                self.assertTrue((visible["close_time"] < decision_ms).all())
                self.assertFalse((visible["close_time"] == decision_ms).any())

    def test_entry_is_first_15m_open_strictly_after_decision(self):
        bars = self.candles(15 * 60_000, count=8)
        decision_ms = 5 * 60_000
        idx = hb.next_entry_index(bars, decision_ms)
        self.assertEqual(idx, 1)  # opens at :15; the :00 candle was already in progress
        self.assertGreater(int(bars.iloc[idx]["open_time"]), decision_ms)

    def test_15m_scan_schedule_and_execution_bars_use_strict_cutoff(self):
        scans = list(hb.scan_times(0, 3 * 60 * 60 * 1000))
        self.assertTrue(scans)
        self.assertGreaterEqual(len(scans), 10)
        for decision_ms in scans:
            self.assertIn((decision_ms // 60_000) % 60, (5, 20, 35, 50))
            visible = hb.frame_at(self.candles(15 * 60_000), decision_ms)
            self.assertTrue((visible["close_time"] < decision_ms).all())


if __name__ == "__main__":
    unittest.main()
