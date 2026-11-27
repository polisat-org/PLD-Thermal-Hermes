"""HERMES Simples com saida pela rede, para a bancada sem OBC e TT&C.

    python3 -m hermes_simples.rede                 # SDR real, HTTP na porta 8080
    python3 -m hermes_simples.rede --sim           # sem SDR: avioes simulados
    python3 -m hermes_simples.rede --http-porta 9000

A missao e a mesma do main.py (Mission, AircraftList, decoder, TCS, log CSV). Muda so o "outro lado":
no lugar da UART para o OBC, este modulo faz o que o OBC faz com cada bloco de 4112 B e guarda o
resultado como os bytes que o TT&C entregaria ao solo:

  - quadro 'H' = 'H' + trailer TCS de 32 B, para cada bloco com trailer valido (magic, versao, CRC).
    O main() deste modulo roda sem a parte termica (tcs=None): nao ha trailer e nao sai 'H';
  - quadro 'P' = 'P' + n + n x 28 B, a cada 5 s durante a missao, a partir do ultimo bloco
    (mesmas regras de payload_downlink.c do OBC: ICAO inteiro de 24 bits, posicao na faixa,
    NaN onde falta dado, last_pos_met_ms = (met_s - seen_pos) x 1000 em float32).

Telecomando (mascara de 4 bits do solo): so o bit Mission (0x01) tem efeito aqui, como no OBC
para o payload: 1 = LIGAR, 0 = DESLIGAR.

HTTP (sem autenticacao: use so na rede local da bancada):
  GET  /quadros?desde=N  -> {"ultimo": M, "quadros": [{"seq": k, "hex": "...", "indicativos": {...}}]}
                            (quadros com seq > N). "indicativos" (ICAO hex -> callsign) so vem nos 'P':
                            o OBC descarta o flight[8] ao compactar, entao isto so existe neste modo.
  POST /telecomando      <- {"mascara": 0..15}  -> {"status": 0}
  GET  /estado           -> resumo (missao, quadros, avioes)
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import threading
import time
from collections import deque
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .aircraft import AircraftList, JsonSource
from .config import Config
from .decoder import Decoder
from .gpio import Gpio
from .main import SimulatedSource
from .mission import Mission
from .mission_log import MissionLog
from .plane import BUFFER_SIZE, unpack_planes
from .tcs_link import crc16_ccitt

TCS_SIZE = 32
DOWNLINK_PERIODO_S = 5.0        # PLD_DOWNLINK_MS do OBC
ICAO_MAX = 16777215.0
MAX_QUADROS = 500               # fila de quadros guardados para a ponte buscar
_F32 = struct.Struct("<f")
_DOWN = struct.Struct("<IfffffI")   # PlaneDown_t, 28 B


def f32(x: float) -> float:
    """Arredonda para float32 (as contas do OBC sao em float)."""
    return _F32.unpack(_F32.pack(x))[0]


def _finito(v: float) -> bool:
    return not (math.isnan(v) or math.isinf(v))


def compactar(planes: list[dict]) -> tuple[list[bytes], dict]:
    """pld_compact() do OBC: Plane_t (204 B) -> PlaneDown_t (28 B)."""
    out: list[bytes] = []
    st = {"validos": 0, "vazios": 0, "descartados": 0, "indicativos": {}}
    for p in planes:
        icao = p["icaoID"]
        if icao == 0.0:
            st["vazios"] += 1
            continue
        lat, lon = p["lat"], p["lon"]
        icao_ok = _finito(icao) and 1.0 <= icao <= ICAO_MAX and float(int(icao)) == icao
        pos_ok = not ((_finito(lat) and not -90.0 <= lat <= 90.0) or (_finito(lon) and not -180.0 <= lon <= 180.0))
        if not icao_ok or not pos_ok:
            st["descartados"] += 1
            continue
        tem_pos = _finito(lat) and _finito(lon)
        nan = float("nan")
        met, seen = p["met_s"], p["seen_pos"]
        last_ms = 0
        if tem_pos and _finito(met) and _finito(seen) and met >= 0.0 and seen >= 0.0 and seen <= met:
            last_ms = int(f32(f32(met - seen) * 1000.0))
        flight = str(p.get("flight") or "").strip()
        if flight:
            st["indicativos"][f"{int(icao):06X}"] = flight
        out.append(_DOWN.pack(
            int(icao),
            lat if tem_pos else nan,
            lon if tem_pos else nan,
            p["alt_baro"] if _finito(p["alt_baro"]) else nan,
            p["gs"] if _finito(p["gs"]) else nan,
            p["track"] if _finito(p["track"]) else nan,
            last_ms,
        ))
    st["validos"] = len(out)
    return out, st


def quadro_p(registros: list[bytes]) -> bytes:
    return b"P" + bytes([len(registros)]) + b"".join(registros)


def trailer_valido(raw: bytes) -> bool:
    """pld_tcs_decode(): magic TCS1, versao 1, CRC16-CCITT dos 30 primeiros bytes."""
    return (len(raw) == TCS_SIZE and raw[:4] == b"TCS1" and raw[4] == 1
            and crc16_ccitt(raw[:30]) == struct.unpack("<H", raw[30:32])[0])


class EnlaceRede:
    """Substitui o UartSender: mesma interface (send / read_commands / open / close)."""

    def __init__(self, relogio=time.monotonic) -> None:
        self.relogio = relogio
        self._lock = threading.Lock()
        self._quadros: deque[tuple[int, bytes]] = deque(maxlen=MAX_QUADROS)
        self._seq = 0
        self._cmds: list[bool] = []
        self.missao = False          # pedido do solo (bit Mission do ultimo telecomando)
        self._registros: list[bytes] = []
        self._extras: dict[int, dict] = {}   # seq -> campos extras do quadro na resposta HTTP
        self._tem_dados = False
        self._ultimo_downlink = 0.0
        self.frames = 0
        self.bytes_sent = 0
        self.tcs_ruins = 0
        self.ultimo_stats: dict = {}
        self.parar = threading.Event()
        self._thread = threading.Thread(target=self._downlink_loop, daemon=True)

    # ---- lado da missao (thread principal)
    def open(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def close(self) -> None:
        self.parar.set()

    def send(self, data: bytes) -> int:
        """Recebe o bloco do payload (como a USART3 do OBC) e gera os quadros."""
        self.frames += 1
        self.bytes_sent += len(data)
        blocos, trailer = data[:BUFFER_SIZE], data[BUFFER_SIZE:BUFFER_SIZE + TCS_SIZE]
        with self._lock:
            if len(blocos) == BUFFER_SIZE and self.missao:
                self._registros, self.ultimo_stats = compactar(unpack_planes(blocos))
                self._tem_dados = True
            if trailer:
                if trailer_valido(trailer):
                    self._publica(b"H" + trailer)
                else:
                    self.tcs_ruins += 1
        return len(data)

    def read_commands(self) -> list[bool]:
        with self._lock:
            cmds, self._cmds = self._cmds, []
        return cmds

    # ---- lado do solo (threads HTTP)
    def telecomando(self, mascara: int) -> int:
        if not 0 <= mascara <= 0x0F:
            return 1
        liga = bool(mascara & 0x01)
        with self._lock:
            if liga and not self.missao:
                self._tem_dados = False
                self._ultimo_downlink = self.relogio()
            self.missao = liga
            self._cmds.append(liga)
        return 0

    def quadros_desde(self, seq: int) -> tuple[int, list[tuple[int, bytes]]]:
        with self._lock:
            return self._seq, [(k, q) for k, q in self._quadros if k > seq]

    # ---- quadro 'P' a cada 5 s, como a missionTask do OBC
    def passo(self) -> None:
        with self._lock:
            agora = self.relogio()
            if self.missao and self._tem_dados and agora - self._ultimo_downlink >= DOWNLINK_PERIODO_S:
                self._publica(quadro_p(self._registros), {"indicativos": self.ultimo_stats.get("indicativos", {})})
                self._ultimo_downlink = agora

    def _downlink_loop(self) -> None:
        while not self.parar.wait(0.1):
            self.passo()

    def extras(self, seq: int) -> dict:
        with self._lock:
            return self._extras.get(seq, {})

    def _publica(self, quadro: bytes, extras: dict | None = None) -> None:   # chamar com o lock
        self._seq += 1
        self._quadros.append((self._seq, quadro))
        if extras:
            self._extras[self._seq] = extras
        primeiro = self._quadros[0][0]
        for k in [k for k in self._extras if k < primeiro]:
            del self._extras[k]


def servidor_http(enlace: EnlaceRede, porta: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path == "/quadros":
                try:
                    desde = int(parse_qs(u.query).get("desde", ["0"])[0])
                except ValueError:
                    desde = 0
                ultimo, qs = enlace.quadros_desde(desde)
                self._json(200, {"ultimo": ultimo, "quadros": [{"seq": k, "hex": q.hex(), **enlace.extras(k)} for k, q in qs]})
            elif u.path == "/estado":
                self._json(200, {"missao": enlace.missao, "blocos": enlace.frames, "tcs_ruins": enlace.tcs_ruins,
                                 "ultimo_quadro": enlace._seq, "avioes": enlace.ultimo_stats})
            else:
                self._json(404, {"erro": "rota desconhecida"})

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/telecomando":
                self._json(404, {"erro": "rota desconhecida"})
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                mascara = int(json.loads(self.rfile.read(n) or b"{}")["mascara"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                self._json(400, {"erro": 'corpo esperado: {"mascara": 0..15}'})
                return
            self._json(200, {"status": enlace.telecomando(mascara)})

        def log_message(self, fmt, *args) -> None:  # silencioso (a ponte consulta a cada 300 ms)
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", porta), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HERMES Simples com saida HTTP (bancada sem OBC/TT&C)")
    ap.add_argument("--http-porta", type=int, default=8080)
    ap.add_argument("--sim", action="store_true", help="avioes simulados, sem SDR nem decoder")
    ap.add_argument("--sim-aircraft", type=int, default=3)
    ap.add_argument("--duration", type=float, default=None, help="segundos (teste)")
    ap.add_argument("--log", default=None, help="caminho do CSV de log da missao")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args(argv)

    cfg = Config()
    if args.log:
        cfg = replace(cfg, MISSION_LOG_PATH=args.log)
    clock = time.monotonic
    gpio = Gpio(cfg, simulate=args.sim)
    enlace = EnlaceRede(clock)
    enlace.open()
    aircraft = AircraftList(cfg.NUM_AIRCRAFT, max_age_s=cfg.STALE_MAX_AGE_S,
                            max_missing_polls=cfg.MAX_MISSING_POLLS, clock=clock)
    sim_count = max(1, min(cfg.NUM_AIRCRAFT, args.sim_aircraft))
    source = SimulatedSource(aircraft, sim_count) if args.sim else JsonSource(cfg.JSON_URL, aircraft)
    decoder = Decoder(cfg, enabled=not args.sim)
    mission_log = MissionLog(cfg.MISSION_LOG_PATH, enabled=not args.no_log)
    # Sem a parte termica: nenhum trailer TCS (logo nenhum quadro 'H'); so os avioes, durante a missao.
    mission = Mission(cfg, gpio, enlace, aircraft, source, decoder, mission_log=mission_log, clock=clock, tcs=None)
    srv = servidor_http(enlace, args.http_porta)
    print(f"HERMES Simples (rede): http://0.0.0.0:{args.http_porta}  envio={cfg.SEND_PERIOD_S}s sim={args.sim}",
          flush=True)
    try:
        mission.run(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        mission.stop()
        enlace.close()
        srv.shutdown()
        gpio.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
