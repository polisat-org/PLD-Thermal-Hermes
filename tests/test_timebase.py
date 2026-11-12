import time
import unittest

from hermes.timebase import Timebase


class TimebaseTest(unittest.TestCase):
    def test_zero_before_start(self):
        self.assertEqual(Timebase(boot_id="x").met_ms(), 0)

    def test_start_and_elapsed(self):
        tb = Timebase(boot_id="x")
        mission_id = tb.start_mission()
        self.assertTrue(mission_id)
        self.assertTrue(tb.mission_active)
        first = tb.met_ms()
        time.sleep(0.02)
        self.assertGreaterEqual(tb.met_ms(), first + 10)

    def test_time_sync(self):
        tb = Timebase(boot_id="x")
        tb.start_mission()
        tb.apply_time_sync(5000)
        self.assertTrue(tb.synced)
        time.sleep(0.02)
        self.assertGreaterEqual(tb.met_ms(), 5010)
        self.assertLess(tb.met_ms(), 6000)

    def test_stop(self):
        tb = Timebase(boot_id="x")
        tb.start_mission()
        tb.stop_mission()
        self.assertFalse(tb.mission_active)


if __name__ == "__main__":
    unittest.main()
