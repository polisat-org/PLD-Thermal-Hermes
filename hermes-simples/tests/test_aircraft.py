import json
import tempfile
import unittest
from pathlib import Path

from hermes_simples.aircraft import AircraftList, JsonSource, apply_aircraft_json
from hermes_simples.plane import FLAG_MLAT, FLAG_MODE_A


class Clock:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class AircraftTests(unittest.TestCase):
    def test_new_icao_gets_new_slot(self):
        aircraft = AircraftList()
        self.assertEqual(aircraft.update(0xABCDEF, {"lat": -23.5}), 0)
        self.assertEqual(aircraft.update(0x123456, {"lat": -22.0}), 1)
        slots = aircraft.snapshot()
        self.assertEqual(slots[0]["icaoID"], float(0xABCDEF))
        self.assertEqual(slots[1]["icaoID"], float(0x123456))

    def test_existing_icao_updates_same_slot(self):
        aircraft = AircraftList()
        aircraft.update(0xABCDEF, {"lat": -23.5, "gs": 100})
        index = aircraft.update(0xABCDEF, {"lat": -24.0, "gs": 200})
        self.assertEqual(index, 0)
        slot = aircraft.snapshot()[0]
        self.assertEqual(slot["lat"], -24.0)
        self.assertEqual(slot["gs"], 200.0)
        self.assertEqual(len(aircraft), 1)

    def test_full_list_ignores_new(self):
        aircraft = AircraftList(capacity=2)
        aircraft.update(1, {})
        aircraft.update(2, {})
        self.assertIsNone(aircraft.update(3, {}))
        self.assertEqual(aircraft.ignored, 1)
        self.assertEqual(len(aircraft), 2)

    def test_stale_icao_frees_slot_and_21st_enters(self):
        # Era o bug principal: com 20 avioes, o 21 nunca entrava.
        aircraft = AircraftList()
        for i in range(20):
            self.assertEqual(aircraft.update(0x100000 + i, {"seen": 0.0}), i)
        self.assertIsNone(aircraft.update(0x100014 + 1, {"seen": 0.0}))

        self.assertIsNone(aircraft.update(0x100000, {"seen": 400.0}))
        self.assertEqual(aircraft.snapshot()[0]["icaoID"], 0.0)
        self.assertEqual(len(aircraft), 19)

        self.assertEqual(aircraft.update(0x200000, {"seen": 0.2}), 0)
        self.assertEqual(len(aircraft), 20)
        self.assertEqual(aircraft.snapshot()[0]["icaoID"], float(0x200000))

    def test_purge_missing_icaos(self):
        aircraft = AircraftList(capacity=2, max_missing_polls=2)
        aircraft.update(1, {})
        aircraft.update(2, {})
        self.assertEqual(aircraft.purge({2}), 0)
        self.assertEqual(aircraft.purge({2}), 0)
        self.assertEqual(aircraft.purge({2}), 1)
        self.assertEqual(aircraft.snapshot()[0]["icaoID"], 0.0)
        self.assertEqual(aircraft.snapshot()[1]["icaoID"], 2.0)

    def test_apply_json_purges_before_allocating(self):
        aircraft = AircraftList(capacity=2, max_missing_polls=0)
        aircraft.update(1, {})
        aircraft.update(2, {})
        updated = apply_aircraft_json({"aircraft": [{"hex": "3"}]}, aircraft)
        self.assertEqual(updated, 1)
        self.assertEqual(len(aircraft), 1)
        self.assertEqual(aircraft.snapshot()[0]["icaoID"], 3.0)

    def test_json_ground_and_flags(self):
        aircraft = AircraftList()
        updated = apply_aircraft_json(
            {"aircraft": [{
                "hex": "e48c0a",
                "flight": "TESTE",
                "alt_baro": "ground",
                "gs": 250.5,
                "modea": True,
                "mlat": ["adsb"],
            }]},
            aircraft,
        )
        self.assertEqual(updated, 1)
        slot = aircraft.snapshot()[0]
        self.assertEqual(slot["alt_baro"], 0.0)
        self.assertEqual(slot["gs"], 250.5)
        self.assertEqual(slot["flight"], "TESTE")
        self.assertEqual(slot["flags"], FLAG_MODE_A | FLAG_MLAT)

    def test_timestamp_is_mission_elapsed_time(self):
        aircraft = AircraftList(clock=Clock(100.0, 103.25))
        aircraft.set_mission_start()
        aircraft.update(0xABCDEF, {"lat": -23.5, "seen": 0.1})
        slot = aircraft.snapshot()[0]
        self.assertEqual(slot["met_s"], 3.25)
        self.assertFalse(slot["met_s"] != slot["met_s"])

    def test_all_planes_in_one_json_share_timestamp(self):
        aircraft = AircraftList(clock=Clock(50.0, 57.5))
        aircraft.set_mission_start()
        apply_aircraft_json(
            {"aircraft": [{"hex": "1"}, {"hex": "2"}]}, aircraft
        )
        self.assertEqual(aircraft.snapshot()[0]["met_s"], 7.5)
        self.assertEqual(aircraft.snapshot()[1]["met_s"], 7.5)

    def test_stale_is_ignored(self):
        aircraft = AircraftList(max_age_s=10)
        self.assertIsNone(aircraft.update(1, {"seen": 99}))
        self.assertEqual(len(aircraft), 0)

    def test_clear(self):
        aircraft = AircraftList()
        aircraft.update(1, {})
        aircraft.clear()
        self.assertEqual(len(aircraft), 0)
        self.assertEqual(aircraft.snapshot()[0]["icaoID"], 0.0)

    def test_json_source_reads_messages_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aircraft.json"
            path.write_text(json.dumps({
                "now": 1.0,
                "messages": 123,
                "aircraft": [{"hex": "e48c0a", "seen": 0.1}],
            }), encoding="utf-8")

            aircraft = AircraftList()
            source = JsonSource(path.as_uri(), aircraft)
            self.assertEqual(source.poll_once(), 1)
            self.assertEqual(source.total_messages, 123)
            self.assertEqual(source.errors, 0)
            self.assertEqual(source.last_updates, 1)


if __name__ == "__main__":
    unittest.main()
