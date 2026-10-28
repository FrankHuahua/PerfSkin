"""LibreHardwareMonitor 新旧网页数据及短时故障测试。"""
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from perfmon.sensors import PROVIDERS, make_providers
from perfmon.sensors_lhm import DEFAULT_URL, LhmSensors, _group, _parse


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class LhmSensorsTest(unittest.TestCase):
    def test_direct_import_in_new_process(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "from perfmon.sensors_lhm import LhmSensors, _parse; "
             "from perfmon.sensors import SensorProvider; "
             "assert issubclass(LhmSensors, SensorProvider)"],
            cwd=FIXTURES.parent.parent, capture_output=True, text=True, timeout=5, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chip_models_do_not_match_common_words(self):
        self.assertEqual(_group("White"), "other")
        self.assertEqual(_group("Composite"), "other")
        self.assertEqual(_group("NCT6798D"), "board")
        self.assertEqual(_group("IT8688"), "board")

    def test_old_format_units_groups_and_zero_fan(self):
        result = _parse(fixture("lhm_old.json"))
        self.assertEqual(len(result), 6)
        self.assertEqual([(item["group"], item["kind"]) for item in result], [
            ("cpu", "temp"), ("cpu", "power"), ("gpu", "temp"),
            ("gpu", "fan"), ("disk", "temp"), ("board", "fan"),
        ])
        self.assertEqual(result[0], {
            "name": "CPU Package", "kind": "temp", "value": 45.0, "unit": "℃",
            "group": "cpu", "hw": "AMD Ryzen 7 7800X3D",
        })
        self.assertEqual(result[1]["value"], 35.2)
        self.assertEqual(result[1]["unit"], "W")
        self.assertEqual(result[3]["value"], 1200.0)
        self.assertEqual(result[3]["unit"], "RPM")
        self.assertEqual(result[5]["hw"], "ASUS 主板")

    def test_new_format_types_ids_and_thousands(self):
        result = _parse(fixture("lhm_new.json"))
        self.assertEqual(len(result), 6)
        self.assertEqual([item["group"] for item in result], [
            "cpu", "cpu", "gpu", "gpu", "disk", "board",
        ])
        self.assertEqual(result[0]["hw"], "Intel Core i7-14700K")
        self.assertEqual(result[3]["value"], 1250.0)
        self.assertEqual(result[5]["value"], 1180.0)
        self.assertEqual(result[5]["hw"], "Nuvoton NCT6798D")

    def test_type_takes_priority_and_other_group(self):
        tree = {"Text": "Sensor", "Children": [
            {"Text": "未知设备", "Children": [
                {"Text": "温度", "Type": "Temperature", "Value": "32.5 °C", "Children": []},
                {"Text": "负载", "Type": "Load", "Value": "44.0 °C", "Children": []},
                {"Text": "未识别", "Value": "12.0 %", "Children": []},
            ]},
        ]}
        self.assertEqual(_parse(tree), [{
            "name": "温度", "kind": "temp", "value": 32.5, "unit": "℃",
            "group": "other", "hw": "未知设备",
        }])

    def test_constructor_requests_once_with_timeout_and_custom_url(self):
        payload = json.dumps(fixture("lhm_old.json")).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)) as request:
            provider = LhmSensors({"lhm": {"url": "http://127.0.0.1:9999/data.json"}})
        request.assert_called_once_with("http://127.0.0.1:9999/data.json", timeout=0.8)
        self.assertEqual(len(provider._last), 6)

    def test_make_providers_lazily_registers_lhm(self):
        payload = json.dumps(fixture("lhm_old.json")).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)):
            providers = make_providers(["lhm"], cfg={"lhm": {"url": "http://127.0.0.1:9999/data.json"}})
        self.assertIs(PROVIDERS["lhm"], LhmSensors)
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].url, "http://127.0.0.1:9999/data.json")

    def test_unavailable_constructor_raises_and_registry_skips(self):
        with patch("urllib.request.urlopen", side_effect=URLError("未启动")) as request:
            with self.assertRaises(URLError):
                LhmSensors({})
            self.assertEqual(make_providers(["lhm"], cfg={}), [])
        self.assertEqual(request.call_args.args, (DEFAULT_URL,))

    def test_failed_reads_keep_last_four_then_clear_and_recover(self):
        payload = json.dumps(fixture("lhm_new.json")).encode("utf-8")
        with patch("urllib.request.urlopen", side_effect=[
            io.BytesIO(payload), *[URLError("暂时断开") for _ in range(5)], io.BytesIO(payload),
        ]):
            provider = LhmSensors({})
            for _ in range(4):
                self.assertEqual(len(provider.read()), 6)
            self.assertEqual(provider.read(), [])
            self.assertEqual(len(provider.read()), 6)
            self.assertEqual(provider._failures, 0)


if __name__ == "__main__":
    unittest.main()
