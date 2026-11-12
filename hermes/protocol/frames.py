"""Framing used on the RPi <-> OBC UART link."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from .crc import crc16_ccitt

SOF = b"\xAA\x55"
VERSION = 1

# SOF(2) version(1) type(1) seq(2) met_ms(4) len(2) flags(1) count(1)
HEADER = struct.Struct("<2sBBHIHBB")
HEADER_SIZE = HEADER.size
CRC_STRUCT = struct.Struct("<H")
MAX_PAYLOAD = 20 * 28  # 20 Plane_t records
MAX_FRAME = HEADER_SIZE + MAX_PAYLOAD + CRC_STRUCT.size


class FrameType(IntEnum):
    HK = 0x01
    ADSB = 0x02
    THERMAL = 0x03
    EVENT = 0x04
    TIME_SYNC = 0x05
    ACK = 0x06


@dataclass(frozen=True)
class Frame:
    version: int
    type: int
    seq: int
    met_ms: int
    flags: int
    count: int
    payload: bytes

    @property
    def crc(self) -> int:
        return crc16_ccitt(self.header_without_sof() + self.payload)

    def header_without_sof(self) -> bytes:
        return struct.pack(
            "<BBHIHBB",
            self.version,
            self.type,
            self.seq,
            self.met_ms,
            len(self.payload),
            self.flags,
            self.count,
        )

    def to_bytes(self) -> bytes:
        return encode_frame(
            self.type,
            self.seq,
            self.met_ms,
            self.payload,
            flags=self.flags,
            count=self.count,
        )


def encode_frame(
    frame_type: int | FrameType,
    seq: int,
    met_ms: int,
    payload: bytes = b"",
    flags: int = 0,
    count: int = 0,
) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload too large: {len(payload)} > {MAX_PAYLOAD}")
    header = HEADER.pack(
        SOF,
        VERSION,
        int(frame_type) & 0xFF,
        int(seq) & 0xFFFF,
        int(met_ms) & 0xFFFFFFFF,
        len(payload),
        int(flags) & 0xFF,
        int(count) & 0xFF,
    )
    body = header + payload
    crc = crc16_ccitt(body[2:])
    return body + CRC_STRUCT.pack(crc)


def decode_frame(data: bytes) -> Frame:
    if len(data) < HEADER_SIZE + CRC_STRUCT.size:
        raise ValueError("frame too short")
    sof, version, frame_type, seq, met_ms, length, flags, count = HEADER.unpack_from(data)
    if sof != SOF:
        raise ValueError("invalid SOF")
    if version != VERSION:
        raise ValueError(f"unsupported version {version}")
    if length > MAX_PAYLOAD:
        raise ValueError(f"payload length {length} exceeds maximum")
    total = HEADER_SIZE + length + CRC_STRUCT.size
    if len(data) != total:
        raise ValueError(f"frame length mismatch: expected {total}, got {len(data)}")
    payload = data[HEADER_SIZE : HEADER_SIZE + length]
    expected_crc = CRC_STRUCT.unpack_from(data, HEADER_SIZE + length)[0]
    actual_crc = crc16_ccitt(data[2 : HEADER_SIZE + length])
    if expected_crc != actual_crc:
        raise ValueError("CRC mismatch")
    return Frame(version, frame_type, seq, met_ms, flags, count, payload)


class FrameParser:
    """Incremental parser for a UART byte stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.crc_errors = 0
        self.length_errors = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            start = self._buffer.find(SOF)
            if start < 0:
                # Keep a trailing 0xAA in case SOF was split across reads.
                if self._buffer[-1:] == b"\xAA":
                    self._buffer[:] = self._buffer[-1:]
                else:
                    self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < HEADER_SIZE:
                break
            try:
                _, version, frame_type, seq, met_ms, length, flags, count = HEADER.unpack_from(
                    self._buffer
                )
            except struct.error:
                break
            if version != VERSION or length > MAX_PAYLOAD:
                self.length_errors += 1
                del self._buffer[0]
                continue
            total = HEADER_SIZE + length + CRC_STRUCT.size
            if len(self._buffer) < total:
                break
            candidate = bytes(self._buffer[:total])
            try:
                frames.append(decode_frame(candidate))
            except ValueError as exc:
                if "CRC" in str(exc):
                    self.crc_errors += 1
                else:
                    self.length_errors += 1
                del self._buffer[0]
                continue
            del self._buffer[:total]
        return frames
