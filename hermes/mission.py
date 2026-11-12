"""Mission state machine driven by the ON/OFF inputs."""

from __future__ import annotations

import logging
import time
from enum import IntEnum

log = logging.getLogger(__name__)


class MissionState(IntEnum):
    OFF = 0
    POWERING = 1
    STARTING = 2
    RUNNING = 3
    STOPPING = 4
    FAULT = 5


class MissionManager:
    def __init__(self, config, timebase, gpio, decoder, clock=time.monotonic) -> None:
        self.config = config
        self.timebase = timebase
        self.gpio = gpio
        self.decoder = decoder
        self._clock = clock
        self.state = MissionState.OFF
        self._state_since = clock()
        self._mission_started = 0.0
        self.events: list[str] = []

    def _set_state(self, state: MissionState) -> None:
        if state == self.state:
            return
        self.state = state
        self._state_since = self._clock()
        log.info("mission state -> %s", state.name)
        self.events.append(state.name)

    def tick(self) -> MissionState:
        now = self._clock()
        for event in self.gpio.poll_events():
            if event == "off":
                self._begin_stop("off input")
            elif event == "on" and self.state in (MissionState.OFF, MissionState.FAULT, MissionState.STOPPING):
                self._begin_start()

        if self.state == MissionState.POWERING:
            if now - self._state_since >= self.config.SDR_BOOT_DELAY_S:
                self._set_state(MissionState.STARTING)

        elif self.state == MissionState.STARTING:
            if self.decoder.start():
                self._set_state(MissionState.RUNNING)
            else:
                self._enter_fault("decoder did not start")

        elif self.state == MissionState.RUNNING:
            if not self.decoder.is_alive():
                self._enter_fault("decoder stopped")
            elif self.config.MISSION_DURATION_S > 0 and (
                now - self._mission_started >= self.config.MISSION_DURATION_S
            ):
                self._begin_stop("mission duration elapsed")

        elif self.state == MissionState.FAULT:
            if now - self._state_since >= self.config.DECODER_RESTART_BACKOFF_S:
                log.info("retrying SDR/decoder")
                self.gpio.set_sdr(True)
                self._set_state(MissionState.POWERING)

        elif self.state == MissionState.STOPPING:
            if now - self._state_since >= self.config.SDR_MIN_OFF_TIME_S:
                self._set_state(MissionState.OFF)

        return self.state

    def _begin_start(self) -> None:
        if not self.timebase.mission_active:
            self.timebase.start_mission()
            self._mission_started = self._clock()
        log.info("mission start")
        self.gpio.set_sdr(True)
        self._set_state(MissionState.POWERING)

    def _begin_stop(self, reason: str) -> None:
        if self.state in (MissionState.OFF, MissionState.STOPPING):
            return
        log.info("mission stop: %s", reason)
        try:
            self.decoder.stop()
        except Exception:  # pragma: no cover
            log.exception("decoder stop failed")
        self.gpio.set_sdr(False)
        self.timebase.stop_mission()
        self._set_state(MissionState.STOPPING)

    def _enter_fault(self, reason: str) -> None:
        log.warning("mission fault: %s", reason)
        try:
            self.decoder.stop()
        except Exception:  # pragma: no cover
            log.exception("decoder stop failed")
        self.gpio.set_sdr(False)
        self._set_state(MissionState.FAULT)

    def stop(self) -> None:
        try:
            self.decoder.stop()
        except Exception:  # pragma: no cover
            log.exception("decoder stop failed")
        try:
            self.gpio.set_sdr(False)
        finally:
            self.timebase.stop_mission()
            self._set_state(MissionState.OFF)

    @property
    def sdr_on(self) -> bool:
        return self.gpio.sdr_on

    @property
    def mission_active(self) -> bool:
        return self.state in (
            MissionState.POWERING,
            MissionState.STARTING,
            MissionState.RUNNING,
            MissionState.FAULT,
        )
