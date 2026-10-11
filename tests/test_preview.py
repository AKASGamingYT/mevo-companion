import unittest

from mevo_companion.preview import camera_ndi_hints, match_ndi_source, ndi_enabled, source_switch_ready


class NdiMatchTests(unittest.TestCase):
    def test_matches_serial_suffix_in_ndi_name(self) -> None:
        camera = {
            "name": "mevo-27GNR",
            "txt": {
                "serial_number": "AA00427GNR",
                "camera_name": "Balcony Cam",
                "ndi_mode": "true",
            },
        }
        hints = camera_ndi_hints(camera)
        self.assertIn("27GNR", hints)
        self.assertEqual(
            match_ndi_source(hints, ["MEVO-27GNR (Mevo-27GNR)", "MEVO-27FRY (Mevo-27FRY)"]),
            "MEVO-27GNR (Mevo-27GNR)",
        )
        self.assertTrue(ndi_enabled(camera))

    def test_ndi_off(self) -> None:
        self.assertFalse(ndi_enabled({"txt": {"ndi_mode": "false"}}))
        self.assertIsNone(match_ndi_source(["Piano Cam"], ["MEVO-27GNR (Mevo-27GNR)"]))

    def test_rejects_frame_from_wrong_ndi_source(self) -> None:
        from mevo_companion.preview import PreviewFrame, PreviewHub

        balcony = {
            "id": "balc",
            "name": "mevo-27GNR",
            "txt": {
                "serial_number": "AA00427GNR",
                "camera_name": "Balcony Cam",
                "ndi_mode": "true",
            },
        }
        hub = PreviewHub(get_camera=lambda _cid: balcony)
        hub._frames["balc"] = PreviewFrame(
            jpeg=b"not-a-jpeg",
            width=640,
            height=360,
            source="MEVO-27FRY (Mevo-27FRY)",
            captured_at=0.0,
        )
        self.assertIsNone(hub.frame("balc"))
        hub._frames["balc"].source = "MEVO-27GNR (Mevo-27GNR)"
        self.assertIsNotNone(hub.frame("balc"))

    def test_source_switch_debounce(self) -> None:
        self.assertTrue(source_switch_ready("", "MEVO-A", 0.0, 0.0))
        self.assertFalse(source_switch_ready("MEVO-A", "MEVO-A", 1.0, 2.0))
        self.assertFalse(source_switch_ready("MEVO-A", "MEVO-B", 1.0, 1.2, hold=0.35))
        self.assertTrue(source_switch_ready("MEVO-A", "MEVO-B", 1.0, 1.4, hold=0.35))


if __name__ == "__main__":
    unittest.main()
