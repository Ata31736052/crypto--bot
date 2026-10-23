import unittest
from datetime import datetime, timezone

import pandas as pd

import historical_backtest as hb


class HistoricalCutoffTests(unittest.TestCase):
    def make_frame(self):
        decision_ms = 3_000
        rows = []
        for i in range(5):
            rows.append({
                "open_time": i * 1_000,
                "close_time": i * 1_000 + 999,
                "open": 100.0 + i,
                "high": 102.0 + i,
                "low": 98.0 + i,
                "close": 101.0 + i,
                "volume": 10.0 + i,
            })
        return decision_ms, pd.DataFrame(rows)

    def test_future_mutation_does_not_change_frame_at_decision(self):
        decision_ms, original_raw = self.make_frame()
        mutated_raw = original_raw.copy(deep=True)
        future = mutated_raw["close_time"] >= decision_ms
        for column in ("open", "high", "low", "close", "volume"):
            mutated_raw.loc[future, column] = 999_999.0

        original_at_decision = hb.frame_at(original_raw, decision_ms)
        mutated_at_decision = hb.frame_at(mutated_raw, decision_ms)
        pd.testing.assert_frame_equal(original_at_decision, mutated_at_decision)

    def test_candle_closing_exactly_at_decision_is_excluded(self):
        decision_ms, raw = self.make_frame()
        raw.loc[2, "close_time"] = decision_ms
        frame = hb.frame_at(raw, decision_ms)
        self.assertTrue((frame["close_time"] < decision_ms).all())
        self.assertNotIn(decision_ms, frame["close_time"].tolist())

    def test_scan_schedule_is_hourly_at_minute_ten(self):
        start = int(datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        end = int(datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
        scans = list(hb.scan_times(start, end))
        stamps = [datetime.fromtimestamp(ms / 1000, timezone.utc) for ms in scans]
        self.assertEqual([stamp.hour for stamp in stamps], [0, 1, 2, 3])
        self.assertTrue(all(stamp.minute == 10 for stamp in stamps))


    def test_entry_open_must_be_strictly_after_decision(self):
        decision_ms = 2_000
        h1 = pd.DataFrame({
            "open_time": [1_000, 2_000, 3_000],
            "close_time": [1_999, 2_999, 3_999],
            "open": [10.0, 20.0, 30.0],
        })
        self.assertEqual(hb.next_entry_index(h1, decision_ms), 2)

    def test_no_entry_when_only_open_is_at_or_before_decision(self):
        h1 = pd.DataFrame({
            "open_time": [1_000, 2_000],
            "close_time": [1_999, 2_999],
            "open": [10.0, 20.0],
        })
        self.assertIsNone(hb.next_entry_index(h1, 2_000))


if __name__ == "__main__":
    unittest.main()
