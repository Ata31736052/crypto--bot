import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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
        pd.testing.assert_frame_equal(
            hb.frame_at(original_raw, decision_ms),
            hb.frame_at(mutated_raw, decision_ms)
        )

    def test_candle_closing_exactly_at_decision_is_excluded(self):
        decision_ms, raw = self.make_frame()
        raw.loc[2, "close_time"] = decision_ms
        frame = hb.frame_at(raw, decision_ms)
        self.assertTrue((frame["close_time"] < decision_ms).all())
        self.assertNotIn(decision_ms, frame["close_time"].tolist())

    def test_scan_schedule_is_hourly_at_minute_ten(self):
        start = int(datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        end = int(datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
        stamps = [datetime.fromtimestamp(ms / 1000, timezone.utc)
                  for ms in hb.scan_times(start, end)]
        self.assertEqual([stamp.hour for stamp in stamps], [0, 1, 2, 3])
        self.assertTrue(all(stamp.minute == 10 for stamp in stamps))

    def test_entry_open_must_be_strictly_after_decision(self):
        h1 = pd.DataFrame({
            "open_time": [1_000, 2_000, 3_000],
            "close_time": [1_999, 2_999, 3_999],
            "open": [10.0, 20.0, 30.0],
        })
        self.assertEqual(hb.next_entry_index(h1, 2_000), 2)

    def test_no_entry_when_only_open_is_at_or_before_decision(self):
        h1 = pd.DataFrame({
            "open_time": [1_000, 2_000],
            "close_time": [1_999, 2_999],
            "open": [10.0, 20.0],
        })
        self.assertIsNone(hb.next_entry_index(h1, 2_000))

    def test_future_entry_open_cannot_change_decision_time_ranking(self):
        port = hb.make_portfolio("test")
        candidates = [
            {"symbol": "AAAUSDT", "setup": {"strategy": "TREND", "score": 90},
             "h": pd.DataFrame({"close": [100.0]}), "entry_open_raw": 100.0},
            {"symbol": "BBBUSDT", "setup": {"strategy": "TREND", "score": 90},
             "h": pd.DataFrame({"close": [200.0]}), "entry_open_raw": 200.0},
        ]

        def fake_build_trade(h, strategy, entry_override=None, enable_resistance_gate=True):
            # Ranking trade deliberately derives from the last decision-time close.
            close = float(h.iloc[-1]["close"])
            return {"entry": close, "rr2": close / 100.0,
                    "risk_pct": 2.0 if close == 100.0 else 1.0}, None

        with patch.object(hb.main, "build_trade", side_effect=fake_build_trade):
            first = hb.rank_scan_candidates(candidates, port, 10_000)
            changed = [dict(row, entry_open_raw=9_999_999.0 - i)
                       for i, row in enumerate(candidates)]
            second = hb.rank_scan_candidates(changed, hb.make_portfolio("test"), 10_000)

        self.assertEqual([x["symbol"] for x in first],
                         [x["symbol"] for x in second])
        self.assertEqual([x["symbol"] for x in first], ["BBBUSDT", "AAAUSDT"])


if __name__ == "__main__":
    unittest.main()
