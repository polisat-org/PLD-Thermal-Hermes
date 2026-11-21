import csv
import tempfile
import unittest
from pathlib import Path

from hermes_simples.mission_log import MissionLog


class MissionLogTests(unittest.TestCase):
    def read_rows(self, path):
        with Path(path).open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_start_send_stop_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subdir" / "mission.csv"
            log = MissionLog(str(path))
            log.start()
            log.log(met_s=5.0, aircraft_count=7, messages_total=321,
                    decoder_errors=1, buffers_sent=1)
            log.finish(met_s=10.0, messages_total=400,
                       decoder_errors=1, buffers_sent=2)

            rows = self.read_rows(path)
            self.assertEqual(len(rows), 3)
            self.assertEqual([r["event"] for r in rows], ["start", "send", "stop"])
            self.assertEqual(rows[1]["met_s"], "5.0")
            self.assertEqual(rows[1]["aircraft_count"], "7")
            self.assertEqual(rows[1]["messages_total"], "321")
            self.assertEqual(rows[2]["buffers_sent"], "2")
            self.assertTrue(rows[0]["utc"].endswith("+00:00") or rows[0]["utc"].endswith("Z"))

    def test_header_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mission.csv"
            log = MissionLog(str(path))
            log.start()
            second = MissionLog(str(path))
            second.log(met_s=1.0, aircraft_count=0, messages_total=0,
                       decoder_errors=0, buffers_sent=1)
            self.assertEqual(len(self.read_rows(path)), 2)

    def test_disabled_log_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mission.csv"
            log = MissionLog(str(path), enabled=False)
            log.start()
            log.log(met_s=1.0, aircraft_count=0, messages_total=0,
                    decoder_errors=0, buffers_sent=1)
            log.finish(met_s=2.0, messages_total=0, decoder_errors=0,
                       buffers_sent=1)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
