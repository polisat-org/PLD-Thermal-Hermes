import unittest

from hermes.protocol.payloads import (
    TEMP_INVALID,
    Housekeeping,
    ThermalSample,
    decode_ack,
    decode_hk,
    decode_thermal,
    encode_ack,
    encode_event,
    encode_hk,
    encode_thermal,
)


class PayloadTest(unittest.TestCase):
    def test_thermal_roundtrip(self):
        sample = ThermalSample((5.0, -1.25, None, 30.0), (0.5, 1.0), 3)
        data = encode_thermal(sample)
        self.assertEqual(len(data), 11)
        decoded = decode_thermal(data)
        self.assertEqual(decoded.temps_c, (5.0, -1.25, None, 30.0))
        self.assertEqual(decoded.duties, (0.5, 1.0))
        self.assertEqual(decoded.status, 3)

    def test_thermal_invalid(self):
        sample = ThermalSample((None, None, None, None), (2.0, -1.0), 1)
        decoded = decode_thermal(encode_thermal(sample))
        self.assertEqual(decoded.temps_c, (None, None, None, None))
        self.assertEqual(decoded.duties, (1.0, 0.0))

    def test_hk_roundtrip(self):
        hk = Housekeeping(1, 2, 3, 4, 5, 4212, 1, 1, 3)
        decoded = decode_hk(encode_hk(hk))
        self.assertEqual(decoded, hk)

    def test_hk_invalid_cpu_temp(self):
        hk = Housekeeping(cpu_temp_x100=TEMP_INVALID)
        self.assertEqual(decode_hk(encode_hk(hk)).cpu_temp_x100, TEMP_INVALID)

    def test_ack_roundtrip(self):
        self.assertEqual(decode_ack(encode_ack(7, 5, 0)), (7, 5, 0))

    def test_event(self):
        self.assertEqual(encode_event(1, "ON"), b"\x01ON")


if __name__ == "__main__":
    unittest.main()
