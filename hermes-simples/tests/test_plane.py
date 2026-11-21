import math
import unittest

from hermes_simples.plane import (
    ADDRTYPE,
    BUFFER_SIZE,
    FLOAT_FIELDS,
    NAV_ALT_HOLD,
    NAV_TCAS,
    PLANE_SIZE,
    PLANE_STRUCT,
    empty_slot,
    enum_code,
    nav_modes_code,
    new_plane,
    pack_planes,
    unpack_planes,
)


class PlaneTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(PLANE_SIZE, 204)
        self.assertEqual(BUFFER_SIZE, 4080)
        self.assertEqual(len(FLOAT_FIELDS), 45)
        self.assertEqual(PLANE_STRUCT.size, PLANE_SIZE)

    def test_met_s_logo_depois_do_icao(self):
        # Garante que o OBC sabe onde ler o timestamp sem procurar no cabecalho.
        self.assertEqual(FLOAT_FIELDS[0], "icaoID")
        self.assertEqual(FLOAT_FIELDS[1], "met_s")
        self.assertEqual(PLANE_STRUCT.format, "<8s8s45fII")

    def test_roundtrip_all_fields(self):
        plane = new_plane(0xE48C0A)
        plane["flight"] = "ABC123"
        plane["squawk"] = "7700"
        for i, name in enumerate(FLOAT_FIELDS):
            plane[name] = float(i) + 0.5
        plane["nav_modes"] = NAV_ALT_HOLD | NAV_TCAS
        plane["flags"] = 0b1011

        slots = unpack_planes(pack_planes([plane]))
        self.assertEqual(len(slots), 20)
        out = slots[0]
        self.assertEqual(out["flight"], "ABC123")
        self.assertEqual(out["squawk"], "7700")
        self.assertEqual(out["icaoID"], 0.5)
        self.assertEqual(out["met_s"], 1.5)
        self.assertEqual(out["rssi"], float(len(FLOAT_FIELDS) - 1) + 0.5)
        self.assertEqual(out["nav_modes"], NAV_ALT_HOLD | NAV_TCAS)
        self.assertEqual(out["flags"], 0b1011)
        for name in FLOAT_FIELDS:
            self.assertEqual(out[name], plane[name], name)

    def test_empty_slots_are_zero(self):
        slots = unpack_planes(pack_planes([]))
        for slot in slots:
            self.assertEqual(slot["icaoID"], 0.0)
            self.assertEqual(slot["met_s"], 0.0)
            self.assertEqual(slot["flight"], "")
        self.assertEqual(pack_planes([]), bytes(BUFFER_SIZE))

    def test_nan_survives(self):
        plane = new_plane(0x123456)
        plane["lat"] = math.nan
        out = unpack_planes(pack_planes([plane]))[0]
        self.assertTrue(math.isnan(out["lat"]))
        self.assertTrue(math.isnan(out["alt_baro"]))

    def test_string_truncation(self):
        plane = new_plane(1)
        plane["flight"] = "ABCDEFGHIJK"
        self.assertEqual(unpack_planes(pack_planes([plane]))[0]["flight"], "ABCDEFG")

    def test_enums(self):
        self.assertEqual(enum_code("adsb_icao", ADDRTYPE), 1.0)
        self.assertEqual(enum_code("NAO_EXISTE", ADDRTYPE), 0.0)
        self.assertEqual(nav_modes_code(["autopilot", "lnav"]), 0b10001)

    def test_rejects_wrong_buffer(self):
        with self.assertRaises(ValueError):
            unpack_planes(b"\x00" * 100)

    def test_empty_slot_helper(self):
        slot = empty_slot()
        self.assertEqual(slot["icaoID"], 0.0)
        self.assertEqual(slot["met_s"], 0.0)


if __name__ == "__main__":
    unittest.main()
