import unittest

import pandas as pd

import historical_backtest as hb


class ReplayIndexingTests(unittest.TestCase):
    def test_frame_at_matches_strict_filter_and_tail(self):
        df = pd.DataFrame({
            "open_time": [0, 10, 20, 30, 40],
            "close_time": [9, 19, 29, 39, 49],
            "close": [100, 101, 102, 103, 104],
        })

        actual = hb.frame_at(df, decision_ms=30, n=2)
        expected = df[df["close_time"] < 30].tail(2).reset_index(drop=True)

        pd.testing.assert_frame_equal(actual, expected)

    def test_frame_at_excludes_candle_closing_exactly_at_decision(self):
        df = pd.DataFrame({
            "open_time": [0, 10, 20],
            "close_time": [9, 19, 29],
            "close": [100, 101, 102],
        })

        actual = hb.frame_at(df, decision_ms=29, n=250)
        self.assertEqual(actual["close_time"].tolist(), [9, 19])

    def test_next_entry_index_is_strictly_after_decision(self):
        df = pd.DataFrame({"open_time": [10, 20, 30]})

        self.assertEqual(hb.next_entry_index(df, decision_ms=20), 2)
        self.assertEqual(hb.next_entry_index(df, decision_ms=9), 0)
        self.assertIsNone(hb.next_entry_index(df, decision_ms=30))

    def test_empty_frames_keep_empty_shape(self):
        df = pd.DataFrame(columns=["open_time", "close_time", "close"])
        actual = hb.frame_at(df, decision_ms=10, n=250)
        self.assertEqual(list(actual.columns), list(df.columns))
        self.assertTrue(actual.empty)


if __name__ == "__main__":
    unittest.main()
