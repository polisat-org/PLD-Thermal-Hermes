import json
import struct
import time
import unittest
import urllib.request

from hermes_simples.plane import BUFFER_SIZE, empty_slot, new_plane, pack_planes
from hermes_simples.rede import EnlaceRede, servidor_http, trailer_valido
from hermes_simples.tcs_link import crc16_ccitt


def trailer() -> bytes:
    corpo = b"TCS1" + bytes([1, 1]) + struct.pack("<H", 5) + b"\x00" * 22
    return corpo + struct.pack("<H", crc16_ccitt(corpo))


def bloco(n_avioes: int) -> bytes:
    planes = []
    for i in range(20):
        if i < n_avioes:
            p = new_plane(0xE48000 + i)
            p["flight"] = f"TAM{3050 + i}".encode()
            p.update(lat=-23.5, lon=-46.6, alt_baro=9000.0, gs=200.0, track=90.0, met_s=100.0, seen_pos=2.0)
            planes.append(p)
        else:
            planes.append(empty_slot())
    return pack_planes(planes) + trailer()


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class EnlaceTests(unittest.TestCase):
    def setUp(self):
        self.rel = Relogio()
        self.e = EnlaceRede(self.rel)

    def tick(self, dt):
        self.rel.t += dt
        self.e.passo()   # um passo do laço de downlink, sem thread

    def test_trailer_valido(self):
        self.assertTrue(trailer_valido(trailer()))
        ruim = bytearray(trailer()); ruim[10] ^= 1
        self.assertFalse(trailer_valido(bytes(ruim)))

    def test_h_em_todo_bloco_mesmo_sem_missao(self):
        self.e.send(bloco(2))
        _, qs = self.e.quadros_desde(0)
        self.assertEqual([q[:1] for _, q in qs], [b"H"])
        self.assertEqual(len(qs[0][1]), 33)

    def test_telecomando_bit_mission(self):
        self.assertEqual(self.e.telecomando(0x01), 0)
        self.assertTrue(self.e.missao)
        self.assertEqual(self.e.read_commands(), [True])
        self.assertEqual(self.e.telecomando(0x04), 0)   # Detumbling: payload desliga
        self.assertFalse(self.e.missao)
        self.assertEqual(self.e.read_commands(), [False])
        self.assertEqual(self.e.telecomando(0x10), 1)   # fora da máscara

    def test_p_a_cada_5_s_so_na_missao(self):
        self.e.send(bloco(3))
        self.tick(6)
        _, qs = self.e.quadros_desde(0)
        self.assertNotIn(b"P", [q[:1] for _, q in qs])          # missão desligada
        self.e.telecomando(0x01)
        self.e.send(bloco(3))
        self.tick(4.9)
        self.assertNotIn(b"P", [q[:1] for _, q in self.e.quadros_desde(0)[1]])
        self.tick(0.2)
        ps = [q for _, q in self.e.quadros_desde(0)[1] if q[:1] == b"P"]
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0][1], 3)
        self.assertEqual(len(ps[0]), 2 + 3 * 28)
        icao, lat, lon, alt, gs, trk, last_ms = struct.unpack_from("<IfffffI", ps[0], 2)
        self.assertEqual(icao, 0xE48000)
        self.assertEqual(last_ms, 98000)
        seq = [k for k, q in self.e.quadros_desde(0)[1] if q[:1] == b"P"][0]
        self.assertEqual(self.e.extras(seq), {"indicativos": {"E48000": "TAM3050", "E48001": "TAM3051", "E48002": "TAM3052"}})

    def test_quadros_desde(self):
        for _ in range(3):
            self.e.send(bloco(0))
        ultimo, qs = self.e.quadros_desde(1)
        self.assertEqual(ultimo, 3)
        self.assertEqual([k for k, _ in qs], [2, 3])


class HttpTests(unittest.TestCase):
    def test_rotas(self):
        e = EnlaceRede()
        srv = servidor_http(e, 0)
        porta = srv.server_address[1]
        try:
            e.send(bloco(1))
            with urllib.request.urlopen(f"http://127.0.0.1:{porta}/quadros?desde=0", timeout=2) as r:
                d = json.load(r)
            self.assertEqual(d["ultimo"], 1)
            self.assertTrue(d["quadros"][0]["hex"].startswith("48"))   # 'H'
            req = urllib.request.Request(f"http://127.0.0.1:{porta}/telecomando", data=b'{"mascara": 1}',
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=2) as r:
                self.assertEqual(json.load(r), {"status": 0})
            self.assertTrue(e.missao)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
