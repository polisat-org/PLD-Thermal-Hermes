import json
import struct
import time
import unittest
import urllib.request

from hermes_simples.plane import BUFFER_SIZE, empty_slot, new_plane, pack_planes
import logging
import threading

from hermes_simples.rede import EnlaceRede, _ParaEnlace, servidor_http, trailer_valido, vigia
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
        self.assertEqual(len(ps[0]), 2 + 3 * 36)
        icao, lat, lon, alt, gs, trk, last_ms, cs = struct.unpack_from("<IfffffI8s", ps[0], 2)
        self.assertEqual(icao, 0xE48000)
        self.assertEqual(last_ms, 98000)
        self.assertEqual(cs, b"TAM3050\0")
        self.assertEqual(struct.unpack_from("8s", ps[0], 2 + 36 + 28)[0], b"TAM3051\0")

    def test_indicativo_igual_ao_obc(self):
        from hermes_simples.rede import indicativo
        self.assertEqual(indicativo("TAM3054 "), b"TAM3054 ")
        self.assertEqual(indicativo("GLO12\x01XY"), b"GLO12\0\0\0")
        self.assertEqual(indicativo("AB\ufffdCD"), b"AB\0\0\0\0\0\0")   # byte >= 0x80 vira U+FFFD no unpack
        self.assertEqual(indicativo(""), bytes(8))
        self.assertEqual(indicativo("ABCDEFGHIJ"), b"ABCDEFGH")

    def test_quadros_desde(self):
        for _ in range(3):
            self.e.send(bloco(0))
        ultimo, qs = self.e.quadros_desde(1)
        self.assertEqual(ultimo, 3)
        self.assertEqual([k for k, _ in qs], [2, 3])


class EventosTests(unittest.TestCase):
    def test_log_vira_evento(self):
        e = EnlaceRede()
        lg = logging.getLogger("hermes_simples.teste")
        h = _ParaEnlace(e)
        lg.addHandler(h)
        try:
            lg.error("decodificador caiu")
            lg.debug("nao vai")
        finally:
            lg.removeHandler(h)
        ultimo, evs = e.eventos_desde(0)
        self.assertEqual(ultimo, 1)
        self.assertEqual(evs, [{"seq": 1, "nivel": "erro", "texto": "decodificador caiu"}])
        self.assertEqual(e.eventos_desde(1)[1], [])
        self.assertEqual(len(e.eventos_desde(99)[1]), 1)   # ponte de outra execucao: recomeca

    def test_vigia_avisa_sdr_fora_do_usb_uma_vez(self):
        class M:
            active, wanted = True, True
        class D:
            def is_alive(self):
                return True
        class S:
            errors = 0
        e = EnlaceRede()
        h = _ParaEnlace(e)
        pkg = logging.getLogger("hermes_simples")
        nivel_antigo = pkg.level
        pkg.setLevel(logging.INFO)
        pkg.addHandler(h)
        usb = iter([True, False, False, True])
        parar = threading.Event()
        chamadas = []
        def checa():
            v = next(usb)
            chamadas.append(v)
            if len(chamadas) == 4:
                parar.set()
            return v
        try:
            import hermes_simples.rede as rede
            antigo, rede.VIGIA_PERIODO_S = rede.VIGIA_PERIODO_S, 0.001
            vigia(e, M(), D(), S(), False, parar, checa_usb=checa)
        finally:
            rede.VIGIA_PERIODO_S = antigo
            pkg.removeHandler(h)
            pkg.setLevel(nivel_antigo)
        textos = [x["texto"] for x in e.eventos_desde(0)[1]]
        self.assertEqual(sum("nao aparece no USB" in t for t in textos), 1)
        self.assertEqual(sum("de volta no USB" in t for t in textos), 1)
        self.assertTrue(e.saude["sdr_usb"])


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
