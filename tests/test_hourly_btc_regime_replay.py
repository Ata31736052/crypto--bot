import inspect
import unittest
from datetime import datetime, timezone

import pandas as pd

import main
import main_experimental
import historical_backtest as hb


class HourlyBtcRegimeReplayTests(unittest.TestCase):
    def test_scan_schedule_is_hourly_and_minute_ten(self):
        start = int(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        end = start + 4 * 3600 * 1000
        scans = list(hb.scan_times(start, end))
        self.assertEqual(len(scans), 4)
        stamps = [datetime.fromtimestamp(x / 1000, timezone.utc) for x in scans]
        self.assertTrue(all(x.minute == 10 for x in stamps))
        self.assertTrue(all((stamps[i+1] - stamps[i]).total_seconds() == 3600 for i in range(3)))

    def test_scan_does_not_reuse_a_decision_at_or_before_start(self):
        start = int(datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc).timestamp() * 1000)
        first = next(hb.scan_times(start, start + 3 * 3600 * 1000))
        stamp = datetime.fromtimestamp(first / 1000, timezone.utc)
        self.assertEqual((stamp.hour, stamp.minute), (1, 10))

    def test_frame_at_excludes_candle_closing_at_decision(self):
        frame = pd.DataFrame({"close_time": [999, 1999, 2999], "close": [1, 2, 3]})
        result = hb.frame_at(frame, 2000)
        self.assertEqual(result["close_time"].tolist(), [999])

    def test_main_keeps_btc_hard_veto_and_pr22_removes_it(self):
        self.assertIn("BTC Daily+4H bearish", inspect.getsource(main.bottom_setup))
        self.assertNotIn("BTC Daily+4H bearish", inspect.getsource(main_experimental.bottom_setup))

    def test_pr22_scores_btc_as_context_not_veto(self):
        self.assertEqual(main_experimental.bottom_btc_context_points("BULLISH", "BULLISH"), 2)
        self.assertEqual(main_experimental.bottom_btc_context_points("NEUTRAL", "BEARISH"), 1)
        self.assertEqual(main_experimental.bottom_btc_context_points("BEARISH", "BEARISH"), 0)

    def test_analyze_symbol_uses_current_production_signature(self):
        self.assertEqual(list(inspect.signature(main.analyze_symbol).parameters), ["symbol", "btc_d", "btc_h"])
        self.assertEqual(list(inspect.signature(main_experimental.analyze_symbol).parameters), ["symbol", "btc_d", "btc_h"])

    def test_both_engines_keep_existing_quality_thresholds(self):
        for engine in (main, main_experimental):
            self.assertEqual(engine.BOTTOM_MIN_SCORE, 66)
            self.assertEqual(engine.BOTTOM_VOL_MIN, 0.60)
            self.assertEqual(engine.MIN_RR_TP1, 1.80)
            self.assertEqual(engine.MIN_RR_TP2, 2.40)
            self.assertEqual(engine.MAX_SIGNALS, 3)


if __name__ == "__main__":
    unittest.main()
