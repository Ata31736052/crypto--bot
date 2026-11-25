import tempfile
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from unittest.mock import Mock

from research.earlyedge.live_runner import run_scan, _bars, _format_alert, _decision_indices


class LiveRunnerTests(unittest.TestCase):
    def test_night_window_exits_before_any_network_or_telegram_io(self):
        session = Mock()
        with tempfile.TemporaryDirectory() as d:
            result = run_scan(
                now=datetime(2026, 11, 25, 23, 0),
                session=session, telegram_token="x", telegram_chat_id="y",
                state_path=Path(d) / "state.json",
            )
        self.assertEqual(result["status"], "skipped_outside_iran_window")
        self.assertEqual(result["api_calls"], 0)
        session.get.assert_not_called()
        session.post.assert_not_called()

    def test_utc_time_converts_to_iran_before_guard(self):
        session = Mock()
        # 19:30 UTC = 23:00 Iran; must exit before network.
        result = run_scan(now=datetime(2026, 11, 25, 19, 30, tzinfo=timezone.utc),
                          session=session)
        self.assertEqual(result["status"], "skipped_outside_iran_window")
        session.get.assert_not_called()

    def test_forming_candle_is_excluded(self):
        now_ms = 1_000_000
        raw = [
            [0, "1", "2", "0.5", "1.5", "10", "899999"],
            [900000, "1", "2", "0.5", "1.5", "10", "1799999"],
        ]
        bars = _bars(raw, now_ms, 900000)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close_time"], 899999)

    def test_morning_decisions_exclude_overnight_candles(self):
        now = datetime(2026, 11, 25, 7, 30, tzinfo=ZoneInfo("Asia/Tehran"))
        start_ms = int(datetime(2026, 11, 25, 7, 0, tzinfo=ZoneInfo("Asia/Tehran")).timestamp() * 1000)
        bars = [
            {"close_time": start_ms - 900001},  # 06:45 close: overnight, excluded
            {"close_time": start_ms - 1},       # 07:00 close: eligible
            {"close_time": start_ms + 899999},  # 07:15 close: eligible
            {"close_time": start_ms + 1799999}, # 07:30 close: eligible
        ]
        self.assertEqual(_decision_indices(bars, now), [2, 3])


    def test_entry_alert_contains_risk_and_two_targets(self):
        signal = {"price": 100.0, "stop_pct": 1.5, "state": "ENTRY",
                  "engine": "BOTTOM_REVERSAL", "reason": "test"}
        bars = [{"high": float(x), "low": float(x)-1, "close": float(x)}
                for x in range(1, 40)]
        message = _format_alert("ETHUSDT", signal, bars)
        self.assertIn("حد ضرر", message)
        self.assertIn("هدف ۱", message)
        self.assertIn("هدف ۲", message)
        self.assertIn("RR=1.8", message)
        self.assertIn("RR=2.4", message)


if __name__ == "__main__":
    unittest.main()
