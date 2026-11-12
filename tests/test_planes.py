import math
import struct
import unittest

from hermes.protocol.planes import (
    PLANE_SIZE,
    Plane,
    pack_planes,
    parse_icao,
    unpack_planes,
)


class PlaneTest(unittest.TestCase):
    def test_size(self):
        self.assertEqual(PLANE_SIZE, 28)
        self.assertEqual(len(Plane(0xABC123).to_bytes()), 28)

    def test_roundtrip(self):
        plane = Plane(0xABC123, -23.5, -46.6, 35000.0, 450.0, 90.0, 1234)
        data = plane.to_bytes()
        self.assertEqual(data[0:3], b"\x23\xc1\xab")
        decoded = Plane.from_bytes(data)
        self.assertEqual(decoded.icao_id, plane.icao_id)
        self.assertAlmostEqual(decoded.latitude, plane.latitude, places=4)
        self.assertAlmostEqual(decoded.longitude, plane.longitude, places=4)
        self.assertAlmostEqual(decoded.altitude_ft, plane.altitude_ft)
        self.assertAlmostEqual(decoded.velocity_kt, plane.velocity_kt)
        self.assertAlmostEqual(decoded.track_deg, plane.track_deg)
        self.assertEqual(decoded.last_pos_met_ms, plane.last_pos_met_ms)

    def test_nan_invalid(self):
        plane = Plane(1, None, float("nan"), None, None, None, 0)
        decoded = Plane.from_bytes(plane.to_bytes())
        self.assertTrue(math.isnan(decoded.latitude))
        self.assertTrue(math.isnan(decoded.altitude_ft))
        self.assertEqual(decoded.last_pos_met_ms, 0)

    def test_icao_masked(self):
        self.assertEqual(struct.unpack("<I", Plane(0x1FFFFFFF).to_bytes()[:4])[0], 0xFFFFFF)

    def test_pack_twenty(self):
        planes = [Plane(i) for i in range(20)]
        payload = pack_planes(planes)
        self.assertEqual(len(payload), 560)
        self.assertEqual(len(unpack_planes(payload)), 20)

    def test_pack_too_many(self):
        with self.assertRaises(ValueError):
            pack_planes([Plane(i) for i in range(21)])

    def test_parse_icao(self):
        self.assertEqual(parse_icao("abc123"), 0xABC123)
        self.assertEqual(parse_icao(0x1ABC123), 0xABC123)
        with self.assertRaises(ValueError):
            parse_icao(None)


if __name__ == "__main__":
    unittest.main()
