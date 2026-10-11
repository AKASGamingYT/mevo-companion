import json
import struct
import unittest

from mevo_companion.protocol import MAGIC, decode_frame, encode_frame, wire_command


class ProtocolTests(unittest.TestCase):
    def test_wire_command_pascal_and_snake(self) -> None:
        self.assertEqual(wire_command("Ping"), "ping")
        self.assertEqual(wire_command("GetSettings"), "get_settings")
        self.assertEqual(wire_command("GetNetworkConfiguration"), "get_network_configuration")
        self.assertEqual(wire_command("authorize"), "authorize")
        self.assertEqual(wire_command("get_settings"), "get_settings")
        self.assertEqual(wire_command("GfxGet"), "gfx_get")
        self.assertEqual(wire_command("Shutdown"), "shut_down")
        self.assertEqual(wire_command("ShutDown"), "shut_down")
        self.assertEqual(wire_command("shutdown"), "shut_down")
        self.assertEqual(wire_command("shut_down"), "shut_down")
        self.assertEqual(wire_command("Reboot"), "reboot")

    def test_cman_roundtrip(self) -> None:
        payload = {"command": "ping"}
        frame = encode_frame(payload)
        self.assertTrue(frame.startswith(MAGIC))
        (length,) = struct.unpack(">I", frame[4:8])
        self.assertEqual(length, len(b'{"command":"ping"}'))
        self.assertIn(json.dumps(payload, separators=(",", ":")).encode(), frame)
        self.assertEqual(decode_frame(frame), payload)


class ShutdownWireTests(unittest.TestCase):
    def test_shutdown_action_sends_shut_down(self) -> None:
        from mevo_companion.protocol import CameraSession

        session = CameraSession("127.0.0.1", 38000)
        captured: dict[str, object] = {}

        def fake_transact(payload, timeout):  # noqa: ANN001
            captured["payload"] = payload
            return {
                "command": "shut_down_result",
                "result": True,
                "error_code": 0,
                "error_description": "",
            }

        session._ensure_socket = lambda timeout, authorize: None  # type: ignore[method-assign]
        session._transact = fake_transact  # type: ignore[method-assign]
        session._close_socket = lambda: None  # type: ignore[method-assign]
        result = session.send_command("Shutdown")
        self.assertEqual(captured["payload"]["command"], "shut_down")
        self.assertEqual(result["command"], "shut_down_result")
        self.assertTrue(result["result"])


if __name__ == "__main__":
    unittest.main()
