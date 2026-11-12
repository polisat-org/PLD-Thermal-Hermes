"""HERMES payload configuration.

All mission-specific values live here so they can be reviewed and changed in one
place.  GPIO numbers are BCM numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Config:
    # ------------------------------------------------------------------
    # ADS-B
    # ------------------------------------------------------------------
    N_AIRCRAFT: int = 20
    AIRCRAFT_TTL_S: float = 300.0
    POSITION_MAX_AGE_S: float = 10.0
    JSON_POLL_S: float = 1.0
    DECODER_JSON_URL: str = "http://127.0.0.1:8080/data/aircraft.json"

    DECODER_MANAGE: bool = True
    DECODER_BIN: str = "/usr/bin/dump1090-fa"
    DECODER_ARGS: tuple[str, ...] = (
        "--device-type",
        "rtlsdr",
        "--gain",
        "-10",
        "--ppm",
        "0",
        "--freq",
        "1090000000",
        "--net",
        "--net-ro-port",
        "30002",
        "--net-http-port",
        "8080",
        "--quiet",
    )
    DECODER_START_TIMEOUT_S: float = 15.0
    DECODER_RESTART_BACKOFF_S: float = 5.0

    RAW_LOG_ENABLED: bool = True
    RAW_FEED_HOST: str = "127.0.0.1"
    RAW_FEED_PORT: int = 30002
    RAW_LOG_DIR: str = "logs/raw"

    # ------------------------------------------------------------------
    # Mission / GPIO
    # ------------------------------------------------------------------
    MISSION_ON_GPIO: int = 27
    MISSION_OFF_GPIO: int = 22
    SDR_POWER_GPIO: int = 17
    INPUT_ACTIVE_HIGH: bool = True
    INPUT_DEBOUNCE_MS: int = 15
    MISSION_POLL_S: float = 0.05
    SDR_BOOT_DELAY_S: float = 3.0
    SDR_MIN_OFF_TIME_S: float = 5.0
    MISSION_DURATION_S: float = 600.0
    MISSION_DEFAULT_ON: bool = False

    # ------------------------------------------------------------------
    # UART / OBC
    # ------------------------------------------------------------------
    UART_PORT: str = "/dev/serial0"
    UART_BAUD: int = 115200
    UART_RX_ENABLED: bool = True
    UART_WRITE_TIMEOUT_S: float = 0.5
    UART_TX_QUEUE_SIZE: int = 4
    HK_PERIOD_S: float = 5.0
    SEND_ADSB_WHEN_OFF: bool = False

    # ------------------------------------------------------------------
    # Thermal
    # ------------------------------------------------------------------
    W1_GPIO: int = 4
    SENSOR_MAP: Mapping[int, tuple[str, ...]] = field(
        default_factory=lambda: {1: (), 2: ()}
    )
    SENSOR_POLL_S: float = 2.0
    SENSOR_FAIL_LIMIT: int = 3
    SENSOR_MIN_C: float = -55.0
    SENSOR_MAX_C: float = 125.0

    PWM_PINS: Mapping[int, int] = field(default_factory=lambda: {1: 12, 2: 13})
    PWM_FREQ_HZ: int = 1000
    PWM_ACTIVE_HIGH: bool = True

    TEMP_START_C: float = 5.0
    TEMP_TARGET_C: float = 15.0
    TEMP_STOP_C: float = 15.0
    TEMP_CUTOFF_C: float = 45.0
    HEAT_MIN_DUTY: float = 0.05
    HEAT_MAX_DUTY: float = 1.0
    HEAT_RATE_LIMIT_PER_S: float = 0.5
    THERMAL_ALWAYS_ON: bool = True

    HEATER_SUPPLY_V: float = 13.8
    HEATER_RESISTANCE_OHM: float = 82.0
    HEATER_MAX_CURRENT_A: float = 0.5

    # ------------------------------------------------------------------
    # Logging / systemd
    # ------------------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"
    SYSTEMD_NOTIFY: bool = True


CONFIG = Config()
