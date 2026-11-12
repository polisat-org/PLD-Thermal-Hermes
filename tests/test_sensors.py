import tempfile
import unittest
from pathlib import Path

from hermes.thermal.sensors import ThermalSensors, discover_sensors, parse_w1_slave


class W1Test(unittest.TestCase):
    def test_parse_valid(self):
        text = "72 01 4b 46 7f ff 0e 10 67 : crc=67 YES\n72 01 4b 46 7f ff 0e 10 67 t=23125\n"
        self.assertEqual(parse_w1_slave(text), 23.125)

    def test_parse_negative(self):
        text = "aa 01 4b 46 7f ff 0e 10 67 : crc=67 YES\naa 01 4b 46 7f ff 0e 10 67 t=-5000\n"
        self.assertEqual(parse_w1_slave(text), -5.0)

    def test_parse_crc_error(self):
        text = "aa 01 4b 46 7f ff 0e 10 67 : crc=67 NO\naa 01 4b 46 7f ff 0e 10 67 t=5000\n"
        self.assertIsNone(parse_w1_slave(text))

    def test_parse_garbage(self):
        self.assertIsNone(parse_w1_slave("nonsense"))
        self.assertIsNone(parse_w1_slave("x YES\ny t=abc"))


class SensorsTest(unittest.TestCase):
    def test_discovery_and_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rom in ("28-000000000003", "28-000000000001", "28-000000000002"):
                path = Path(tmp) / rom
                path.mkdir()
                (path / "w1_slave").write_text(
                    "x YES\ny t=10000\n", encoding="ascii"
                )
            found = discover_sensors(tmp)
            self.assertEqual([p.name for p in found][:2], ["28-000000000001", "28-000000000002"])
            sensors = ThermalSensors(root=tmp)
            groups = sensors.read_groups()
            self.assertEqual(groups[1], (10.0, 10.0))
            self.assertEqual(groups[2], (10.0, None))

    def test_explicit_map_missing_sensor(self):
        with tempfile.TemporaryDirectory() as tmp:
            sensors = ThermalSensors({1: ("28-000000000001",), 2: ()}, root=tmp)
            groups = sensors.read_groups()
            self.assertEqual(groups[1], (None, None))
            self.assertEqual(groups[2], (None, None))


if __name__ == "__main__":
    unittest.main()
