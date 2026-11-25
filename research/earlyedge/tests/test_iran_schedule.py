import unittest
from datetime import datetime, timezone

from research.earlyedge.iran_schedule import iran_now, is_scan_window, scan_window_status


class IranScheduleTests(unittest.TestCase):
    def test_start_is_inclusive(self):
        self.assertTrue(is_scan_window(datetime(2026, 11, 25, 7, 0)))

    def test_end_is_exclusive(self):
        self.assertFalse(is_scan_window(datetime(2026, 11, 25, 23, 0)))

    def test_last_minute_before_end_is_allowed(self):
        self.assertTrue(is_scan_window(datetime(2026, 11, 25, 22, 59)))

    def test_overnight_is_blocked(self):
        for hour in (23, 0, 1, 2, 3, 4, 5, 6):
            with self.subTest(hour=hour):
                self.assertFalse(is_scan_window(datetime(2026, 11, 25, hour, 30)))

    def test_utc_aware_time_is_converted_to_iran(self):
        # 03:30 UTC == 07:00 in Iran.
        utc = datetime(2026, 11, 25, 3, 30, tzinfo=timezone.utc)
        local = iran_now(utc)
        self.assertEqual((local.hour, local.minute), (7, 0))
        self.assertTrue(is_scan_window(utc))

    def test_status_explains_block(self):
        status = scan_window_status(datetime(2026, 11, 25, 23, 15))
        self.assertFalse(status["allowed"])
        self.assertEqual(status["timezone"], "Asia/Tehran")
        self.assertEqual(status["reason"], "outside_iran_scan_window")


if __name__ == "__main__":
    unittest.main()
