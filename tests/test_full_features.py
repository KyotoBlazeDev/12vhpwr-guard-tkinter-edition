import configparser
from pathlib import Path
import subprocess
import tempfile
import queue
import threading
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import gpu_telemetry as telemetry
import settings_service as settings
import startup_manager as startup
import hwinfo_12vhpwr_guard as guard
from tray_service import TrayService


class GpuTelemetryTests(unittest.TestCase):
    XML = '''<nvidia_smi_log><gpu id="00000000:01:00.0"><product_name>GPU A</product_name>
    <pci><pci_bus_id>00000000:01:00.0</pci_bus_id></pci>
    <gpu_power_readings><instant_power_draw>0 W</instant_power_draw></gpu_power_readings>
    <voltage><graphics_volt>N/A</graphics_volt></voltage>
    <clocks><graphics_clock>247 MHz</graphics_clock></clocks>
    <fb_memory_usage><used>2048 MiB</used><total>8192 MiB</total></fb_memory_usage>
    <utilization><gpu_util>0 %</gpu_util></utilization>
    <temperature><gpu_temp>36 C</gpu_temp></temperature></gpu>
    <gpu id="00000000:02:00.0"><product_name>GPU B</product_name></gpu></nvidia_smi_log>'''

    def test_multi_gpu_identity_missing_fields_and_zero(self):
        devices = telemetry.parse_xml(self.XML)
        self.assertEqual(2, len(devices))
        self.assertNotEqual(devices[0]['bus'], devices[1]['bus'])
        metrics = devices[0]['metrics']
        self.assertEqual(0, metrics['power'])
        self.assertEqual(0, metrics['utilization'])
        self.assertEqual(2048, metrics['memory_used'])
        self.assertIsNone(metrics['core_voltage'])
        self.assertTrue(all(value is None for value in devices[1]['metrics'].values()))

    def test_missing_smi_uses_getter_fallback(self):
        with mock.patch.object(telemetry, 'find_smi', return_value=None), \
             mock.patch.object(telemetry, 'sample_nvml', return_value=['fallback']) as fallback:
            self.assertEqual(['fallback'], telemetry.sample_devices())
            fallback.assert_called_once()

    def test_nvml_fallback_binds_only_readers_and_cleans_up(self):
        calls = []
        class Function:
            def __init__(self, name):
                self.name = name
            def __call__(self, *args):
                calls.append(self.name)
                if self.name == 'nvmlDeviceGetCount_v2':
                    args[0]._obj.value = 1
                elif self.name == 'nvmlDeviceGetHandleByIndex_v2':
                    args[1]._obj.value = 1
                elif self.name == 'nvmlDeviceGetName':
                    args[1].value = b'GPU A'
                elif self.name == 'nvmlDeviceGetPciInfo_v3':
                    args[1]._obj.busId = b'00000000:01:00.0'
                elif self.name == 'nvmlDeviceGetMemoryInfo':
                    args[1]._obj.total = 8*1024**3
                    args[1]._obj.used = 2*1024**3
                elif self.name == 'nvmlDeviceGetPowerUsage':
                    args[1]._obj.value = 0
                elif self.name == 'nvmlDeviceGetClockInfo':
                    args[2]._obj.value = 247
                elif self.name == 'nvmlDeviceGetTemperature':
                    args[2]._obj.value = 36
                elif self.name == 'nvmlDeviceGetUtilizationRates':
                    return 3  # Not supported: never synthesize load from board power.
                return 0
        class Library:
            def __getattr__(self, name):
                if name.startswith('nvmlDeviceSet'):
                    raise AssertionError('Telemetry must never bind setters')
                return Function(name)
        with mock.patch.object(telemetry.Path, 'is_file', return_value=True), \
             mock.patch.object(telemetry.c, 'WinDLL', return_value=Library()):
            device = telemetry.sample_nvml()[0]
        self.assertEqual('00000000:01:00.0', device['bus'])
        self.assertEqual(2048, device['metrics']['memory_used'])
        self.assertEqual(0, device['metrics']['power'])
        self.assertIsNone(device['metrics']['utilization'])
        self.assertEqual('nvmlShutdown', calls[-1])

    def test_worker_clears_failed_and_paused_samples(self):
        monitor = telemetry.GpuMonitor()
        monitor.state = dict(sample_time=1, devices=['stale'])
        stop = threading.Event()
        with mock.patch.object(telemetry, 'sample_devices', side_effect=RuntimeError('GPU lost')), \
             mock.patch.object(stop, 'wait', side_effect=lambda _: stop.set()):
            monitor.run(stop, lambda: False)
        self.assertEqual([], monitor.snapshot()['devices'])
        self.assertIsNone(monitor.snapshot()['sample_time'])
        self.assertEqual('GPU lost', monitor.snapshot()['error'])
        stop.clear()
        with mock.patch.object(telemetry, 'sample_devices') as query, \
             mock.patch.object(stop, 'wait', side_effect=lambda _: stop.set()):
            monitor.run(stop, lambda: True)
            query.assert_not_called()

    def test_freshness_rejects_future_and_stale(self):
        state = dict(sample_time=10, devices=['device'])
        self.assertEqual(['device'], telemetry.fresh_devices(state, now=11))
        for now in (9, 20):
            self.assertEqual([], telemetry.fresh_devices(state, now=now))
        self.assertEqual([], telemetry.fresh_devices(state, False, now=11))
        for value in ('nan', 'inf', '-1 W', 'N/A', '4294967295 W'):
            self.assertIsNone(telemetry.number(value, 3000))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.original = settings.current()
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.path = Path(self.temp.name) / 'config.ini'

    def tearDown(self):
        guard.set_config_values(self.original['threshold_amps'], self.original['sustained_seconds_required'])
        guard.set_response_mode(self.original['response_mode'])
        self.temp.cleanup()

    def test_atomic_save_preserves_unknown_keys_and_defers_advanced_settings(self):
        self.path.write_text('[Settings]\ncustom = keep\n', encoding='utf-8')
        values = dict(self.original, threshold_amps=8, critical_seconds=2, sensor_backend='hwinfo')
        before_tiers, before_backend = guard.get_tier_values(), guard.SENSOR_BACKEND
        settings.apply(values, self.path)
        parsed = configparser.ConfigParser()
        parsed.read(self.path)
        self.assertEqual('keep', parsed['Settings']['custom'])
        self.assertEqual('hwinfo', parsed['Settings']['sensor_backend'])
        self.assertEqual(8, guard.get_config_values()[0])
        self.assertEqual(before_tiers, guard.get_tier_values())
        self.assertEqual(before_backend, guard.SENSOR_BACKEND)

    def test_failed_save_does_not_change_live_settings(self):
        with mock.patch.object(settings.os, 'replace', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                settings.apply(dict(self.original, threshold_amps=8), self.path)
        self.assertEqual(self.original['threshold_amps'], guard.get_config_values()[0])
        self.assertFalse(self.path.with_suffix('.ini.tmp').exists())

    def test_invalid_or_reordered_thresholds_rejected_before_write(self):
        for overrides in (dict(threshold_amps=float('nan')), dict(critical_amps=5),
                          dict(catastrophic_amps=31), dict(sensor_backend='guessed')):
            with self.assertRaises(ValueError):
                settings.apply(dict(self.original, **overrides), self.path)
        self.assertFalse(self.path.exists())


class StartupTests(unittest.TestCase):
    def test_task_xml_quotes_paths_and_sets_watchdog_without_runtime_limit(self):
        xml = startup.task_xml(r'C:\A & B\pythonw.exe', r'C:\A & B\guard_tk.py', 'DOMAIN\\User', True)
        root = ET.fromstring(xml)
        ns = {'t': startup.NS}
        self.assertEqual('PT0S', root.findtext('t:Settings/t:ExecutionTimeLimit', namespaces=ns))
        self.assertEqual('999', root.findtext('t:Settings/t:RestartOnFailure/t:Count', namespaces=ns))
        self.assertEqual('PT1M', root.findtext('t:Settings/t:RestartOnFailure/t:Interval', namespaces=ns))
        self.assertEqual('false', root.findtext('t:Settings/t:StopIfGoingOnBatteries', namespaces=ns))
        self.assertEqual('HighestAvailable', root.findtext('t:Principals/t:Principal/t:RunLevel', namespaces=ns))
        self.assertEqual('"C:\\A & B\\guard_tk.py" --tray', root.findtext('t:Actions/t:Exec/t:Arguments', namespaces=ns))

    def test_other_installation_task_is_never_changed(self):
        with mock.patch.object(startup, 'status', return_value='Different installation owns this task'), \
             mock.patch.object(startup, 'run') as run:
            for action in ('enable', 'disable', 'remove'):
                with self.assertRaises(RuntimeError):
                    startup.change(action)
            with self.assertRaises(RuntimeError):
                startup.install()
            run.assert_not_called()

    def test_task_change_is_verified(self):
        with mock.patch.object(startup, 'status', side_effect=['Enabled', 'Disabled']), \
             mock.patch.object(startup, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual('Disabled', startup.change('disable'))
            self.assertIn(startup.TASK_NAME, run.call_args.args)


class TrayTests(unittest.TestCase):
    def test_shell_failure_restores_dashboard_instead_of_breaking_refresh(self):
        commands = queue.Queue()
        tray = TrayService(commands)
        class BrokenIcon:
            @property
            def title(self):
                return ''
            @title.setter
            def title(self, value):
                raise OSError('shell unavailable')
        tray.icon = BrokenIcon()
        tray.thread = mock.Mock()
        tray.thread.is_alive.return_value = True
        tray.update('LIVE')
        self.assertEqual('shell unavailable', tray.error)
        self.assertEqual('show', commands.get_nowait())
        self.assertFalse(tray.available())


if __name__ == '__main__':
    unittest.main()
