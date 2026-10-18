import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from mevo_companion.api import create_app
from mevo_companion.discovery import CameraRegistry
from mevo_companion.firmware.store import store_dir


class FrozenStoreTests(unittest.TestCase):
    def test_packaged_app_keeps_firmware_in_the_user_data_dir(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEVO_COMPANION_STORE", None)
            with (
                patch("mevo_companion.firmware.store.is_frozen", return_value=True),
                patch(
                    "mevo_companion.firmware.store.user_data_dir",
                    return_value=Path("/tmp/mevo-data"),
                ),
            ):
                self.assertEqual(store_dir(), Path("/tmp/mevo-data") / "firmware-store")


class ShutdownTests(unittest.TestCase):
    def test_local_shutdown_stops_the_app(self) -> None:
        app = create_app(CameraRegistry())
        stop = threading.Event()
        app.state.stop = stop
        response = TestClient(app).post("/api/shutdown")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(stop.is_set())

    def test_quit_command_fails_when_the_app_is_stopped(self) -> None:
        from mevo_companion.__main__ import main

        self.assertEqual(main(["--quit", "--port", "9"]), 1)
