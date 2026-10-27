"""No-lookahead checks for the 15m/30m experimental confirmation frames."""
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

    def test_15m_and_30m_frames_exclude_open_or_exact_cutoff_candles(self):
        decision_ms = 35 * 60_000
        for interval_ms in (15 * 60_000, 30 * 60_000):
            with self.subTest(interval_ms=interval_ms):
                visible = hb.frame_at(self.candles(interval_ms), decision_ms)
                self.assertGreater(len(visible), 0)
                self.assertTrue((visible["close_time"] < decision_ms).all())
                self.assertFalse((visible["close_time"] == decision_ms).any())

    def test_entry_is_first_15m_open_strictly_after_decision(self):
        bars = self.candles(15 * 60_000, count=8)
        decision_ms = 35 * 60_000
        idx = hb.next_entry_index(bars, decision_ms)
        self.assertEqual(idx, 3)  # opens at :45; the :30 candle was already in progress
        self.assertGreater(int(bars.iloc[idx]["open_time"]), decision_ms)

    def test_hourly_scan_schedule_uses_minute_35_for_closed_mtf_bars(self):
        scans = list(hb.scan_times(0, 3 * 60 * 60 * 1000))
        self.assertTrue(scans)
        for decision_ms in scans:
            self.assertEqual((decision_ms // 60_000) % 60, 35)
            for interval_ms in (15 * 60_000, 30 * 60_000):
                visible = hb.frame_at(self.candles(interval_ms), decision_ms)
                self.assertTrue((visible["close_time"] < decision_ms).all())


if __name__ == "__main__":
    unittest.main()
