import os
import unittest
from unittest.mock import patch

from mevo_companion.singleton import AlreadyRunning, InstanceLock


class InstanceLockTests(unittest.TestCase):
    def _key(self, suffix: str) -> str:
        return f"MevoCompanionTest-{os.getpid()}-{suffix}"

    def test_second_acquire_is_rejected(self) -> None:
        key = self._key("second")
        with InstanceLock(key):
            with self.assertRaises(AlreadyRunning):
                with InstanceLock(key):
                    pass

    def test_release_allows_another_instance(self) -> None:
        key = self._key("release")
        first = InstanceLock(key)
        first.acquire()
        first.release()
        second = InstanceLock(key)
        second.acquire()
        second.release()

    def test_distinct_keys_can_run_together(self) -> None:
        first = InstanceLock(self._key("a"))
        second = InstanceLock(self._key("b"))
        first.acquire()
        try:
            second.acquire()
        finally:
            second.release()
            first.release()

    def test_main_exits_when_another_instance_holds_the_lock(self) -> None:
        from mevo_companion.__main__ import main

        with patch("mevo_companion.__main__.InstanceLock") as lock_cls:
            lock_cls.return_value.acquire.side_effect = AlreadyRunning()
            with patch("mevo_companion.__main__.sys.stderr"):
                self.assertEqual(main(["--no-window"]), 1)
            lock_cls.return_value.release.assert_not_called()
