"""Configuracao minima. Numeros de GPIO sao BCM (wPi entre parenteses)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # GPIO
    GPIO_SDR: int = 17      # wPi 0  - pino fisico 11 - alimentacao do SDR
    SDR_ACTIVE_HIGH: bool = True
    GPIO_POLL_S: float = 0.05
    SDR_MIN_OFF_S: float = 5.0

    # UART (dados puros, sem cabecalho): 20 x 204 = 4080 bytes
    UART_PORT: str = "/dev/serial0"
    UART_BAUD: int = 115200

    # dump1090
    JSON_URL: str = "file:///run/dump1090-fa/aircraft.json"
    JSON_POLL_S: float = 1.0
    DECODER_BIN: str = "/usr/bin/dump1090-fa"
    DECODER_ARGS: tuple[str, ...] = (
        "--device-type", "rtlsdr",
        "--gain", "-10",
        "--ppm", "0",
        "--freq", "1090000000",
        "--net",
        "--net-ro-port", "30002",
        "--write-json", "/run/dump1090-fa",
        "--quiet",
    )
    DECODER_START_TIMEOUT_S: float = 15.0
    DECODER_RESTART_BACKOFF_S: float = 5.0
    DECODER_LOG_PATH: str = os.environ.get(
        "DECODER_LOG_PATH", str(Path.home() / "hermes-simples-decoder.log")
    )

    # Lista de avioes / slots
    NUM_AIRCRAFT: int = 20
    STALE_MAX_AGE_S: float = 300.0   # seen acima disso libera o slot
    MAX_MISSING_POLLS: int = 3       # ausencias seguidas no JSON liberam o slot

    # Envio e evidencia local
    SEND_PERIOD_S: float = 5.0
    # LIGAR pedido mas o start falhou (SDR/dump1090): tenta de novo a cada START_RETRY_S
    START_RETRY_S: float = 5.0
    # Trailer termico do TCS no fim de cada bloco (4080 + 32 B, a cada 5 s, inclusive com a missao desligada).
    # O OBC (branch payload_integration) espera 4112 B. Desligar volta ao protocolo antigo de 4080 B.
    TCS_TRAILER: bool = True
    TCS_LOGS: str = os.environ.get("TCS_LOGS", str(Path.home() / "tcs" / "logs"))
    MISSION_LOG_PATH: str = os.environ.get(
        "MISSION_LOG_PATH", str(Path.home() / "hermes-simples-mission.csv")
    )
