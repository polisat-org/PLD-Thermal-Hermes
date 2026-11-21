#!/usr/bin/env python3
"""Console interativa para testar a UART bruta do HERMES Simples.

Uso:
    sudo systemctl stop hermes-payload hermes-simples
    python3 teste_uart/console_uart.py --port /dev/serial0

Dentro do console, digite 'help'.
"""
from __future__ import annotations

import argparse
import shlex
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_simples.plane import (  # noqa: E402
    BUFFER_SIZE,
    PLANE_SIZE,
    new_plane,
    pack_planes,
    unpack_planes,
)


def parse_hex(parts: list[str]) -> bytes:
    text = "".join(parts).replace("0x", "")
    if len(text) % 2:
        raise ValueError("numero impar de digitos hex")
    return bytes.fromhex(text)


def sample_plane(n: int, icao: int = 0xE40000) -> dict:
    plane = new_plane(icao + n)
    plane["met_s"] = 1.0 + n * 0.5
    plane["flight"] = f"FLY{n}"
    plane["squawk"] = f"{1000 + n:04d}"
    plane["alt_baro"] = 1000.0 + n
    plane["gs"] = 200.0 + n
    plane["track"] = 270.0 + n
    plane["lat"] = -23.5 - n * 0.01
    plane["lon"] = -46.6 + n * 0.01
    plane["rssi"] = -3.5
    return plane


def sample_buffer(count: int = 3) -> bytes:
    count = max(0, min(20, count))
    return pack_planes([sample_plane(i) for i in range(count)])


class Console:
    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.baud = baud
        self.ser = None
        self.stop_evt = threading.Event()
        self.lock = threading.Lock()
        self.rx = bytearray()
        self.tx_bytes = 0
        self.sends = 0
        self.periodic_evt: threading.Event | None = None
        self.periodic_thread: threading.Thread | None = None
        self.capture = False

    def out(self, text: str = "") -> None:
        with self.lock:
            print(text, flush=True)

    def open_port(self) -> None:
        import serial

        if self.ser is not None:
            self.close_port()
        try:
            self.ser = serial.Serial(
                self.port, self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
                write_timeout=0.5,
            )
        except Exception as exc:
            self.out(f"erro ao abrir {self.port}: {exc}")
            self.ser = None
            return
        self.stop_evt.clear()
        threading.Thread(target=self.rx_thread, daemon=True).start()
        self.out(f"aberta {self.port} @ {self.baud} 8N1")

    def close_port(self) -> None:
        self.stop_periodic()
        self.stop_evt.set()
        if self.ser is not None:
            self.ser.close()
            self.ser = None
            self.out("porta fechada")

    def rx_thread(self) -> None:
        while not self.stop_evt.is_set():
            ser = self.ser
            if ser is None:
                time.sleep(0.05)
                continue
            try:
                waiting = ser.in_waiting or 0
                if waiting:
                    data = ser.read(waiting)
                    if data:
                        if self.capture and len(self.rx) < 65536:
                            self.rx.extend(data)
                        self.out("RX  " + data.hex(" ").upper())
                else:
                    time.sleep(0.01)
            except Exception as exc:
                self.out(f"erro RX: {exc}")
                time.sleep(0.1)

    def tx(self, data: bytes) -> None:
        if self.ser is None:
            self.out("porta nao aberta (digite 'open')")
            return
        try:
            self.ser.write(data)
            self.tx_bytes += len(data)
            self.sends += 1
            self.out("TX  " + data[:48].hex(" ").upper() +
                     (f" ... ({len(data)} bytes)" if len(data) > 48 else ""))
        except Exception as exc:
            self.out(f"erro TX: {exc}")

    def stop_periodic(self) -> None:
        if self.periodic_evt is not None:
            self.periodic_evt.set()
            if self.periodic_thread is not None:
                self.periodic_thread.join(timeout=1.0)
            self.periodic_evt = None
            self.periodic_thread = None
            self.out("envio automatico parado")

    def start_periodic(self, period: float, factory) -> None:
        self.stop_periodic()
        evt = threading.Event()
        self.periodic_evt = evt

        def worker() -> None:
            while not evt.is_set():
                try:
                    factory()
                except Exception as exc:
                    self.out(f"erro periodico: {exc}")
                evt.wait(max(0.01, period))

        self.periodic_thread = threading.Thread(target=worker, daemon=True)
        self.periodic_thread.start()
        self.out(f"envio automatico iniciado ({period:g}s)")

    def help(self) -> None:
        self.out("""
COMANDOS
  help / quit                    ajuda / sair
  port CAMINHO / baud N          configurar porta e velocidade
  open / close                   abrir / fechar a serial

BYTES BRUTOS
  byte XX                        envia UM byte
  send XX XX ...                 envia bytes escolhidos
  text "oi"                      envia ASCII
  burst N [HEX]                  envia exatamente N bytes e para
  loop N PERIODO [HEX]           repete N bytes fixos a cada PERIODO
                                 -> testa STM32 que le N bytes fixos

PROTOCOLO HERMES SIMPLES
  plane N                        envia 1 Plane_t (204 bytes) de exemplo
  buffer [N]                     envia buffer cheio (4080 bytes) com N avioes
  stream [N] PERIODO             envia buffer cheio repetidamente
  stop                           para TODO envio automatico (silencio)

RECEBIMENTO
  capture on|off                 guarda bytes recebidos em memoria
  rx                             mostra bytes recebidos
  planes                         decodifica os primeiros 4080 bytes capturados
  stats / clear                  contadores / limpar
""".strip())

    def handle(self, line: str) -> bool:
        line = line.strip()
        if not line or line.startswith("#"):
            return True
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            self.out(f"erro: {exc}")
            return True
        cmd = parts[0].lower()
        args = parts[1:]
        try:
            if cmd in ("quit", "exit", "q"):
                return False
            elif cmd == "help":
                self.help()
            elif cmd == "port" and args:
                self.port = args[0]
                self.out(f"porta = {self.port} (digite 'open')")
            elif cmd == "baud" and args:
                self.baud = int(args[0], 0)
                self.out(f"baud = {self.baud} (digite 'open')")
            elif cmd == "open":
                self.open_port()
            elif cmd == "close":
                self.close_port()
            elif cmd == "byte" and len(args) == 1:
                self.tx(parse_hex([args[0]]))
            elif cmd == "send" and args:
                self.tx(parse_hex(args))
            elif cmd == "text" and args:
                self.tx(" ".join(args).encode("ascii", "ignore"))
            elif cmd == "burst" and args:
                n = int(args[0], 0)
                pattern = parse_hex(args[1:]) if len(args) > 1 else b"\x55"
                if n <= 0:
                    raise ValueError("N deve ser > 0")
                self.tx((pattern * (n // len(pattern) + 1))[:n])
            elif cmd == "loop" and len(args) >= 2:
                n = int(args[0], 0)
                period = float(args[1])
                pattern = parse_hex(args[2:]) if len(args) > 2 else b"\x55"
                if n <= 0:
                    raise ValueError("N deve ser > 0")
                self.start_periodic(period, lambda: self.tx(
                    (pattern * (n // len(pattern) + 1))[:n]))
            elif cmd == "plane" and args:
                n = int(args[0], 0)
                self.tx(pack_planes([sample_plane(n)])[:PLANE_SIZE])
            elif cmd == "buffer":
                n = int(args[0], 0) if args else 3
                self.tx(sample_buffer(n))
            elif cmd == "stream" and args:
                if args[0].lower() == "stop":
                    self.stop_periodic()
                else:
                    if len(args) == 1:
                        period, n = float(args[0]), 3
                    else:
                        n, period = int(args[0], 0), float(args[1])
                    self.start_periodic(period, lambda: self.tx(sample_buffer(n)))
            elif cmd == "stop":
                self.stop_periodic()
            elif cmd == "capture" and args:
                self.capture = args[0].lower() in ("on", "1", "sim", "yes")
                self.out(f"captura {'ligada' if self.capture else 'desligada'}")
            elif cmd == "rx":
                self.out(bytes(self.rx).hex(" ").upper() or "(nada capturado)")
            elif cmd == "planes":
                if len(self.rx) < BUFFER_SIZE:
                    self.out(f"faltam bytes: {len(self.rx)}/{BUFFER_SIZE}")
                else:
                    for i, plane in enumerate(unpack_planes(bytes(self.rx[:BUFFER_SIZE]))):
                        if plane["icaoID"] == 0:
                            continue
                        self.out(
                            f"[{i:2d}] ICAO={int(plane['icaoID']):06X} "
                            f"{plane['flight']!r} met_s={plane['met_s']:7.2f} "
                            f"lat={plane['lat']:.5f} "
                            f"lon={plane['lon']:.5f} alt={plane['alt_baro']:.0f} "
                            f"gs={plane['gs']:.0f} rssi={plane['rssi']:.1f}"
                        )
            elif cmd == "stats":
                self.out(f"tx_bytes   {self.tx_bytes}")
                self.out(f"sends      {self.sends}")
                self.out(f"rx_bytes   {len(self.rx)}")
            elif cmd == "clear":
                self.rx.clear()
                self.tx_bytes = 0
                self.sends = 0
                self.out("limpo")
            else:
                self.out(f"comando desconhecido: {line!r} (digite 'help')")
        except Exception as exc:
            self.out(f"erro: {type(exc).__name__}: {exc}")
        return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="/dev/serial0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        print(f"PLANE_SIZE={PLANE_SIZE} BUFFER_SIZE={BUFFER_SIZE}")
        print("plane 0:", pack_planes([sample_plane(0)])[:PLANE_SIZE].hex(" ").upper())
        print("buffer:", sample_buffer(1).hex(" ").upper()[:120], "...")
        return 0

    console = Console(args.port, args.baud)
    console.out("Console UART HERMES Simples. Digite 'help'.")
    console.open_port()
    try:
        while True:
            try:
                line = input("uart> ")
            except (KeyboardInterrupt, EOFError):
                console.out()
                break
            if not console.handle(line):
                break
    finally:
        console.close_port()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
