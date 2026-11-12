import unittest
from types import SimpleNamespace

from hermes.mission import MissionManager, MissionState


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeGpio:
    def __init__(self):
        self.events = []
        self.sdr_on = False

    def poll_events(self):
        events, self.events = self.events, []
        return events

    def set_sdr(self, on):
        self.sdr_on = bool(on)


class FakeDecoder:
    def __init__(self):
        self.start_ok = True
        self.alive = True
        self.stop_calls = 0

    def start(self):
        return self.start_ok

    def stop(self):
        self.stop_calls += 1

    def is_alive(self):
        return self.alive


class FakeTimebase:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.active = False

    def start_mission(self):
        self.started += 1
        self.active = True

    def stop_mission(self):
        self.stopped += 1
        self.active = False

    @property
    def mission_active(self):
        return self.active


class MissionTest(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            SDR_BOOT_DELAY_S=0.1,
            SDR_MIN_OFF_TIME_S=0.1,
            DECODER_RESTART_BACKOFF_S=1.0,
            MISSION_DURATION_S=10.0,
        )
        self.clock = FakeClock()
        self.gpio = FakeGpio()
        self.decoder = FakeDecoder()
        self.timebase = FakeTimebase()
        self.mission = MissionManager(
            self.config, self.timebase, self.gpio, self.decoder, clock=self.clock
        )

    def test_start_sequence(self):
        self.gpio.events = ["on"]
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.POWERING)
        self.assertTrue(self.gpio.sdr_on)
        self.clock.now = 0.2
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.STARTING)
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.RUNNING)

    def test_duration_stops(self):
        self.gpio.events = ["on"]
        self.mission.tick()
        self.clock.now = 0.2
        self.mission.tick()
        self.mission.tick()
        self.clock.now = 11.0
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.STOPPING)
        self.assertFalse(self.gpio.sdr_on)
        self.assertEqual(self.timebase.stopped, 1)
        self.clock.now = 11.2
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.OFF)

    def test_off_input(self):
        self.gpio.events = ["on"]
        self.mission.tick()
        self.gpio.events = ["off"]
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.STOPPING)
        self.assertFalse(self.gpio.sdr_on)

    def test_decoder_fault_and_retry(self):
        self.gpio.events = ["on"]
        self.mission.tick()
        self.clock.now = 0.2
        self.mission.tick()
        self.mission.tick()
        self.decoder.alive = False
        self.clock.now = 2.0
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.FAULT)
        self.assertFalse(self.gpio.sdr_on)
        self.clock.now = 3.1
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.POWERING)
        self.assertTrue(self.gpio.sdr_on)

    def test_decoder_start_failure(self):
        self.decoder.start_ok = False
        self.gpio.events = ["on"]
        self.mission.tick()
        self.clock.now = 0.2
        self.mission.tick()
        self.mission.tick()
        self.assertEqual(self.mission.state, MissionState.FAULT)


if __name__ == "__main__":
    unittest.main()
