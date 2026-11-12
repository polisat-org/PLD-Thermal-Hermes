"""Record the AVR raw stream (TCP 30002) with mission timestamps.

The raw stream is not used for decoding: readsb already validates and decodes
frames.  It is kept as ground-truth evidence for V&V and for replay.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path


def avr_icao(line: str) -> str | None:
    text = line.strip()
    if not text.startswith("*") or not text.endswith(";"):
        return None
    payload = text[1:-1]
    if len(payload) not in (14, 28):
        return None
    try:
        int(payload, 16)
    except ValueError:
        return None
    return payload[2:8].upper()


class RawFeedLogger:
    def __init__(self, host: str, port: int, log_dir: str, clock_ms) -> None:
        self.host = host
        self.port = port
        self.log_dir = Path(log_dir)
        self._clock_ms = clock_ms
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.messages = 0
        self.errors = 0
        self._file = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / time.strftime("avr-%Y%m%d-%H%M%S.jsonl")
        self._file = path.open("a", encoding="utf-8")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="adsb-raw", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        with self._lock:
            if self._file:
                self._file.flush()
                self._file.close()
                self._file = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=2.0) as sock:
                    sock.settimeout(1.0)
                    buffer = b""
                    while not self._stop.is_set():
                        try:
                            chunk = sock.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            self._handle(line.decode("ascii", "ignore"))
            except OSError:
                self.errors += 1
                self._stop.wait(2.0)

    def _handle(self, line: str) -> None:
        icao = avr_icao(line)
        if icao is None:
            return
        record = {"met_ms": self._clock_ms(), "icao": icao, "raw": line.strip()}
        with self._lock:
            if self._file:
                self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
                self._file.flush()
            self.messages += 1
