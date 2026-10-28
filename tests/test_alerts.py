"""高负载规则及托盘扩展测试。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from perfmon.alerts import AlertRules
from perfmon.ext_alerts import setup


def config(**changes):
    result = {
        "enabled": True, "cpu": 90, "mem": 90, "gpu": 95, "temp": 90,
        "disk_free_gb": 10, "sustain_seconds": 30, "cooldown_minutes": 10,
    }
    result.update(changes)
    return result


def snapshot(cpu=0, mem=0, gpu=None, temp=None, disks=None):
    return {
        "cpu": {"percent": cpu}, "mem": {"percent": mem},
        "gpu": [] if gpu is None else [{"name": "显卡", "util": gpu}],
        "sensors": [] if temp is None else [{"kind": "temp", "name": "CPU Package", "value": temp}],
        "disk": {"volumes": disks or []},
        "procs": {"cpu": [{"name": "chrome", "cpu": 35}]},
    }


def volume(mount, free_gb):
    return {"mount": mount, "total": 100 * 2**30, "used": (100 - free_gb) * 2**30}


class AlertRulesTest(unittest.TestCase):
    def test_cpu_requires_continuous_duration_and_only_fires_once(self):
        rules = AlertRules(config())
        high = snapshot(cpu=91)
        self.assertEqual(rules.feed(high, 0), [])
        self.assertEqual(rules.feed(high, 29), [])
        alerts = rules.feed(high, 30)
        self.assertEqual([a.key for a in alerts], ["cpu"])
        self.assertIn("chrome（35%）", alerts[0].message)
        self.assertEqual(rules.feed(high, 31), [])
        self.assertEqual(rules.feed(high, 631), [])

    def test_interrupted_sustain_and_recovery_restart_timing(self):
        rules = AlertRules(config(cooldown_minutes=0))
        high = snapshot(cpu=95)
        self.assertEqual(rules.feed(high, 0), [])
        self.assertEqual(rules.feed(snapshot(cpu=89), 20), [])
        self.assertEqual(rules.feed(high, 21), [])
        self.assertEqual(rules.feed(high, 50), [])
        self.assertEqual([a.key for a in rules.feed(high, 51)], ["cpu"])
        # 处于阈值和恢复线之间时，不重新提醒。
        self.assertEqual(rules.feed(snapshot(cpu=88), 60), [])
        self.assertEqual(rules.feed(high, 61), [])
        self.assertEqual(rules.feed(snapshot(cpu=84), 70), [])
        self.assertEqual(rules.feed(high, 71), [])
        self.assertEqual([a.key for a in rules.feed(high, 101)], ["cpu"])

    def test_recovery_during_cooldown_waits_until_cooldown_ends(self):
        rules = AlertRules(config())
        high = snapshot(cpu=95)
        rules.feed(high, 0)
        self.assertEqual([a.key for a in rules.feed(high, 30)], ["cpu"])
        rules.feed(snapshot(cpu=84), 40)
        rules.feed(high, 41)
        self.assertEqual(rules.feed(high, 71), [])
        self.assertEqual(rules.feed(high, 629), [])
        self.assertEqual([a.key for a in rules.feed(high, 630)], ["cpu"])

    def test_memory_gpu_and_any_temperature_sensor(self):
        rules = AlertRules(config())
        sample = snapshot(mem=91, gpu=96, temp=92)
        sample["sensors"].insert(0, {"kind": "temp", "name": "SSD", "value": 50})
        self.assertEqual(rules.feed(sample, 0), [])
        alerts = rules.feed(sample, 30)
        self.assertEqual({a.key for a in alerts}, {"mem", "gpu", "temp"})
        self.assertIn("CPU Package", next(a.message for a in alerts if a.key == "temp"))

    def test_disk_is_immediate_and_requires_space_recovery(self):
        rules = AlertRules(config(cooldown_minutes=0))
        low = snapshot(disks=[volume("C:\\", 40), volume("D:\\", 8.2)])
        alerts = rules.feed(low, 0)
        self.assertEqual([a.key for a in alerts], ["disk"])
        self.assertIn("D: 只剩 8.2 GB", alerts[0].message)
        self.assertEqual(rules.feed(low, 100), [])
        self.assertEqual(rules.feed(snapshot(disks=[volume("D:\\", 12)]), 101), [])
        self.assertEqual(rules.feed(low, 102), [])
        rules.feed(snapshot(disks=[volume("D:\\", 16)]), 103)
        self.assertEqual([a.key for a in rules.feed(low, 104)], ["disk"])

    def test_removable_drive_is_ignored(self):
        rules = AlertRules(config(cooldown_minutes=0))
        usb = dict(volume("F:\\", 1), removable=True)
        self.assertEqual(rules.feed(snapshot(disks=[volume("C:\\", 40), usb]), 0), [])

    def test_disk_recovery_still_respects_cooldown(self):
        rules = AlertRules(config())
        low = snapshot(disks=[volume("D:\\", 8)])
        self.assertEqual([a.key for a in rules.feed(low, 0)], ["disk"])
        rules.feed(snapshot(disks=[volume("D:\\", 16)]), 1)
        self.assertEqual(rules.feed(low, 2), [])
        self.assertEqual([a.key for a in rules.feed(low, 600)], ["disk"])


class AlertsExtensionTest(unittest.TestCase):
    def test_menu_toggle_persists_configuration(self):
        app = SimpleNamespace(cfg={"alerts": config(enabled=False)}, config_path="config.json", tray=Mock())
        extension = setup(app)
        label, toggle, checked = extension.menu_items()[0]
        self.assertEqual(label, "高负载提醒")
        self.assertFalse(checked())
        with patch("perfmon.app.save_json") as save_json:
            toggle()
        self.assertTrue(checked())
        save_json.assert_called_once_with("config.json", app.cfg)
        app.tray.update_menu.assert_called_once_with()

    def test_worker_skips_missing_tray_and_disabled_samples(self):
        app = SimpleNamespace(cfg={"alerts": config(cpu=10, sustain_seconds=0)},
                              tray=None, stopping=False, collector=Mock())
        extension = setup(app)
        tray = Mock()
        sample = snapshot(cpu=50)
        calls = 0

        def wait_next(seq, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return 1, sample
            if calls == 2:
                app.tray = tray
                return 2, sample
            if calls == 3:
                app.cfg["alerts"]["enabled"] = False
                return 3, sample
            app.stopping = True
            return seq, sample

        app.collector.wait_next.side_effect = wait_next
        extension._run()
        tray.notify.assert_called_once()
        self.assertEqual(app.collector.wait_next.call_count, 4)

    def test_worker_logs_error_and_continues(self):
        app = SimpleNamespace(cfg={"alerts": config(cpu=10, sustain_seconds=0)},
                              tray=Mock(), stopping=False, collector=Mock())
        extension = setup(app)
        calls = 0

        def wait_next(seq, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("测试采样失败")
            if calls == 2:
                return 1, snapshot(cpu=50)
            app.stopping = True
            return seq, None

        app.collector.wait_next.side_effect = wait_next
        with patch.object(extension._stop, "wait", return_value=False), \
             self.assertLogs("perfmon.ext_alerts", level="ERROR") as logged:
            extension._run()
        self.assertIn("高负载提醒线程出错", logged.output[0])
        app.tray.notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
