import unittest
from hermes.config import CONFIG
from hermes.thermal.controller import ThermalController


class FakeSensors:
    def __init__(self, groups):
        self.groups = groups

    def read_groups(self):
        return self.groups


class FakePwm:
    def __init__(self):
        self.duties = {1: 0.0, 2: 0.0}
        self.closed = False

    def set_duty(self, pack, duty):
        self.duties[pack] = duty

    def close(self):
        self.closed = True


class ControllerTest(unittest.TestCase):
    def make(self, groups):
        pwm = FakePwm()
        controller = ThermalController(
            CONFIG, FakeSensors(groups), pwm, lambda: 1000
        )
        return controller, pwm

    def test_cold_turns_on(self):
        controller, pwm = self.make({1: (0.0, 0.0), 2: (0.0, 0.0)})
        sample = controller.tick(now_mono=0.0)
        self.assertEqual(pwm.duties, {1: 1.0, 2: 1.0})
        self.assertEqual(sample.duties, (1.0, 1.0))

    def test_overtemp_off(self):
        controller, pwm = self.make({1: (50.0, 50.0), 2: (50.0, 50.0)})
        controller.tick(now_mono=0.0)
        self.assertEqual(pwm.duties, {1: 0.0, 2: 0.0})

    def test_missing_sensor_off(self):
        controller, pwm = self.make({1: (None, None), 2: (None, None)})
        sample = controller.tick(now_mono=0.0)
        self.assertEqual(pwm.duties, {1: 0.0, 2: 0.0})
        self.assertEqual(sample.temps_c, (None, None, None, None))

    def test_disabled_off(self):
        controller, pwm = self.make({1: (0.0, 0.0), 2: (0.0, 0.0)})
        controller.set_enabled(False)
        controller.tick(now_mono=0.0)
        self.assertEqual(pwm.duties, {1: 0.0, 2: 0.0})

    def test_snapshot_temps(self):
        controller, _ = self.make({1: (1.0, 2.0), 2: (3.0, 4.0)})
        controller.tick(now_mono=0.0)
        self.assertEqual(controller.snapshot().temps_c, (1.0, 2.0, 3.0, 4.0))


if __name__ == "__main__":
    unittest.main()
