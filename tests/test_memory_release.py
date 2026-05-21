import types
import unittest
from unittest.mock import Mock, patch

from app import memory


class MemoryReleaseTest(unittest.TestCase):
    def test_force_release_bypasses_interval_throttle(self):
        original_last_trim = memory._memory_trim_runtime.get("last_trim_ts", 0.0)
        memory._memory_trim_runtime["last_trim_ts"] = 100.0
        fake_libc = types.SimpleNamespace(malloc_trim=Mock())

        try:
            with patch.object(memory, "MEMORY_TRIM_ENABLED", True), patch.object(
                memory, "MEMORY_TRIM_MIN_INTERVAL_SECONDS", 60
            ), patch("app.memory.time.monotonic", return_value=120.0), patch(
                "app.memory.gc.collect"
            ) as gc_collect, patch.object(
                memory.os, "name", "posix"
            ), patch.object(
                memory.os, "uname", return_value=types.SimpleNamespace(sysname="Linux")
            ), patch(
                "app.memory.ctypes.CDLL", return_value=fake_libc
            ):
                released = memory.release_process_memory("tree-sync", force=True)

            self.assertTrue(released)
            gc_collect.assert_called_once()
            fake_libc.malloc_trim.assert_called_once_with(0)
        finally:
            memory._memory_trim_runtime["last_trim_ts"] = original_last_trim


if __name__ == "__main__":
    unittest.main()
