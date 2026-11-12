import unittest

from hermes.protocol.crc import crc16_ccitt


class CrcTest(unittest.TestCase):
    def test_known_vector(self):
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)

    def test_empty(self):
        self.assertEqual(crc16_ccitt(b""), 0xFFFF)

    def test_different_data_differs(self):
        self.assertNotEqual(crc16_ccitt(b"abc"), crc16_ccitt(b"abd"))

    def test_custom_init(self):
        self.assertEqual(crc16_ccitt(b"123456789", 0), 0x31C3)


if __name__ == "__main__":
    unittest.main()
