import time
import unittest
from dataclasses import replace

from hermes_simples.config import Config
from hermes_simples.gpio import Gpio
from hermes_simples.mission import Mission
from hermes_simples.plane import BUFFER_SIZE
from hermes_simples.uart import UartSender


class FakeGpio:
    def __init__(self, on=False, off=False):
        self._on = on
        self._off = off
        self.sdr = False
        self.ok = True

    def on_pressed(self):
        return self._on

    def off_pressed(self):
        return self._off

    def set_sdr(self, on):
        self.sdr = bool(on) and self.ok
        return self.ok

    def close(self):
        self.sdr = False


class FakeUart:
    def __init__(self):
        self.data = []

    def send(self, payload):
        self.data.append(payload)
        return len(payload)


class FakeSource:
    def __init__(self):
        self.polls = 0
        self.errors = 1
        self.total_messages = 42

    def poll_once(self):
        self.polls += 1
        return 0


class FakeDecoder:
    def __init__(self, ready=True):
        self.starts = 0
        self.stops = 0
        self.ready = ready

    def start(self):
        self.starts += 1
        return self.ready

    def stop(self):
        self.stops += 1


class FakeAircraft:
    def __init__(self, count=3):
        self.count = count
        self.clears = 0
        self.starts = 0
        self._t0 = None

    def set_mission_start(self):
        self.starts += 1
        self._t0 = time.monotonic()

    def met_s(self):
        if self._t0 is None:
            return 0.0
        return round(time.monotonic() - self._t0, 3)

    def __len__(self):
        return self.count

    def snapshot(self):
        from hermes_simples.plane import empty_slot
        return [empty_slot() for _ in range(20)]

    def clear(self):
        self.clears += 1


class FakeLog:
    def __init__(self):
        self.starts = 0
        self.finishes = 0
        self.rows = []

    def start(self):
        self.starts += 1

    def log(self, **values):
        self.rows.append(values)

    def finish(self, **values):
        self.finishes += 1
        self.log(event="stop", **values)


def fast_cfg():
    return replace(Config(), GPIO_POLL_S=0.001, JSON_POLL_S=0.005,
                   SEND_PERIOD_S=0.005, SDR_MIN_OFF_S=0)


class MissionTests(unittest.TestCase):
    def make(self, on=False, off=False, decoder_ready=True):
        gpio = FakeGpio(on, off)
        uart = FakeUart()
        source = FakeSource()
        decoder = FakeDecoder(ready=decoder_ready)
        aircraft = FakeAircraft()
        log = FakeLog()
        mission = Mission(fast_cfg(), gpio, uart, aircraft, source, decoder,
                          mission_log=log)
        return mission, gpio, uart, source, decoder, aircraft, log

    def test_on_sends_and_off_stops(self):
        mission, gpio, uart, source, decoder, _, _ = self.make(on=True)
        mission.run(0.08)
        self.assertTrue(mission.active)
        self.assertGreater(len(uart.data), 2)
        self.assertEqual(len(uart.data[0]), BUFFER_SIZE)
        self.assertGreater(source.polls, 2)
        self.assertEqual(decoder.starts, 1)
        mission.stop()
        self.assertFalse(mission.active)
        self.assertFalse(gpio.sdr)
        self.assertEqual(decoder.stops, 1)

    def test_off_has_priority(self):
        mission, gpio, uart, _, _, _, log = self.make(on=True, off=True)
        mission.run(0.03)
        self.assertFalse(mission.active)
        self.assertEqual(uart.data, [])
        self.assertEqual(log.starts, 0)

    def test_no_on_no_sends(self):
        mission, _, uart, _, decoder, _, log = self.make()
        mission.run(0.03)
        self.assertEqual(uart.data, [])
        self.assertEqual(decoder.starts, 0)
        self.assertEqual(log.rows, [])

    def test_start_sets_timestamp_and_logs_sends(self):
        mission, _, _, _, _, aircraft, log = self.make(on=True)
        mission.run(0.04)
        self.assertEqual(aircraft.starts, 1)
        self.assertEqual(log.starts, 1)
        self.assertGreater(len(log.rows), 0)
        row = log.rows[0]
        self.assertEqual(row["event"], "send")
        self.assertEqual(row["aircraft_count"], 3)
        self.assertEqual(row["messages_total"], 42)
        self.assertEqual(row["decoder_errors"], 1)
        self.assertEqual(row["buffers_sent"], 1)
        self.assertGreaterEqual(row["met_s"], 0.0)

    def test_stop_logs_finish_once(self):
        mission, _, _, _, _, _, log = self.make(on=True)
        mission.run(0.03)
        mission.stop()
        self.assertEqual(log.finishes, 1)
        self.assertEqual(log.rows[-1]["event"], "stop")
        mission.stop()
        self.assertEqual(log.finishes, 1)

    def test_failed_sdr_does_not_start_or_log(self):
        mission, gpio, uart, _, decoder, aircraft, log = self.make(on=True)
        gpio.ok = False
        mission.run(0.03)
        self.assertFalse(mission.active)
        self.assertEqual(decoder.starts, 0)
        self.assertEqual(aircraft.starts, 0)
        self.assertEqual(log.starts, 0)
        self.assertEqual(log.rows, [])
        self.assertEqual(uart.data, [])

    def test_failed_decoder_does_not_start(self):
        mission, gpio, uart, _, decoder, aircraft, log = self.make(
            on=True, decoder_ready=False)
        mission.run(0.03)
        self.assertFalse(mission.active)
        self.assertFalse(gpio.sdr)
        self.assertGreaterEqual(decoder.starts, 1)
        self.assertGreaterEqual(decoder.stops, 1)
        self.assertEqual(aircraft.starts, 0)
        self.assertEqual(log.starts, 0)
        self.assertEqual(uart.data, [])

    def test_sdr_minimum_off_time(self):
        cfg = replace(Config(), SDR_MIN_OFF_S=60)
        gpio = Gpio(cfg, simulate=True)
        self.assertTrue(gpio.set_sdr(True))
        gpio.set_sdr(False)
        self.assertFalse(gpio.set_sdr(True))
        self.assertFalse(gpio.sdr_on())


if __name__ == "__main__":
    unittest.main()
