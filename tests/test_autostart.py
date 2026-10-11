import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mevo_companion import autostart


class AutostartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.startup = Path(self.tmp.name) / "Startup"
        self.store = Path(self.tmp.name) / "store"
        self.startup.mkdir()
        self.store.mkdir()
        self.old_startup = os.environ.get("MEVO_COMPANION_STARTUP_DIR")
        self.old_store = os.environ.get("MEVO_COMPANION_STORE")
        os.environ["MEVO_COMPANION_STARTUP_DIR"] = str(self.startup)
        os.environ["MEVO_COMPANION_STORE"] = str(self.store)

    def tearDown(self) -> None:
        if self.old_startup is None:
            os.environ.pop("MEVO_COMPANION_STARTUP_DIR", None)
        else:
            os.environ["MEVO_COMPANION_STARTUP_DIR"] = self.old_startup
        if self.old_store is None:
            os.environ.pop("MEVO_COMPANION_STORE", None)
        else:
            os.environ["MEVO_COMPANION_STORE"] = self.old_store
        self.tmp.cleanup()

    def test_install_writes_hidden_background_launcher(self) -> None:
        info = autostart.install_autostart()
        script = Path(str(info["path"]))
        self.assertTrue(script.exists())
        text = script.read_text(encoding="utf-8")
        self.assertIn("-m mevo_companion", text)
        self.assertNotIn("--no-window", text)
        self.assertNotIn("--open-ui", text)
        self.assertIn("pythonw.exe", text)
        self.assertIn(", 0, False", text)
        self.assertTrue(info["installed"])

    def test_uninstall_stops_ensure_from_reinstalling(self) -> None:
        autostart.install_autostart()
        autostart.uninstall_autostart()
        self.assertFalse(autostart.startup_script().exists())
        self.assertFalse(autostart.is_installed())
        self.assertIsNone(autostart.ensure_autostart())
        self.assertFalse(autostart.startup_script().exists())

    def test_ensure_installs_when_missing(self) -> None:
        info = autostart.ensure_autostart()
        self.assertIsNotNone(info)
        self.assertTrue(autostart.startup_script().exists())

    def test_tray_icon_is_the_mevo_logo(self) -> None:
        from mevo_companion.tray import load_icon

        image = load_icon()
        self.assertGreaterEqual(image.size[0], 16)
        self.assertEqual(image.mode, "RGBA")

    def test_install_startup_cli_exits_without_server(self) -> None:
        from mevo_companion.__main__ import main

        with patch("mevo_companion.__main__.InstanceLock") as lock_cls:
            self.assertEqual(main(["--install-startup"]), 0)
            lock_cls.assert_not_called()
        self.assertTrue(autostart.startup_script().exists())
