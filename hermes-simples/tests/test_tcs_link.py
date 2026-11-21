"""Trailer térmico do TCS no fim do bloco da UART (tcs_link.py)."""
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_simples.config import Config
from hermes_simples.mission import Mission
from hermes_simples.plane import BUFFER_SIZE
from hermes_simples.tcs_link import TRAILER_SIZE, TcsLeitor, crc16_ccitt, decodificar
from tests.test_mission import FakeDecoder, FakeGpio, FakeSource

CAB = "t_s,hora,sonda1_bruta,sonda2_bruta,sonda1,sonda2,duty_A,situacao_A,sonda3_bruta,sonda4_bruta,sonda3,sonda4,duty_B,situacao_B\n"
AGORA = datetime(2026, 11, 21, 3, 0, 0, tzinfo=timezone(timedelta(hours=-3)))


def linha(hora, s=(20.5, 20.25, 19.75, ""), duty=(0.35, 0.0), sit=(0, 4)):
    return (f"1510.15,{hora.isoformat(timespec='milliseconds')},0,0,{s[0]},{s[1]},{duty[0]},{sit[0]},"
            f"0,0,{s[2]},{s[3]},{duty[1]},{sit[1]}\n")


class TrailerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.pasta = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def _log(self, texto, nome="tcs-20261121-030000.csv"):
        (self.pasta / nome).write_text(CAB + texto, encoding="utf-8")

    def test_crc_referencia(self):
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)

    def test_linha_recente(self):
        self._log(linha(AGORA - timedelta(seconds=1)))
        t = TcsLeitor(self.pasta, agora=lambda: AGORA).trailer(True, 3)
        self.assertEqual(len(t), TRAILER_SIZE)
        d = decodificar(t)
        self.assertTrue(d["valido"])
        self.assertAlmostEqual(d["idade_s"], 1.0, places=1)
        self.assertEqual(d["sondas"], [20.5, 20.25, 19.75, None])
        self.assertEqual(d["duty"], (35, 0))
        self.assertEqual(d["situacao"], (0, 4))
        self.assertTrue(d["missao"])
        self.assertEqual(d["avioes"], 3)
        self.assertAlmostEqual(d["t_s"], 1510.1, places=1)

    def test_linha_velha_e_invalida(self):
        self._log(linha(AGORA - timedelta(seconds=60)))
        d = decodificar(TcsLeitor(self.pasta, agora=lambda: AGORA).trailer(False, 0))
        self.assertFalse(d["valido"])
        self.assertEqual(d["sondas"][0], 20.5)     # o dado vai, mas marcado como velho

    def test_sem_log(self):
        d = decodificar(TcsLeitor(self.pasta / "nao_existe", agora=lambda: AGORA).trailer(False, 0))
        self.assertFalse(d["valido"])
        self.assertIsNone(d["idade_s"])
        self.assertEqual(d["sondas"], [None] * 4)

    def test_linha_pela_metade_usa_a_anterior(self):
        self._log(linha(AGORA - timedelta(seconds=2)) + "1511.15,2026-11-21T03:00")
        d = decodificar(TcsLeitor(self.pasta, agora=lambda: AGORA).trailer(False, 0))
        self.assertTrue(d["valido"])
        self.assertEqual(d["sondas"][0], 20.5)

    def test_arquivo_mais_novo(self):
        self._log(linha(AGORA - timedelta(seconds=900), s=(5.0, 5.0, 5.0, 5.0)), "tcs-20261120-010000.csv")
        self._log(linha(AGORA - timedelta(seconds=1)), "tcs-20261121-020000.csv")
        d = decodificar(TcsLeitor(self.pasta, agora=lambda: AGORA).trailer(False, 0))
        self.assertEqual(d["sondas"][0], 20.5)

    def test_crc_detecta_corrupcao(self):
        self._log(linha(AGORA))
        t = bytearray(TcsLeitor(self.pasta, agora=lambda: AGORA).trailer(False, 0))
        t[10] ^= 0x01
        with self.assertRaises(ValueError):
            decodificar(bytes(t))


class MissaoComTcsTest(unittest.TestCase):
    class Uart:
        def __init__(self):
            self.data = []

        def send(self, payload):
            self.data.append(payload)
            return len(payload)

    class Aircraft:
        def clear(self): pass
        def set_mission_start(self): pass
        def snapshot(self): return [{"icaoID": 0}] * 20
        def met_s(self): return 0.0
        def __len__(self): return 0

    def test_bloco_sai_com_missao_desligada(self):
        t = [0.0]
        cfg = replace(Config(), GPIO_POLL_S=0.05, SEND_PERIOD_S=5.0, MISSION_LOG_PATH="/dev/null")

        class Tcs:
            def trailer(self, missao, avioes):
                return b"T" * TRAILER_SIZE

        uart = self.Uart()
        m = Mission(cfg, FakeGpio(), uart, self.Aircraft(), FakeSource(), FakeDecoder(),
                    clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt), tcs=Tcs())
        m.run(duration=11.0)
        self.assertFalse(m.active)
        self.assertEqual(len(uart.data), 3)                    # t = 0, 5 e 10 s
        self.assertTrue(all(len(d) == BUFFER_SIZE + TRAILER_SIZE for d in uart.data))
        self.assertEqual(m.sends, 0)                           # não conta como envio de missão


if __name__ == "__main__":
    unittest.main()
