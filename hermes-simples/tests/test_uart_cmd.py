"""Comandos LIGAR/DESLIGAR do OBC pela UART (A5 5A cmd ~cmd)."""
import unittest
from dataclasses import replace

from hermes_simples.config import Config
from hermes_simples.mission import Mission
from hermes_simples.uart import encode_command, parse_commands
from tests.test_mission import FakeDecoder, FakeGpio, FakeSource


class ParseTest(unittest.TestCase):
    def test_encode(self):
        self.assertEqual(encode_command(True), bytes.fromhex("A55A01FE"))
        self.assertEqual(encode_command(False), bytes.fromhex("A55A00FF"))

    def test_parse_sequence_with_noise(self):
        buf = bytearray(b"\x00\x13" + encode_command(True) + b"\xA5\x5A\x01\x00" + b"\xFF" + encode_command(False))
        self.assertEqual(parse_commands(buf), [True, False])
        self.assertEqual(len(buf), 0)

    def test_split_across_reads(self):
        raw = encode_command(True)
        buf = bytearray(raw[:1])
        self.assertEqual(parse_commands(buf), [])
        buf.extend(raw[1:3])
        self.assertEqual(parse_commands(buf), [])
        buf.extend(raw[3:])
        self.assertEqual(parse_commands(buf), [True])

    def test_rejects_bad_value(self):
        buf = bytearray(b"\xA5\x5A\x02\xFD")
        self.assertEqual(parse_commands(buf), [])


class CmdUart:
    """UART falsa que entrega comandos uma vez por ciclo."""

    def __init__(self, script):
        self.script = list(script)   # lista de listas de comandos por ciclo
        self.data = []

    def read_commands(self):
        return self.script.pop(0) if self.script else []

    def send(self, payload):
        self.data.append(payload)
        return len(payload)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class MissionCmdTest(unittest.TestCase):
    def _mission(self, script):
        clock = Clock()
        cfg = replace(Config(), GPIO_POLL_S=0.05, SEND_PERIOD_S=5.0, MISSION_LOG_PATH="/dev/null")

        class Aircraft:
            def clear(self): pass
            def set_mission_start(self): pass
            def snapshot(self): return [{"icaoID": 0}] * 20
            def met_s(self): return 0.0
            def __len__(self): return 0

        uart = CmdUart(script)
        m = Mission(cfg, FakeGpio(), uart, Aircraft(), FakeSource(), FakeDecoder(),
                    clock=clock, sleep=clock.sleep)
        return m, uart

    def test_on_starts_and_off_stops(self):
        m, uart = self._mission([[True], [], [], [False]])
        m.run(duration=0.15)        # 3 ciclos: liga e envia
        self.assertTrue(m.active)
        self.assertEqual(len(uart.data), 1)
        m.run(duration=0.05)        # 4o ciclo: desliga
        self.assertFalse(m.active)

    def test_repeated_on_is_ignored_while_active(self):
        m, uart = self._mission([[True], [True, True], [True]])
        m.run(duration=0.15)
        self.assertTrue(m.active)
        self.assertEqual(m.decoder.starts, 1)

    def test_last_command_in_batch_wins(self):
        m, _ = self._mission([[True, False]])
        m.run(duration=0.05)
        self.assertFalse(m.active)


if __name__ == "__main__":
    unittest.main()
