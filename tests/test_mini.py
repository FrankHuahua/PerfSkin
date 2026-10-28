"""迷你悬浮窗入口及 Windows 置顶行为测试。"""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from perfmon import ext_mini


class MiniExtensionTest(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            cfg={"mini": {"size": [340, 176], "topmost": True}},
            features=set(), stopping=False, open_window=Mock(),
        )
        self.extension = ext_mini.setup(self.app)

    def test_feature_menu_and_action(self):
        self.assertIn("mini", self.app.features)
        self.assertEqual(self.extension.menu_items()[0][0], "迷你悬浮窗")
        self.assertFalse(self.extension.action("other", {}))
        with patch.object(ext_mini.winsys, "IS_WINDOWS", False):
            self.assertTrue(self.extension.action("open-mini", {}))
        self.app.open_window.assert_called_once_with(
            "mini.html", size=(340, 176), title="迷你监控 · PerfSkin",
        )

    def test_windows_window_is_found_and_set_topmost(self):
        finished = threading.Event()

        def set_topmost(hwnd):
            self.assertEqual(hwnd, 123)
            finished.set()

        with patch.object(ext_mini.winsys, "IS_WINDOWS", True), \
             patch.object(ext_mini.winsys, "find_window", side_effect=[None, 123]) as find, \
             patch.object(ext_mini.winsys, "set_topmost", side_effect=set_topmost) as topmost:
            self.extension.open()
            self.assertTrue(finished.wait(2), "迷你窗未在预期时间置顶")
        self.assertEqual(find.call_count, 2)
        topmost.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
