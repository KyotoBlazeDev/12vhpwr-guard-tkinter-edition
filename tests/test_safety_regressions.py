import configparser
import math
import unittest
from unittest import mock

import gpu_mitigation
import hwinfo_12vhpwr_guard as guard


class SensorValidationTests(unittest.TestCase):
    def _hwinfo_entry(self, value):
        entry = guard.HWiNFOEntry()
        entry.name_original = b"GPU 12VHPWR Pin1 Current"
        entry.unit = b"A"
        entry.value = value
        return entry

    def test_hwinfo_discards_implausibly_high_current(self):
        pins = guard.find_pin_currents([self._hwinfo_entry(65.535)])
        self.assertEqual([], pins)

    def test_hwinfo_discards_non_finite_current(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                pins = guard.find_pin_currents([self._hwinfo_entry(value)])
                self.assertEqual([], pins)


class ConfigurationValidationTests(unittest.TestCase):
    def test_non_finite_safety_settings_fall_back_to_defaults(self):
        parsed = configparser.ConfigParser()
        parsed.read_string(
            "[Settings]\n"
            "threshold_amps = nan\n"
            "sustained_seconds_required = inf\n"
            "critical_amps = inf\n"
            "catastrophic_amps = inf\n"
        )
        parsed.read = mock.Mock(return_value=[])

        with mock.patch.object(guard.os.path, "exists", return_value=True):
            with mock.patch.object(guard.configparser, "ConfigParser", return_value=parsed):
                loaded = guard.load_config()

        self.assertTrue(math.isfinite(loaded["threshold_amps"]))
        self.assertTrue(math.isfinite(loaded["sustained_seconds_required"]))
        self.assertTrue(math.isfinite(loaded["critical_amps"]))
        self.assertTrue(math.isfinite(loaded["catastrophic_amps"]))
        self.assertEqual(guard.DEFAULT_THRESHOLD_AMPS, loaded["threshold_amps"])
        self.assertEqual(
            guard.DEFAULT_SUSTAINED_SECONDS_REQUIRED,
            loaded["sustained_seconds_required"],
        )


class RecoveryMarkerTests(unittest.TestCase):
    class FailingNvml:
        def reset_locked_clocks(self, _handle):
            raise RuntimeError("temporary driver failure")

        def set_power_limit_mw(self, _handle, _limit):
            raise RuntimeError("temporary driver failure")

    def test_failed_startup_recovery_keeps_marker_for_retry(self):
        mitigator = gpu_mitigation.GpuMitigator(marker_path="unused-in-test")
        mitigator._read_marker = mock.Mock(
            return_value={
                "reason": "test",
                "subsystem_id": 0x89E31043,
                "saved_power_limit_mw": 500_000,
                "applied_power_limit": True,
                "applied_clocks": True,
            }
        )
        mitigator._clear_marker = mock.Mock()
        mitigator._nvml = self.FailingNvml()
        mitigator._handle = object()
        mitigator.connect = lambda _subsystem_id=None: True

        result = mitigator.startup_recovery()

        self.assertIn("failed", result)
        mitigator._clear_marker.assert_not_called()

    def test_unavailable_gpu_keeps_startup_marker_for_retry(self):
        mitigator = gpu_mitigation.GpuMitigator(marker_path="unused-in-test")
        mitigator._read_marker = mock.Mock(
            return_value={"reason": "test", "subsystem_id": 0x89E31043}
        )
        mitigator._clear_marker = mock.Mock()
        mitigator.connect = lambda _subsystem_id=None: False

        result = mitigator.startup_recovery()

        self.assertIn("cannot reach the GPU", result)
        mitigator._clear_marker.assert_not_called()

    def test_failed_live_release_keeps_marker_for_startup_recovery(self):
        mitigator = gpu_mitigation.GpuMitigator(marker_path="unused-in-test")
        mitigator._clear_marker = mock.Mock()
        mitigator._nvml = self.FailingNvml()
        mitigator._handle = object()
        mitigator.engaged = True
        mitigator._applied_clocks = True
        mitigator._applied_power = True
        mitigator._saved_power_limit_mw = 500_000

        restored = mitigator.release()

        self.assertFalse(restored)
        mitigator._clear_marker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
