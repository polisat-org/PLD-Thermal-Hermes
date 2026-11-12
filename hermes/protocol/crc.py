"""CRC16-CCITT (XMODEM) used by the payload/OBC frames."""

from __future__ import annotations


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    """Return the CRC16-CCITT of *data* (poly 0x1021, no reflection)."""
    crc = init & 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc
