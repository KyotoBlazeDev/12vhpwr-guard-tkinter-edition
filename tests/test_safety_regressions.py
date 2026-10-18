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


class CompletePinCoverageTests(unittest.TestCase):
    def entries(self):
        result = []
        for label in guard.PIN_LABELS:
            entry = guard.HWiNFOEntry()
            entry.name_original = label.encode()
            entry.unit = b"A"
            entry.value = 0
            result.append(entry)
        return result

    def test_complete_zero_sample_is_valid_and_ordered(self):
        pins = guard.find_pin_currents(list(reversed(self.entries())))
        self.assertEqual(list(guard.PIN_LABELS), [p[0] for p in pins])
        self.assertTrue(all(p[1] == 0 for p in pins))

    def test_missing_duplicate_and_wrong_units_are_rejected(self):
        entries = self.entries()
        for sample in (entries[:1], entries[:-1], entries + entries[:1],
                       entries[:-1] + entries[:1]):
            self.assertEqual([], guard.find_pin_currents(sample))
        entries[0].unit = b"W"
        self.assertEqual([], guard.find_pin_currents(entries))

    def test_blank_units_remain_supported(self):
        entries = self.entries()
        entries[0].unit = b""
        self.assertEqual(6, len(guard.find_pin_currents(entries)))


class GpuIdentityTests(unittest.TestCase):
    def nvml(self, ids):
        nvml = mock.Mock()
        nvml.device_count.return_value = len(ids)
        nvml.handle.side_effect = lambda i: i
        nvml.subsystem_id.side_effect = lambda h: ids[h]
        return nvml

    def test_explicit_mismatch_never_falls_back(self):
        m = gpu_mitigation.GpuMitigator()
        self.assertIsNone(m._match_gpu(self.nvml([0x1234]), 0x5678))

    def test_single_matching_gpu_and_unknown_identity(self):
        for target in (None, 0x1234):
            m = gpu_mitigation.GpuMitigator()
            self.assertEqual(0, m._match_gpu(self.nvml([0x1234]), target))

    def test_multiple_gpus_are_ambiguous_even_with_unique_model(self):
        for ids in ([0x1234, 0x1234], [0x1234, 0x5678]):
            m = gpu_mitigation.GpuMitigator()
            self.assertIsNone(m._match_gpu(self.nvml(ids), 0x1234))


class AcquisitionRecoveryTests(unittest.TestCase):
    def test_failed_acquisition_resets_clean_window_before_next_evaluation(self):
        import logging
        import threading
        from contextlib import ExitStack
        failures = ([], guard.TransientReadError("bad sample"),
                    guard.SensorGoneError("disconnected"), RuntimeError("query failed"))
        for failure in failures:
            with self.subTest(failure=failure), ExitStack() as stack:
                stop = threading.Event()
                backend = mock.Mock()
                backend.tag = 'test'
                backend.degraded = None
                backend.sensor = None
                calls = []
                def read():
                    if not calls:
                        calls.append(1)
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                    stop.set()
                    return [(label, 0.0, "A") for label in guard.PIN_LABELS]
                backend.read_pins.side_effect = read
                response = mock.Mock()
                response.engaged = True
                response.clear_since = 1.0
                def evaluate(**kwargs):
                    self.assertIsNone(response.clear_since)
                    return None
                response.evaluate.side_effect = evaluate
                for name in ('eventlog_register_source', 'run_startup_recovery',
                             'announce_last_shutdown', 'toast', 'CrossCheck', 'FlightRecorder'):
                    stack.enter_context(mock.patch.object(guard, name))
                stack.enter_context(mock.patch.object(guard, 'HAVE_EVENTLOG', False))
                stack.enter_context(mock.patch.object(guard, 'get_state', return_value={'paused': False}))
                stack.enter_context(mock.patch.object(guard, 'set_state'))
                stack.enter_context(mock.patch.object(guard, 'candidate_backends', return_value=[mock.Mock(key='test', return_value=backend)]))
                stack.enter_context(mock.patch.object(guard, 'TieredResponse', return_value=response))
                stack.enter_context(mock.patch.object(guard.time, 'sleep'))
                guard.monitor_loop(stop, logging.getLogger('regression'))
                response.evaluate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
