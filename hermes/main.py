"""HERMES payload entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from dataclasses import replace
from pathlib import Path

from hermes import systemd
from hermes.adsb.decoder_process import DecoderProcess
from hermes.adsb.raw_feed import RawFeedLogger
from hermes.adsb.source import JsonSource, SimulatedSource
from hermes.adsb.table import AircraftTable
from hermes.config import CONFIG, Config
from hermes.gpio_control import GpioController, MockGpio, PigpioGpio
from hermes.mission import MissionManager
from hermes.protocol.frames import FrameType
from hermes.protocol.payloads import (
    TEMP_INVALID,
    EventCode,
    Housekeeping,
    encode_ack,
    encode_event,
    encode_hk,
    encode_thermal,
)
from hermes.protocol.planes import pack_planes
from hermes.pwm import MockPwm, PigpioPwm
from hermes.thermal.controller import ThermalController
from hermes.thermal.sensors import SimulatedSensors, ThermalSensors
from hermes.timebase import Timebase
from hermes.uart import NullUart, UartLink

log = logging.getLogger("hermes")


class _ReadyDecoder:
    """Pretend the decoder is healthy during simulation."""

    manage = False

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True

    def is_json_alive(self) -> bool:
        return True


def _read_cpu_temp_x100() -> int:
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return int(int(raw) / 10)
    except (OSError, ValueError):
        return TEMP_INVALID


def _setup_logging(config: Config) -> None:
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)
    file_handler = logging.FileHandler(Path(config.LOG_DIR) / "hermes.log")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


class PayloadApp:
    def __init__(
        self,
        config: Config = CONFIG,
        simulate: bool = False,
        no_decoder: bool = False,
        no_uart: bool = False,
        no_raw: bool = False,
        auto_on: bool = False,
    ) -> None:
        self.config = config
        self.simulate = simulate
        self.no_decoder = no_decoder
        self.no_uart = no_uart
        self.no_raw = no_raw
        self.auto_on = auto_on
        self._stop = threading.Event()
        self._event_index = 0

        if simulate:
            self._gpio_backend = MockGpio()
            self._pwm_backend = MockPwm()
            self.sensors = SimulatedSensors()
        else:
            self._gpio_backend = PigpioGpio()
            self._pwm_backend = PigpioPwm(
                dict(config.PWM_PINS), config.PWM_FREQ_HZ, config.PWM_ACTIVE_HIGH
            )
            self.sensors = ThermalSensors(dict(config.SENSOR_MAP), fail_limit=config.SENSOR_FAIL_LIMIT)

        self.gpio = GpioController(
            self._gpio_backend,
            mission_on_pin=config.MISSION_ON_GPIO,
            mission_off_pin=config.MISSION_OFF_GPIO,
            sdr_power_pin=config.SDR_POWER_GPIO,
            active_high=config.INPUT_ACTIVE_HIGH,
            debounce_s=config.INPUT_DEBOUNCE_MS / 1000.0,
        )
        self.timebase = Timebase()
        self.table = AircraftTable(
            capacity=config.N_AIRCRAFT,
            ttl_s=config.AIRCRAFT_TTL_S,
            position_max_age_s=config.POSITION_MAX_AGE_S,
        )

        if simulate:
            self.decoder = _ReadyDecoder()
        else:
            self.decoder = DecoderProcess(
                config.DECODER_BIN,
                config.DECODER_ARGS,
                config.DECODER_JSON_URL,
                start_timeout_s=config.DECODER_START_TIMEOUT_S,
                restart_backoff_s=config.DECODER_RESTART_BACKOFF_S,
                manage=config.DECODER_MANAGE and not no_decoder,
            )

        if simulate:
            self.source = SimulatedSource(self.table, self.timebase.met_ms)
        else:
            self.source = JsonSource(
                config.DECODER_JSON_URL,
                self.table,
                self.timebase.met_ms,
                config.JSON_POLL_S,
            )

        self.raw = RawFeedLogger(
            config.RAW_FEED_HOST,
            config.RAW_FEED_PORT,
            config.RAW_LOG_DIR,
            self.timebase.met_ms,
        )
        self.thermal = ThermalController(config, self.sensors, self._pwm_backend, self.timebase.met_ms)
        self.mission = MissionManager(config, self.timebase, self.gpio, self.decoder)

        if simulate or no_uart:
            self.uart = NullUart()
        else:
            self.uart = UartLink(
                config.UART_PORT,
                config.UART_BAUD,
                config.UART_RX_ENABLED,
                config.UART_WRITE_TIMEOUT_S,
                config.UART_TX_QUEUE_SIZE,
            )

    def start(self) -> None:
        if self.auto_on and hasattr(self._gpio_backend, "inputs"):
            self._gpio_backend.inputs[self.config.MISSION_ON_GPIO] = 1
        if not self.simulate and not self.no_uart:
            try:
                self.uart.start()
            except Exception as exc:
                log.error("UART unavailable (%s); continuing without telemetry", exc)
                self.uart = NullUart()
        self.source.start()
        if not self.simulate and not self.no_raw and self.config.RAW_LOG_ENABLED:
            self.raw.start()
        self.thermal.start()
        systemd.ready()
        log.info(
            "payload ready: simulate=%s decoder=%s uart=%s",
            self.simulate,
            not self.no_decoder,
            not isinstance(self.uart, NullUart),
        )

    def run(self) -> None:
        next_adsb = 0.0
        next_hk = 0.0
        next_thermal = 0.0
        next_watchdog = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            self.mission.tick()
            self.thermal.set_enabled(
                self.config.THERMAL_ALWAYS_ON or self.mission.mission_active
            )
            self._handle_rx()
            self._emit_mission_events()

            if now >= next_adsb:
                self._send_adsb()
                next_adsb = now + 1.0
            if now >= next_hk:
                self._send_hk()
                next_hk = now + self.config.HK_PERIOD_S
            if now >= next_thermal:
                self._send_thermal()
                next_thermal = now + 1.0
            if now >= next_watchdog:
                systemd.watchdog()
                next_watchdog = now + 1.0
            self._stop.wait(self.config.MISSION_POLL_S)

    def stop(self) -> None:
        systemd.stopping()
        try:
            self.mission.stop()
        finally:
            try:
                self.thermal.stop()
            finally:
                try:
                    self.source.stop()
                finally:
                    try:
                        self.raw.stop()
                    finally:
                        try:
                            self.uart.stop()
                        finally:
                            self.gpio.close()

    def _handle_rx(self) -> None:
        for frame in self.uart.poll_rx():
            if frame.type == FrameType.TIME_SYNC:
                self.timebase.apply_time_sync(frame.met_ms)
                self.uart.send(
                    FrameType.ACK,
                    self.timebase.met_ms(),
                    encode_ack(frame.seq, int(FrameType.TIME_SYNC), 0),
                )

    def _emit_mission_events(self) -> None:
        pending = self.mission.events[self._event_index :]
        self._event_index = len(self.mission.events)
        codes = {
            "POWERING": EventCode.MISSION_ON,
            "STOPPING": EventCode.MISSION_OFF,
            "FAULT": EventCode.DECODER_FAULT,
        }
        for name in pending:
            code = codes.get(name, 0)
            if code:
                self.uart.send(
                    FrameType.EVENT,
                    self.timebase.met_ms(),
                    encode_event(code, name),
                )

    def _send_adsb(self) -> None:
        if not (self.mission.mission_active or self.config.SEND_ADSB_WHEN_OFF):
            return
        met = self.timebase.met_ms()
        planes = self.table.snapshot(met)
        payload = pack_planes(planes)
        self.uart.send(FrameType.ADSB, met, payload, count=len(planes))

    def _send_hk(self) -> None:
        if not self.mission.mission_active:
            return
        hk = Housekeeping(
            frames_tx=getattr(self.uart, "frames_tx", 0),
            frames_rx=getattr(self.uart, "frames_rx", 0),
            crc_errors=getattr(self.uart, "crc_errors", 0),
            adsb_updates=self.table.updates,
            raw_messages=getattr(self.raw, "messages", 0),
            cpu_temp_x100=_read_cpu_temp_x100(),
            sdr_on=int(self.mission.sdr_on),
            decoder_ok=int(self.decoder.is_alive()),
            mission_state=int(self.mission.state),
        )
        self.uart.send(FrameType.HK, self.timebase.met_ms(), encode_hk(hk))

    def _send_thermal(self) -> None:
        if not (self.mission.mission_active or self.config.THERMAL_ALWAYS_ON):
            return
        sample = self.thermal.snapshot()
        self.uart.send(FrameType.THERMAL, self.timebase.met_ms(), encode_thermal(sample))

    def request_stop(self) -> None:
        self._stop.set()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HERMES payload")
    parser.add_argument("--simulate", action="store_true", help="use mock GPIO/PWM/sensors")
    parser.add_argument("--no-decoder", action="store_true", help="do not spawn dump1090")
    parser.add_argument("--no-uart", action="store_true", help="do not open the OBC UART")
    parser.add_argument("--no-raw", action="store_true", help="disable raw AVR logging")
    parser.add_argument("--duration", type=float, default=None, help="override mission duration (s)")
    parser.add_argument("--auto-on", action="store_true", help="press MISSION_ON at boot (simulation)")
    parser.add_argument("--log-level", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = CONFIG
    if args.duration is not None:
        config = replace(config, MISSION_DURATION_S=args.duration)
    if args.log_level:
        config = replace(config, LOG_LEVEL=args.log_level)
    if args.simulate:
        config = replace(config, SDR_BOOT_DELAY_S=0.2, SDR_MIN_OFF_TIME_S=0.2)
    _setup_logging(config)

    try:
        app = PayloadApp(
            config,
            simulate=args.simulate,
            no_decoder=args.no_decoder,
            no_uart=args.no_uart,
            no_raw=args.no_raw,
            auto_on=args.auto_on,
        )
    except Exception:
        log.exception("failed to initialize payload")
        return 1

    def _signal(_signum, _frame):
        app.request_stop()

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)

    try:
        app.start()
        app.run()
    except Exception:
        log.exception("fatal error")
        return 1
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
