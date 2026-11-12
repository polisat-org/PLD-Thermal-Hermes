import struct
import unittest

from hermes.protocol.frames import (
    HEADER_SIZE,
    MAX_PAYLOAD,
    FrameParser,
    FrameType,
    decode_frame,
    encode_frame,
)


class FrameTest(unittest.TestCase):
    def test_roundtrip(self):
        payload = bytes(range(64))
        raw = encode_frame(FrameType.ADSB, 7, 123456, payload, flags=1, count=2)
        frame = decode_frame(raw)
        self.assertEqual(frame.type, FrameType.ADSB)
        self.assertEqual(frame.seq, 7)
        self.assertEqual(frame.met_ms, 123456)
        self.assertEqual(frame.flags, 1)
        self.assertEqual(frame.count, 2)
        self.assertEqual(frame.payload, payload)

    def test_frame_object_roundtrip(self):
        frame = decode_frame(encode_frame(FrameType.HK, 1, 2, b"abc"))
        self.assertEqual(decode_frame(frame.to_bytes()), frame)

    def test_max_payload(self):
        raw = encode_frame(FrameType.ADSB, 0, 0, b"x" * MAX_PAYLOAD)
        self.assertEqual(len(raw), HEADER_SIZE + MAX_PAYLOAD + 2)
        self.assertEqual(len(decode_frame(raw).payload), MAX_PAYLOAD)

    def test_too_large(self):
        with self.assertRaises(ValueError):
            encode_frame(FrameType.ADSB, 0, 0, b"x" * (MAX_PAYLOAD + 1))

    def test_bad_crc(self):
        raw = bytearray(encode_frame(FrameType.HK, 0, 0, b"abc"))
        raw[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            decode_frame(bytes(raw))

    def test_bad_version(self):
        raw = bytearray(encode_frame(FrameType.HK, 0, 0, b"abc"))
        raw[2] = 9
        with self.assertRaises(ValueError):
            decode_frame(bytes(raw))

    def test_parser_split_and_garbage(self):
        raw = b"\x00\xAA" + encode_frame(FrameType.HK, 1, 10, b"a") + encode_frame(
            FrameType.THERMAL, 2, 20, b"b"
        )
        parser = FrameParser()
        frames = []
        for byte in raw:
            frames.extend(parser.feed(bytes([byte])))
        self.assertEqual([frame.seq for frame in frames], [1, 2])

    def test_parser_recovers_after_crc_error(self):
        good = encode_frame(FrameType.HK, 1, 10, b"a")
        bad = bytearray(encode_frame(FrameType.HK, 2, 20, b"b"))
        bad[-1] ^= 0xFF
        parser = FrameParser()
        frames = parser.feed(bytes(bad) + good)
        self.assertEqual([frame.seq for frame in frames], [1])
        self.assertGreaterEqual(parser.crc_errors, 1)

    def test_parser_rejects_bad_length(self):
        raw = bytearray(encode_frame(FrameType.HK, 0, 0, b"abc"))
        struct.pack_into("<H", raw, 10, MAX_PAYLOAD + 1)
        parser = FrameParser()
        frames = parser.feed(bytes(raw))
        self.assertEqual(frames, [])
        self.assertGreaterEqual(parser.length_errors, 1)


if __name__ == "__main__":
    unittest.main()
