"""UART link to the OBC with a non-blocking TX queue."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Protocol

from .protocol.frames import Frame, FrameParser, encode_frame

log = logging.getLogger(__name__)


class UartBackend(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def send(self, frame_type: int, met_ms: int, payload: bytes, flags: int, count: int) -> int | None: ...
    def poll_rx(self) -> list[Frame]: ...


class UartLink:
    def __init__(
        self,
        port: str = "/dev/serial0",
        baud: int = 115200,
        rx_enabled: bool = True,
        write_timeout_s: float = 0.5,
        queue_size: int = 4,
    ) -> None:
        self.port = port
        self.baud = baud
        self.rx_enabled = rx_enabled
        self.write_timeout_s = write_timeout_s
        self.queue_size = max(1, queue_size)
        self._serial = None
        self._parser = FrameParser()
        self._queue: queue.Queue[bytes | None] = queue.Queue(self.queue_size)
        self._writer: threading.Thread | None = None
        self._stop = threading.Event()
        self._seq = 0
        self.frames_tx = 0
        self.frames_rx = 0
        self.crc_errors = 0
        self.tx_dropped = 0
        self.errors = 0

    def start(self) -> None:
        import serial  # type: ignore

        self._serial = serial.Serial(
            self.port,
            self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
            write_timeout=self.write_timeout_s,
        )
        self._stop.clear()
        self._writer = threading.Thread(target=self._write_loop, name="uart-tx", daemon=True)
        self._writer.start()
        log.info("UART open on %s at %d", self.port, self.baud)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._writer:
            self._writer.join(timeout=2.0)
            self._writer = None
        if self._serial:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def send(
        self,
        frame_type: int,
        met_ms: int,
        payload: bytes = b"",
        flags: int = 0,
        count: int = 0,
    ) -> int | None:
        if self._serial is None:
            return None
        seq = self.next_seq()
        data = encode_frame(frame_type, seq, met_ms, payload, flags=flags, count=count)
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self.tx_dropped += 1
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(data)
            except queue.Full:
                self.tx_dropped += 1
                return None
        return seq

    def poll_rx(self) -> list[Frame]:
        if self._serial is None or not self.rx_enabled:
            return []
        try:
            waiting = getattr(self._serial, "in_waiting", 0) or 0
            data = self._serial.read(waiting) if waiting else b""
        except Exception:
            self.errors += 1
            return []
        if not data:
            return []
        frames = self._parser.feed(data)
        self.frames_rx += len(frames)
        self.crc_errors = self._parser.crc_errors
        return frames

    def _write_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if data is None:
                continue
            try:
                if self._serial:
                    self._serial.write(data)
                    self.frames_tx += 1
            except Exception:
                self.errors += 1
                log.exception("UART write failed")


class NullUart:
    """No-op link used by simulation and tests."""

    def __init__(self) -> None:
        self.frames_tx = 0
        self.frames_rx = 0
        self.crc_errors = 0
        self.tx_dropped = 0
        self.errors = 0
        self.sent: list[tuple[int, int, bytes]] = []
        self._seq = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def send(self, frame_type, met_ms, payload=b"", flags=0, count=0):
        self._seq = (self._seq + 1) & 0xFFFF
        self.sent.append((int(frame_type), int(met_ms), bytes(payload)))
        self.frames_tx += 1
        return self._seq

    def poll_rx(self) -> list[Frame]:
        return []
