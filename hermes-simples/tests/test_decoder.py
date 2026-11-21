import sys
import tempfile
import unittest
from pathlib import Path

from hermes_simples.decoder_process import DecoderProcess


class DecoderProcessTests(unittest.TestCase):
    def test_stale_json_is_removed_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "aircraft.json"
            json_path.write_text('{"messages": -1, "aircraft": []}',
                                 encoding="utf-8")

            fake_decoder = (
                "import pathlib, sys, time\n"
                "p = pathlib.Path(sys.argv[1])\n"
                "p.write_text('{\"messages\": 7, \"aircraft\": []}')\n"
                "time.sleep(10)\n"
            )
            decoder = DecoderProcess(
                sys.executable,
                ("-c", fake_decoder, str(json_path)),
                json_path.as_uri(),
                start_timeout_s=3.0,
                restart_backoff_s=0.0,
                log_path=str(Path(tmp) / "decoder.log"),
            )
            try:
                self.assertTrue(decoder.start())
                self.assertTrue(decoder.is_alive())
                self.assertIn('"messages": 7', json_path.read_text(encoding="utf-8"))
            finally:
                decoder.stop()

    def test_dead_decoder_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            decoder = DecoderProcess(
                sys.executable,
                ("-c", "import sys; sys.exit(3)"),
                (Path(tmp) / "aircraft.json").as_uri(),
                start_timeout_s=1.0,
                restart_backoff_s=0.0,
                log_path=str(Path(tmp) / "decoder.log"),
            )
            self.assertFalse(decoder.start())
            self.assertEqual(decoder.failures, 1)
            self.assertFalse(decoder.is_alive())


if __name__ == "__main__":
    unittest.main()
