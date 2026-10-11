import sys
import unittest

from mevo_companion.__main__ import _ensure_stdio
from uvicorn.logging import ColourizedFormatter


class StdioTests(unittest.TestCase):
    def test_pythonw_stdio_survives_uvicorn_formatter(self) -> None:
        old_out, old_err = sys.stdout, sys.stderr
        sink = None
        try:
            sys.stdout = None
            sys.stderr = None
            _ensure_stdio()
            sink = sys.stdout
            self.assertIsNotNone(sys.stdout)
            self.assertIs(sys.stderr, sys.stdout)
            assert sys.stdout is not None
            self.assertFalse(sys.stdout.isatty())
            ColourizedFormatter()
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
            if sink is not None:
                sink.close()


if __name__ == "__main__":
    unittest.main()
