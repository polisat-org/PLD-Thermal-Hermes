import unittest

from hermes.adsb.source import apply_aircraft_json
from hermes.adsb.table import AircraftTable


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.table = AircraftTable(capacity=20, ttl_s=300, position_max_age_s=10)

    def test_apply(self):
        document = {
            "now": 1.0,
            "aircraft": [
                {
                    "hex": "abc123",
                    "lat": -23.5,
                    "lon": -46.6,
                    "alt_baro": 35000,
                    "gs": 450.0,
                    "track": 90.0,
                    "seen_pos": 1.5,
                }
            ],
        }
        self.assertEqual(apply_aircraft_json(document, self.table, 10000), 1)
        plane = self.table.snapshot(10000)[0]
        self.assertEqual(plane.icao_id, 0xABC123)
        self.assertEqual(plane.latitude, -23.5)
        self.assertEqual(plane.last_pos_met_ms, 8500)

    def test_ground_altitude(self):
        document = {"aircraft": [{"hex": "abc123", "alt_baro": "ground"}]}
        apply_aircraft_json(document, self.table, 0)
        self.assertEqual(self.table.snapshot(0)[0].altitude_ft, 0.0)

    def test_invalid_entries_ignored(self):
        document = {"aircraft": [{"hex": ""}, {"lat": 1.0}, "bad"]}
        self.assertEqual(apply_aircraft_json(document, self.table, 0), 0)


if __name__ == "__main__":
    unittest.main()
