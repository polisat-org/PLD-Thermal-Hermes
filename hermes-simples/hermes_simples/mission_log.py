"""Log CSV simples com evidencia da missao (sem voltar a ter HK na UART).

Uma linha por envio, mais uma no inicio e outra no fim da missao. Serve para
provar HLR-ADS-01/05/07/08 sem complicar o protocolo serial.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path


class MissionLog:
    FIELDS = (
        "utc",
        "met_s",
        "event",
        "aircraft_count",
        "messages_total",
        "decoder_errors",
        "buffers_sent",
    )

    def __init__(self, path: str, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.rows = 0

    def _write(self, values: dict) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(values)
        self.rows += 1

    def start(self) -> None:
        self._write({
            "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "met_s": 0.0,
            "event": "start",
            "aircraft_count": 0,
            "messages_total": 0,
            "decoder_errors": 0,
            "buffers_sent": 0,
        })

    def log(self, *, met_s: float, aircraft_count: int, messages_total: int,
            decoder_errors: int, buffers_sent: int, event: str = "send") -> None:
        self._write({
            "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "met_s": round(float(met_s), 3),
            "event": event,
            "aircraft_count": int(aircraft_count),
            "messages_total": int(messages_total),
            "decoder_errors": int(decoder_errors),
            "buffers_sent": int(buffers_sent),
        })

    def finish(self, *, met_s: float, messages_total: int,
               decoder_errors: int, buffers_sent: int) -> None:
        self.log(
            met_s=met_s,
            aircraft_count=0,
            messages_total=messages_total,
            decoder_errors=decoder_errors,
            buffers_sent=buffers_sent,
            event="stop",
        )
