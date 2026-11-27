"""Missao minima: OFF -> RUNNING -> OFF. Sem TIME_SYNC, HK, EVENT nem ACK.

O log CSV e apenas local (arquivo), nunca vai na UART.
"""
from __future__ import annotations

import logging
import time

from .plane import pack_planes

log = logging.getLogger(__name__)


class Mission:
    def __init__(self, cfg, gpio, uart, aircraft, source, decoder,
                 mission_log=None, clock=None, sleep=None, tcs=None) -> None:
        self.cfg = cfg
        self.gpio = gpio
        self.uart = uart
        self.aircraft = aircraft
        self.source = source
        self.decoder = decoder
        self.mission_log = mission_log
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.active = False
        self.wanted = False   # ultimo comando do OBC foi LIGAR (a missao deve estar rodando)
        self._decoder_caiu = False
        self.sends = 0
        # Com o TCS (controle termico das baterias), o bloco sai a cada envio mesmo com a missao
        # desligada (avioes zerados) e leva no fim o trailer termico de 32 B (tcs_link.py).
        self.tcs = tcs

    # ------------------------------------------------------------- helpers
    def _met_s(self) -> float:
        met_s = getattr(self.aircraft, "met_s", None)
        return float(met_s()) if met_s is not None else 0.0

    def _aircraft_count(self) -> int:
        try:
            return len(self.aircraft)
        except TypeError:
            return sum(1 for slot in self.aircraft.snapshot() if slot["icaoID"] != 0)

    def _source_int(self, name: str) -> int:
        try:
            return int(getattr(self.source, name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _log(self, event: str = "send") -> None:
        if self.mission_log is None:
            return
        self.mission_log.log(
            met_s=self._met_s(),
            aircraft_count=self._aircraft_count(),
            messages_total=self._source_int("total_messages"),
            decoder_errors=self._source_int("errors"),
            buffers_sent=self.sends,
            event=event,
        )

    # ------------------------------------------------------------- missao
    def start(self) -> bool:
        if not self.gpio.set_sdr(True):
            log.warning("missao nao iniciou: saida do SDR nao ligou (tempo minimo desligado?)")
            return False
        ready = self.decoder.start()
        if ready is False:
            # dump1090 nao ficou pronto: nao finge que a missao comecou.
            self.decoder.stop()
            self.gpio.set_sdr(False)
            log.error("missao nao iniciou: decodificador ADS-B nao ficou pronto; nova tentativa em %.0f s",
                      self.cfg.START_RETRY_S)
            return False
        self.aircraft.clear()
        self.aircraft.set_mission_start()
        self.sends = 0
        if self.mission_log is not None:
            self.mission_log.start()
        self.active = True
        log.info("missao iniciada")
        return True

    def stop(self) -> None:
        was_active = self.active
        self.decoder.stop()
        self.gpio.set_sdr(False)
        if was_active and self.mission_log is not None:
            self.mission_log.finish(
                met_s=self._met_s(),
                messages_total=self._source_int("total_messages"),
                decoder_errors=self._source_int("errors"),
                buffers_sent=self.sends,
            )
        self.aircraft.clear()
        self.active = False
        if was_active:
            log.info("missao parada")

    def run(self, duration: float | None = None) -> None:
        t0 = self.clock()
        next_poll = 0.0
        next_send = 0.0
        next_retry = 0.0
        next_check = 0.0
        while duration is None or (self.clock() - t0) < duration:
            # Comandos do OBC pela UART (A5 5A cmd ~cmd); o ultimo do lote vale.
            read_commands = getattr(self.uart, "read_commands", None)
            cmds = read_commands() if read_commands is not None else []
            uart_off = bool(cmds) and cmds[-1] is False
            uart_on = bool(cmds) and cmds[-1] is True
            if uart_off:
                self.wanted = False
                if self.active:
                    self.stop()
            elif uart_on:
                self.wanted = True
                next_retry = 0.0

            now = self.clock()
            # LIGAR pedido e missao parada (inclusive se o start anterior falhou, ex.: SDR caiu do USB):
            # tenta de novo a cada START_RETRY_S, em vez de ficar parada ate o proximo comando.
            if self.wanted and not self.active and now >= next_retry:
                if self.start():
                    next_poll = 0.0
                    next_send = 0.0
                else:
                    next_retry = self.clock() + self.cfg.START_RETRY_S
                now = self.clock()
            # dump1090 morreu no meio da missao (ex.: SDR perdeu o USB): tenta reabrir.
            # O DecoderProcess limita a taxa de reinicio (restart_backoff_s).
            is_alive = getattr(self.decoder, "is_alive", None)
            if self.active and is_alive is not None and getattr(self.decoder, "enabled", True) \
                    and now >= next_check and not is_alive():
                if not self._decoder_caiu:   # um aviso por queda; as novas tentativas ficam quietas
                    log.error("decodificador ADS-B parou no meio da missao; tentando reabrir a cada %.0f s",
                              self.cfg.START_RETRY_S)
                self._decoder_caiu = True
                if self.decoder.start():
                    log.info("decodificador ADS-B reaberto")
                    self._decoder_caiu = False
                next_check = self.clock() + self.cfg.START_RETRY_S
                now = self.clock()
            if self.active and now >= next_poll:
                self.source.poll_once()
                next_poll = now + self.cfg.JSON_POLL_S
            if (self.active or self.tcs is not None) and now >= next_send:
                planes = self.aircraft.snapshot()
                data = pack_planes(planes)
                if self.tcs is not None:
                    avioes = sum(1 for p in planes if p.get("icaoID"))
                    data += self.tcs.trailer(self.active, avioes)
                self.uart.send(data)
                if self.active:
                    self.sends += 1
                    self._log("send")
                next_send = now + self.cfg.SEND_PERIOD_S

            self.sleep(self.cfg.GPIO_POLL_S)
