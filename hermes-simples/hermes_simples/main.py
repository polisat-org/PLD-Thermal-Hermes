"""Entrada do programa: python3 -m hermes_simples.main"""
from __future__ import annotations

import argparse
import math
import time
from dataclasses import replace

from .aircraft import AircraftList, JsonSource
from .config import Config
from .decoder import Decoder
from .gpio import Gpio
from .mission import Mission
from .mission_log import MissionLog
from .tcs_link import TcsLeitor
from .uart import UartSender


class SimulatedSource:
    """Gera avioes falsos para teste de bancada (sem SDR)."""

    def __init__(self, aircraft: AircraftList, count: int = 3) -> None:
        self.aircraft = aircraft
        self.count = count
        self.t0 = time.monotonic()
        self.total_messages = 0
        self.errors = 0

    def poll_once(self) -> int:
        elapsed = time.monotonic() - self.t0
        stamp = self.aircraft.met_s()
        for i in range(self.count):
            self.aircraft.update(
                0xA00000 + i,
                {
                    "lat": -23.55 + 0.01 * math.sin(elapsed / 10 + i),
                    "lon": -46.63 + 0.01 * math.cos(elapsed / 10 + i),
                    "alt_baro": 10000 + i * 500,
                    "gs": 180 + i * 10,
                    "track": (elapsed * 5 + i * 30) % 360,
                    "flight": f"TEST{i}",
                    "seen": 0.0,
                },
                met_s=stamp,
            )
        self.total_messages += self.count
        return self.count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HERMES Simples")
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=None)
    ap.add_argument("--sim", action="store_true", help="gera avioes falsos, sem decoder")
    ap.add_argument("--sim-aircraft", type=int, default=3,
                    help="quantidade de avioes no --sim (1..20)")
    ap.add_argument("--force-on", action="store_true", help="com --sim: liga a missao automaticamente")
    ap.add_argument("--no-uart", action="store_true", help="nao abre a serial")
    ap.add_argument("--duration", type=float, default=None, help="segundos (teste)")
    ap.add_argument("--log", default=None, help="caminho do CSV de log da missao")
    ap.add_argument("--no-log", action="store_true", help="desliga o log CSV local")
    args = ap.parse_args(argv)

    cfg = Config()
    overrides = {}
    if args.port:
        overrides["UART_PORT"] = args.port
    if args.baud:
        overrides["UART_BAUD"] = args.baud
    if args.log:
        overrides["MISSION_LOG_PATH"] = args.log
    if overrides:
        cfg = replace(cfg, **overrides)

    clock = time.monotonic
    gpio = Gpio(cfg, simulate=args.sim)
    uart = UartSender(cfg.UART_PORT, cfg.UART_BAUD)
    if not args.no_uart:
        uart.open()

    aircraft = AircraftList(
        cfg.NUM_AIRCRAFT,
        max_age_s=cfg.STALE_MAX_AGE_S,
        max_missing_polls=cfg.MAX_MISSING_POLLS,
        clock=clock,
    )
    sim_count = max(1, min(cfg.NUM_AIRCRAFT, args.sim_aircraft))
    source = (SimulatedSource(aircraft, sim_count) if args.sim
              else JsonSource(cfg.JSON_URL, aircraft))
    decoder = Decoder(cfg, enabled=not args.sim)
    mission_log = MissionLog(cfg.MISSION_LOG_PATH, enabled=not args.no_log)

    mission = Mission(
        cfg, gpio, uart, aircraft, source, decoder,
        mission_log=mission_log,
        clock=clock,
        tcs=TcsLeitor(cfg.TCS_LOGS) if cfg.TCS_TRAILER else None,
    )
    print(f"HERMES Simples: UART={cfg.UART_PORT}@{cfg.UART_BAUD} "
          f"envio={cfg.SEND_PERIOD_S}s sim={args.sim} avioes={sim_count}", flush=True)
    if mission_log.enabled:
        print(f"log de missao: {cfg.MISSION_LOG_PATH}", flush=True)
    if args.sim and args.force_on:
        mission.start()   # simulacao: comeca ja, sem esperar o comando do OBC
    try:
        mission.run(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        mission.stop()
        uart.close()
        gpio.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
