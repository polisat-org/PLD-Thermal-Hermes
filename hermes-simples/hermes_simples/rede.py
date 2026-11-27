"""HERMES Simples com saida pela rede, para a bancada sem OBC e TT&C.

    python3 -m hermes_simples.rede                 # SDR real, HTTP na porta 8080
    python3 -m hermes_simples.rede --sim           # sem SDR: avioes simulados
    python3 -m hermes_simples.rede --http-porta 9000

A missao e a mesma do main.py (Mission, AircraftList, decoder, TCS, log CSV). Muda so o "outro lado":
no lugar da UART para o OBC, este modulo faz o que o OBC faz com cada bloco de 4112 B e guarda o
resultado como os bytes que o TT&C entregaria ao solo:

  - quadro 'H' = 'H' + trailer TCS de 32 B, para cada bloco com trailer valido (magic, versao, CRC).
    O main() deste modulo roda sem a parte termica (tcs=None): nao ha trailer e nao sai 'H';
  - quadro 'P' = 'P' + n + seq (u16) + tx_met_ms (u32) + mensagens (u32) + n x 36 B, a cada 5 s
    durante a missao, a partir do ultimo bloco (cabecalho igual ao da missionTask do OBC: seq dos 'P',
    instante da transmissao no relogio de missao e mensagens decodificadas na missao)
    (mesmas regras de payload_downlink.c do OBC: ICAO inteiro de 24 bits, posicao na faixa,
    NaN onde falta dado, last_pos_met_ms = (met_s - seen_pos) x 1000 em float32, indicativo de 8 B
    copiado do flight ate o primeiro byte fora de 0x20..0x7E).

Telecomando (mascara de 4 bits do solo): so o bit Mission (0x01) tem efeito aqui, como no OBC
para o payload: 1 = LIGAR, 0 = DESLIGAR.

HTTP (sem autenticacao: use so na rede local da bancada):
  GET  /quadros?desde=N  -> {"ultimo": M, "quadros": [{"seq": k, "hex": "..."}]}  (quadros com seq > N)
  POST /telecomando      <- {"mascara": 0..15}  -> {"status": 0}
  GET  /estado           -> resumo (missao, quadros, avioes)

Erros e avisos (so neste modo; nao passam pelo OBC/TT&C): GET /quadros?desde=N&ev=M tambem traz
"eventos" (seq > M: {"seq", "nivel", "texto"}, de logging do hermes_simples e da vigia de SDR/decodificador)
e "estado" (pedido, ativo, sdr_usb, decodificador, erros_json), para a ground station mostrar.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path
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
MAX_EVENTOS = 300
VIGIA_PERIODO_S = 2.0
SDR_USB_IDS = {("0bda", "2838"), ("0bda", "2832")}   # RTL2832U (RTL-SDR)
log = logging.getLogger("hermes_simples.rede")
_F32 = struct.Struct("<f")
_DOWN = struct.Struct("<IfffffI8s")   # PlaneDown_t, 36 B (payload_downlink.h do OBC)


def f32(x: float) -> float:
    """Arredonda para float32 (as contas do OBC sao em float)."""
    return _F32.unpack(_F32.pack(x))[0]


def _finito(v: float) -> bool:
    return not (math.isnan(v) or math.isinf(v))


def compactar(planes: list[dict]) -> tuple[list[bytes], dict]:
    """pld_compact() do OBC: Plane_t (204 B) -> PlaneDown_t (36 B)."""
    out: list[bytes] = []
    st = {"validos": 0, "vazios": 0, "descartados": 0, "met_max_s": -1.0}
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
        if _finito(p["met_s"]) and p["met_s"] > st["met_max_s"]:
            st["met_max_s"] = p["met_s"]
        tem_pos = _finito(lat) and _finito(lon)
        nan = float("nan")
        met, seen = p["met_s"], p["seen_pos"]
        last_ms = 0
        if tem_pos and _finito(met) and _finito(seen) and met >= 0.0 and seen >= 0.0 and seen <= met:
            last_ms = int(f32(f32(met - seen) * 1000.0))
        out.append(_DOWN.pack(
            int(icao),
            lat if tem_pos else nan,
            lon if tem_pos else nan,
            p["alt_baro"] if _finito(p["alt_baro"]) else nan,
            p["gs"] if _finito(p["gs"]) else nan,
            p["track"] if _finito(p["track"]) else nan,
            last_ms,
            indicativo(p.get("flight")),
        ))
    st["validos"] = len(out)
    return out, st


def indicativo(flight) -> bytes:
    """copia_indicativo() do OBC: ASCII 0x20..0x7E ate o primeiro byte fora da faixa; o resto 0."""
    if isinstance(flight, bytes):
        flight = flight.decode("latin-1")
    out = bytearray(8)
    for i, ch in enumerate(str(flight or "")[:8]):
        if not 0x20 <= ord(ch) <= 0x7E:
            break
        out[i] = ord(ch)
    return bytes(out)


def quadro_p(registros: list[bytes], seq: int = 0, tx_met_ms: int = 0, mensagens: int = 0) -> bytes:
    """pld_build_frame() do OBC: 'P' n seq tx_met_ms mensagens + registros."""
    return (b"P" + struct.pack("<BHII", len(registros), seq & 0xFFFF, tx_met_ms & 0xFFFFFFFF, mensagens & 0xFFFFFFFF)
            + b"".join(registros))


class ContadorMensagens:
    """pld_msgs_update() do OBC: soma o aumento do contador "messages" do dump1090 de cada ICAO.
    Tabela de 64 ICAOs, substituicao circular (igual ao C, para os numeros baterem)."""

    TABELA = 64

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.icao: list[int] = []
        self.msgs: list[int] = []
        self.proximo = 0
        self.total = 0

    def update(self, planes: list[dict]) -> int:
        for p in planes:
            icao = p["icaoID"]
            if icao == 0.0 or not (_finito(icao) and 1.0 <= icao <= ICAO_MAX and float(int(icao)) == icao):
                continue
            m = p.get("messages", float("nan"))
            if not _finito(m) or m < 0.0 or m > 4.0e9:
                continue
            agora, icao = int(m), int(icao)
            if icao in self.icao:
                j = self.icao.index(icao)
                self.total += agora - self.msgs[j] if agora >= self.msgs[j] else agora
                self.msgs[j] = agora
            else:
                if len(self.icao) < self.TABELA:
                    self.icao.append(icao)
                    self.msgs.append(agora)
                else:
                    j = self.proximo
                    self.proximo = (self.proximo + 1) % self.TABELA
                    self.icao[j], self.msgs[j] = icao, agora
                self.total += agora
        self.total &= 0xFFFFFFFF
        return self.total


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
        self._tem_dados = False
        self._seq_p = 0
        self._mensagens = ContadorMensagens()
        self._met_bloco = -1.0            # maior met_s do ultimo bloco
        self._t_bloco = 0.0               # quando o bloco chegou (relogio local)
        self._ultimo_downlink = 0.0
        self.frames = 0
        self.bytes_sent = 0
        self.tcs_ruins = 0
        self.ultimo_stats: dict = {}
        self._eventos: deque[tuple[int, str, str]] = deque(maxlen=MAX_EVENTOS)
        self._ev_seq = 0
        self.saude: dict = {}        # preenchido pela vigia
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
                planes = unpack_planes(blocos)
                self._registros, self.ultimo_stats = compactar(planes)
                self._met_bloco = self.ultimo_stats["met_max_s"]
                self._t_bloco = self.relogio()
                self._mensagens.update(planes)
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
                self._mensagens.reset()
                self._met_bloco = -1.0
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
                tx = 0
                if self._met_bloco >= 0.0:   # (uint32_t)(met * 1000.0f) + tempo que segurou o bloco
                    tx = int(f32(self._met_bloco * 1000.0)) + int((agora - self._t_bloco) * 1000.0)
                self._publica(quadro_p(self._registros, self._seq_p, tx, self._mensagens.total))
                self._seq_p = (self._seq_p + 1) & 0xFFFF
                self._ultimo_downlink = agora

    def _downlink_loop(self) -> None:
        while not self.parar.wait(0.1):
            self.passo()

    # ---- erros e avisos para o solo
    def evento(self, nivel: str, texto: str) -> None:
        with self._lock:
            self._ev_seq += 1
            self._eventos.append((self._ev_seq, nivel, texto))

    def eventos_desde(self, seq: int) -> tuple[int, list[dict]]:
        with self._lock:
            if seq > self._ev_seq:
                seq = 0   # a ponte veio de uma execucao anterior: manda tudo o que tem
            return self._ev_seq, [{"seq": k, "nivel": n, "texto": t} for k, n, t in self._eventos if k > seq]

    def _publica(self, quadro: bytes) -> None:   # chamar com o lock
        self._seq += 1
        self._quadros.append((self._seq, quadro))


class _ParaEnlace(logging.Handler):
    """Copia os logs do hermes_simples (INFO ou mais) para os eventos que a ground station busca."""

    NIVEIS = {logging.INFO: "info", logging.WARNING: "aviso", logging.ERROR: "erro", logging.CRITICAL: "erro"}

    def __init__(self, enlace: EnlaceRede) -> None:
        super().__init__(logging.INFO)
        self.enlace = enlace

    def emit(self, record: logging.LogRecord) -> None:
        try:
            texto = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                texto += f" ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
            self.enlace.evento(self.NIVEIS.get(record.levelno, "info"), texto)
        except Exception:  # noqa: BLE001 - log nunca derruba a missao
            pass


def sdr_no_usb(base: Path = Path("/sys/bus/usb/devices")) -> bool:
    for d in base.glob("*"):
        try:
            ids = ((d / "idVendor").read_text().strip(), (d / "idProduct").read_text().strip())
        except OSError:
            continue
        if ids in SDR_USB_IDS:
            try:   # listado mas desautorizado (desconectado pelo kernel) nao serve
                return (d / "authorized").read_text().strip() != "0"
            except OSError:
                return True
    return False


def vigia(enlace: EnlaceRede, mission: Mission, decoder: Decoder, source, simulado: bool,
          parar: threading.Event, checa_usb=sdr_no_usb) -> None:
    """A cada 2 s: SDR no USB, decodificador vivo, leitura do aircraft.json. Avisa nas mudancas."""
    antes: dict = {}
    erros_antes = int(getattr(source, "errors", 0) or 0)
    while True:
        usb = None if simulado else checa_usb()
        vivo = None if simulado or not mission.active else decoder.is_alive()
        erros = int(getattr(source, "errors", 0) or 0)
        json_ok = None if not mission.active else erros == erros_antes
        erros_antes = erros
        agora = {"pedido": mission.wanted, "ativo": mission.active, "sdr_usb": usb,
                 "decodificador": vivo, "erros_json": erros}
        if usb is False and antes.get("sdr_usb") is not False:
            log.error("SDR nao aparece no USB do Pi (cabo/adaptador OTG?)")
        elif usb and antes.get("sdr_usb") is False:
            log.info("SDR de volta no USB")
        if json_ok is False and antes.get("_json_ok") is not False:
            log.warning("falha ao ler o aircraft.json do decodificador (%d erros ate agora)", erros)
        elif json_ok and antes.get("_json_ok") is False:
            log.info("leitura do aircraft.json normalizada")
        if mission.wanted and not mission.active and antes.get("ativo") is not False and antes.get("pedido"):
            log.warning("missao pedida pelo solo, mas parada (tentando iniciar)")
        enlace.saude = agora
        antes = {**agora, "_json_ok": json_ok}
        if parar.wait(VIGIA_PERIODO_S):
            return


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
                try:
                    ev = int(parse_qs(u.query).get("ev", ["0"])[0])
                except ValueError:
                    ev = 0
                ultimo, qs = enlace.quadros_desde(desde)
                ultimo_ev, evs = enlace.eventos_desde(ev)
                self._json(200, {"ultimo": ultimo, "quadros": [{"seq": k, "hex": q.hex()} for k, q in qs],
                                 "ultimo_evento": ultimo_ev, "eventos": evs, "estado": enlace.saude})
            elif u.path == "/estado":
                self._json(200, {"missao": enlace.missao, "blocos": enlace.frames, "tcs_ruins": enlace.tcs_ruins,
                                 "ultimo_quadro": enlace._seq, "avioes": enlace.ultimo_stats, **enlace.saude})
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
    pkg_log = logging.getLogger("hermes_simples")
    pkg_log.setLevel(logging.INFO)
    pkg_log.addHandler(_ParaEnlace(enlace))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    pkg_log.addHandler(console)   # tambem no journalctl
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
    threading.Thread(target=vigia, args=(enlace, mission, decoder, source, args.sim, enlace.parar),
                     daemon=True).start()
    log.info("HERMES Simples (rede) no ar%s", " com avioes simulados" if args.sim else "")
    print(f"HERMES Simples (rede): http://0.0.0.0:{args.http_porta}  envio={cfg.SEND_PERIOD_S}s sim={args.sim}",
          flush=True)
    try:
        mission.run(args.duration)
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("erro inesperado; o servico vai reiniciar")
        time.sleep(1.5)   # da tempo de a ponte buscar o evento
        raise
    finally:
        mission.stop()
        enlace.close()
        srv.shutdown()
        gpio.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
