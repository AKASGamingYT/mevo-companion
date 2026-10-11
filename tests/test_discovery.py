import unittest

from mevo_companion.discovery import DiscoveredCamera, parse_battery


class BatteryParseTests(unittest.TestCase):
    def test_on_battery(self) -> None:
        self.assertEqual(
            parse_battery({"camera_charge": "86", "power_status": "0"}),
            {"percent": 86, "charging": False, "power_status": 0},
        )

    def test_charging_ac(self) -> None:
        self.assertEqual(
            parse_battery({"camera_charge": "100", "power_status": "6"}),
            {"percent": 100, "charging": True, "power_status": 6},
        )

    def test_clamps_and_missing(self) -> None:
        self.assertEqual(
            parse_battery({"camera_charge": "140"}),
            {"percent": 100, "charging": False, "power_status": None},
        )
        self.assertEqual(
            parse_battery({}),
            {"percent": None, "charging": False, "power_status": None},
        )
        self.assertEqual(
            parse_battery({"camera_charge": "nope", "power_status": ""}),
            {"percent": None, "charging": False, "power_status": None},
        )

    def test_camera_dict_includes_battery(self) -> None:
        cam = DiscoveredCamera(
            id="abc",
            name="mevo-27FRY",
            host="192.168.1.60",
            port=38000,
            service_type="_ls-cameraman._tcp.local.",
            txt={"camera_name": "Piano Cam", "camera_charge": "21", "power_status": "0"},
        )
        data = cam.to_dict()
        self.assertEqual(data["battery"]["percent"], 21)
        self.assertFalse(data["battery"]["charging"])
        self.assertFalse(data["offline"])
        self.assertFalse(data["powered_off"])


class OfflineRetentionTests(unittest.TestCase):
    def test_remove_marks_offline_instead_of_dropping(self) -> None:
        from mevo_companion.discovery import CameraRegistry, DiscoveredCamera

        registry = CameraRegistry()
        cam = DiscoveredCamera(
            id="abc",
            name="mevo-27FRY",
            host="192.168.1.60",
            port=38000,
            service_type="_ls-cameraman._tcp.local.",
            txt={"camera_name": "Piano Cam", "camera_charge": "21", "fw_version": "5.3.9"},
        )
        with registry._lock:
            registry._cameras[cam.id] = cam
            registry._mdns_index["_ls-cameraman._tcp.local.|mevo-27FRY._ls-cameraman._tcp.local."] = cam.id
        registry._remove_mdns("_ls-cameraman._tcp.local.", "mevo-27FRY._ls-cameraman._tcp.local.")
        kept = registry.get("abc")
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertTrue(kept.offline)
        self.assertFalse(kept.powered_off)
        self.assertEqual(kept.txt["camera_charge"], "21")
        self.assertEqual(registry.list()[0].id, "abc")
        marked = registry.mark_offline("abc", powered_off=True)
        self.assertTrue(marked.offline if marked else False)
        self.assertTrue(marked.powered_off if marked else False)


class IdentityTests(unittest.TestCase):
    def _txt(self) -> dict[str, str]:
        return {
            "camera_name": "Piano Cam",
            "serial_number": "AA00427FRY",
            "fw_version": "5.3.9",
            "camera_charge": "80",
        }

    def test_new_address_reuses_the_same_camera(self) -> None:
        from mevo_companion.discovery import CameraRegistry

        registry = CameraRegistry()
        txt = self._txt()
        first = registry.observe(
            service_type="_ls-cameraman._tcp.local.",
            service_name="mevo-27FRY._ls-cameraman._tcp.local.",
            host="192.168.1.60",
            port=38000,
            txt=txt,
        )
        second = registry.observe(
            service_type="_ls-cameraman._tcp.local.",
            service_name="mevo-27FRY._ls-cameraman._tcp.local.",
            host="192.168.1.64",
            port=38000,
            txt=txt,
        )
        self.assertEqual(len(registry.list()), 1)
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.host, "192.168.1.64")
        self.assertFalse(second.offline)
        self.assertFalse(second.powered_off)

    def test_both_service_types_stay_one_camera_until_both_leave(self) -> None:
        from mevo_companion.discovery import CameraRegistry

        registry = CameraRegistry()
        txt = self._txt()
        registry.observe(
            service_type="_ls-cameraman._tcp.local.",
            service_name="mevo-27FRY._ls-cameraman._tcp.local.",
            host="192.168.1.60",
            port=38000,
            txt=txt,
        )
        registry.observe(
            service_type="_mevo-cameraman._tcp.local.",
            service_name="mevo-27FRY._mevo-cameraman._tcp.local.",
            host="192.168.1.60",
            port=38000,
            txt=txt,
        )
        self.assertEqual(len(registry.list()), 1)
        registry._remove_mdns("_ls-cameraman._tcp.local.", "mevo-27FRY._ls-cameraman._tcp.local.")
        self.assertFalse(registry.list()[0].offline)
        registry._remove_mdns("_mevo-cameraman._tcp.local.", "mevo-27FRY._mevo-cameraman._tcp.local.")
        self.assertTrue(registry.list()[0].offline)

    def test_collapse_keeps_the_original_id_and_aliases_the_duplicate(self) -> None:
        from mevo_companion.discovery import CameraRegistry, DiscoveredCamera

        registry = CameraRegistry()
        txt = self._txt()
        older = DiscoveredCamera(
            id="oldid",
            name="mevo-27FRY",
            host="192.168.1.60",
            port=38000,
            service_type="_ls-cameraman._tcp.local.",
            txt=dict(txt),
            last_seen="2026-09-20T16:51:01+00:00",
            offline=True,
            powered_off=True,
        )
        newer = DiscoveredCamera(
            id="newid",
            name="mevo-27FRY",
            host="192.168.1.64",
            port=38000,
            service_type="_ls-cameraman._tcp.local.",
            txt=dict(txt),
            last_seen="2026-10-04T14:46:17+00:00",
            offline=False,
        )
        with registry._lock:
            registry._cameras[older.id] = older
            registry._cameras[newer.id] = newer
            registry._collapse_locked()
        listed = registry.list()
        self.assertEqual(len(listed), 1)
        cam = listed[0]
        self.assertEqual(cam.id, "oldid")
        self.assertEqual(cam.host, "192.168.1.64")
        self.assertFalse(cam.offline)
        self.assertFalse(cam.powered_off)
        self.assertIn("newid", cam.aliases)
        self.assertIs(registry.get("newid"), cam)
        self.assertIs(registry.get("oldid"), cam)
        self.assertIn("newid", cam.to_dict()["aliases"])
