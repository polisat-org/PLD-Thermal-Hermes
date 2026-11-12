import time
import unittest

from hermes.gpio_control import GpioController, MockGpio


class GpioTest(unittest.TestCase):
    def test_debounced_on_off(self):
        backend = MockGpio()
        gpio = GpioController(backend, debounce_s=0.005)
        backend.inputs[27] = 1
        self.assertEqual(gpio.poll_events(), [])
        time.sleep(0.01)
        self.assertEqual(gpio.poll_events(), ["on"])
        backend.inputs[27] = 0
        backend.inputs[22] = 1
        self.assertEqual(gpio.poll_events(), [])
        time.sleep(0.01)
        self.assertEqual(gpio.poll_events(), ["off"])

    def test_sdr_output(self):
        backend = MockGpio()
        gpio = GpioController(backend)
        gpio.set_sdr(True)
        self.assertEqual(backend.outputs[17], 1)
        self.assertTrue(gpio.sdr_on)
        gpio.set_sdr(False)
        self.assertEqual(backend.outputs[17], 0)

    def test_active_low(self):
        backend = MockGpio()
        gpio = GpioController(backend, active_high=False, debounce_s=0.001)
        backend.inputs[22] = 1
        gpio.set_sdr(True)
        self.assertEqual(backend.outputs[17], 0)
        backend.inputs[27] = 0
        self.assertEqual(gpio.poll_events(), [])
        time.sleep(0.005)
        self.assertEqual(gpio.poll_events(), ["on"])


if __name__ == "__main__":
    unittest.main()
