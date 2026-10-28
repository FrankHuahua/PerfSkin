"""核心模块的冒烟测试：采集一次、格式正确。"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perfmon.app import DEFAULT_CONFIG  # noqa: E402
from perfmon.collector import HISTORY_FIELDS, DemoCollector  # noqa: E402


class CollectorTest(unittest.TestCase):
    def test_demo_sample(self):
        c = DemoCollector(dict(DEFAULT_CONFIG), demo=True)
        c.sample()
        time.sleep(0.2)
        s = c.sample()
        for k in ("cpu", "mem", "gpu", "net", "disk", "procs", "sensors", "h"):
            self.assertIn(k, s)
        self.assertTrue(0 <= s["cpu"]["percent"] <= 100)
        self.assertEqual(set(s["h"]), set(HISTORY_FIELDS))
        c.stop()


if __name__ == "__main__":
    unittest.main()
