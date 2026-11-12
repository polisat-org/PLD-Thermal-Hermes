import math
import unittest

from hermes.adsb.table import AircraftTable


class AircraftTableTest(unittest.TestCase):
    def test_capacity_evicts_oldest(self):
        table = AircraftTable(capacity=2, ttl_s=1000, position_max_age_s=10)
        table.update(1, 0)
        table.update(2, 1)
        table.update(3, 2)
        self.assertEqual([p.icao_id for p in table.snapshot(2)], [2, 3])

    def test_existing_moves_to_end(self):
        table = AircraftTable(capacity=2, ttl_s=1000, position_max_age_s=10)
        table.update(1, 0)
        table.update(2, 1)
        table.update(1, 2)
        table.update(3, 3)
        self.assertEqual([p.icao_id for p in table.snapshot(3)], [1, 3])

    def test_ttl_purge(self):
        table = AircraftTable(capacity=4, ttl_s=5, position_max_age_s=10)
        table.update(1, 1000)
        table.update(2, 7000)
        self.assertEqual(table.purge(6000), 1)
        self.assertEqual(len(table), 1)

    def test_position_staleness(self):
        table = AircraftTable(capacity=1, ttl_s=1000, position_max_age_s=10)
        table.update(1, 10000, latitude=1.0, longitude=2.0)
        self.assertTrue(math.isfinite(table.snapshot(20000)[0].latitude))
        self.assertTrue(math.isnan(table.snapshot(20101)[0].latitude))
        self.assertEqual(table.snapshot(20101)[0].last_pos_met_ms, 0)

    def test_position_age(self):
        table = AircraftTable(capacity=1, ttl_s=1000, position_max_age_s=10)
        table.update(1, 10000, latitude=1.0, longitude=2.0, position_age_s=3.0)
        self.assertEqual(table.snapshot(10000)[0].last_pos_met_ms, 7000)

    def test_missing_fields_kept(self):
        table = AircraftTable(capacity=1, ttl_s=1000, position_max_age_s=10)
        table.update(1, 0, latitude=1.0, longitude=2.0, altitude_ft=100.0, velocity_kt=50.0)
        table.update(1, 1000, altitude_ft=200.0)
        plane = table.snapshot(1000)[0]
        self.assertEqual(plane.latitude, 1.0)
        self.assertEqual(plane.altitude_ft, 200.0)
        self.assertEqual(plane.velocity_kt, 50.0)

    def test_zero_icao_ignored(self):
        table = AircraftTable(capacity=1)
        self.assertIsNone(table.update(0, 0))
        self.assertEqual(len(table), 0)

    def test_invalid_position(self):
        table = AircraftTable(capacity=1, ttl_s=1000, position_max_age_s=10)
        table.update(1, 0, latitude="abc", longitude=2.0)
        self.assertTrue(math.isnan(table.snapshot(0)[0].latitude))
        self.assertEqual(table.snapshot(0)[0].last_pos_met_ms, 0)


if __name__ == "__main__":
    unittest.main()
