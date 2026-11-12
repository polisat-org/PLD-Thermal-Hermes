import unittest

from hermes.thermal.policy import HeatingParams, ThermalStatus, decide_heating

PARAMS = HeatingParams()


class PolicyTest(unittest.TestCase):
    def test_no_sensor(self):
        decision = decide_heating(None, None, 0.0, False, 10.0, PARAMS)
        self.assertEqual(decision.duty, 0.0)
        self.assertFalse(decision.heating)
        self.assertTrue(decision.status & ThermalStatus.NO_SENSOR)

    def test_overtemp(self):
        decision = decide_heating(50.0, 50.0, 1.0, True, 10.0, PARAMS)
        self.assertEqual(decision.duty, 0.0)
        self.assertTrue(decision.status & ThermalStatus.OVERTEMP)

    def test_cold_turns_on(self):
        decision = decide_heating(0.0, 1.0, 0.0, False, 10.0, PARAMS)
        self.assertEqual(decision.duty, 1.0)
        self.assertTrue(decision.heating)

    def test_hysteresis_keeps_heating(self):
        decision = decide_heating(10.0, 10.0, 0.5, True, 10.0, PARAMS)
        self.assertAlmostEqual(decision.duty, 0.5)
        self.assertTrue(decision.heating)

    def test_hysteresis_stops(self):
        decision = decide_heating(16.0, 16.0, 0.5, True, 10.0, PARAMS)
        self.assertEqual(decision.duty, 0.0)
        self.assertFalse(decision.heating)

    def test_rate_limit(self):
        decision = decide_heating(0.0, 0.0, 0.0, False, 1.0, PARAMS)
        self.assertAlmostEqual(decision.duty, 0.5)

    def test_min_duty(self):
        decision = decide_heating(14.9, 14.9, 0.5, True, 10.0, PARAMS)
        self.assertAlmostEqual(decision.duty, 0.05)

    def test_current_limit(self):
        params = HeatingParams(max_current_a=0.05)
        decision = decide_heating(0.0, 0.0, 1.0, True, 10.0, params)
        self.assertAlmostEqual(decision.duty, 0.05 / (13.8 / 82.0))
        self.assertTrue(decision.status & ThermalStatus.CURRENT_LIMIT)


if __name__ == "__main__":
    unittest.main()
