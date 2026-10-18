import time
import tkinter as tk
import unittest
import json
from pathlib import Path
import tempfile
from unittest import mock

import guard_tk
import astral_i2c
import hwinfo_12vhpwr_guard as guard


class DashboardTests(unittest.TestCase):
    def test_detection_distinguishes_no_model_from_failed_sensor_read(self):
        sensor = astral_i2c.AstralPinSensor()
        with mock.patch.object(sensor, '_load'), mock.patch.object(sensor, '_find_astral', return_value=(None, None, None)):
            with self.assertRaises(astral_i2c.NoCompatibleHardwareError):
                sensor.connect()
        with mock.patch.object(sensor, '_load'), mock.patch.object(sensor, '_find_astral', return_value=(1, 'GPU', 'Astral')):
            with mock.patch.object(sensor, 'read_pins', side_effect=astral_i2c.TransientReadError('read failed')):
                with self.assertRaises(astral_i2c.TransientReadError):
                    sensor.connect()
        self.assertEqual('Astral', sensor.model)

    def test_acquisition_states_require_evidence(self):
        pins = [(label, 0.0, 'A') for label in guard.PIN_LABELS]
        self.assertEqual('LIVE', guard_tk.acquisition_state({'pins': pins}, True)[0])
        self.assertEqual('UNAVAILABLE', guard_tk.acquisition_state({'hardware_supported': False}, False)[0])
        self.assertEqual('ERROR', guard_tk.acquisition_state({'hardware_supported': True, 'pins': pins}, False)[0])
        self.assertEqual('WAITING', guard_tk.acquisition_state({}, False)[0])
        self.assertEqual('ERROR', guard_tk.acquisition_state({'hardware_supported': True, 'pins': pins[:1]}, True)[0])
        pins[0] = (guard.PIN_LABELS[0], float('nan'), 'A')
        self.assertNotEqual('LIVE', guard_tk.acquisition_state({'pins': pins}, True)[0])

    def setUp(self):
        self.saved = guard.get_state()
        self.dialog = guard.dialog
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = guard_tk.GuardWindow(self.root, preview=True, ui_style='TTK')

    def tearDown(self):
        self.root.destroy()
        with guard.state_lock:
            guard.shared.clear()
            guard.shared.update(self.saved)
        guard.dialog = self.dialog

    def test_preview_does_not_start_monitor(self):
        with mock.patch.object(guard, 'monitor_loop') as monitor, \
             mock.patch.object(self.app.gpu_monitor, 'run') as gpu, \
             mock.patch.object(self.app.rail_monitor, 'run') as rails, \
             mock.patch.object(self.app.tray, 'start') as tray:
            self.app.start()
            self.root.update()
            monitor.assert_not_called()
            gpu.assert_not_called()
            rails.assert_not_called()
            tray.assert_not_called()
        self.assertIn('PREVIEW', self.app.status.cget('text'))

    def test_all_windows_and_overlay_work_in_both_styles(self):
        errors = []
        self.root.report_callback_exception = lambda *args: errors.append(args)
        self.root.deiconify()
        self.root.geometry('800x540')
        for style in ('TTK', 'Tk Classic'):
            if style != self.app.ui_style:
                self.app.ui_choice.set(style)
                self.app.switch_ui()
            self.app.show_settings()
            self.app.show_display_settings()
            self.app.show_about()
            self.app.show_warnings()
            self.app.show_diagnostics()
            with mock.patch.object(self.app, 'startup_action'):
                self.app.show_startup()
            self.app.show_overlay()
            self.app.refresh()
            self.root.update()
            self.assertTrue(self.app.overlay.canvas.find_all())
            self.assertTrue(self.app.viewport.cget('scrollregion'))
        self.assertFalse(errors, errors)

    def test_gpu_metrics_are_separate_and_clear_when_stale(self):
        self.app.preview = False
        guard.set_state(status=guard.Status.ERROR, paused=False, hardware_supported=False)
        self.app.gpu_monitor.state = dict(sample_time=time.monotonic(), error=None, devices=[
            dict(name='GPU A', bus='00000000:01:00.0', source='NVML',
                 metrics=dict(power=0, clock=247, memory_used=2048, memory_total=8192,
                              utilization=0, temperature=36, core_voltage=None))])
        self.app.refresh()
        self.assertIn('TELEMETRY', self.app.status.cget('text'))
        text = self.app.metric_text.cget('text')
        self.assertIn('Board power: 0 W', text)
        self.assertIn('2.00 / 8.00 GiB', text)
        self.assertTrue(all(widget.cget('text') == '— A' for widget in self.app.readings))
        self.app.gpu_monitor.state['sample_time'] -= 20
        self.app.refresh()
        self.assertNotIn('247', self.app.metric_text.cget('text'))
        self.assertIsNone(self.app.selected_gpu)

    def test_unexpected_monitor_failure_reaches_watchdog_signal(self):
        with mock.patch.object(guard, 'monitor_loop', side_effect=RuntimeError('failed')):
            self.app.monitor(mock.Mock())
        self.assertTrue(self.app.monitor_failed.is_set())
        self.assertEqual(guard.Status.ERROR, guard.get_state()['status'])

    def test_diagnostics_export_is_local_json(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder) / 'diagnostics.json'
            with mock.patch.object(guard_tk.filedialog, 'asksaveasfilename', return_value=str(path)):
                self.app.export_diagnostics()
            report = json.loads(path.read_text(encoding='utf-8'))
            self.assertTrue(report['preview'])
            self.assertIn('rails', report)
            self.assertIn('gpu', report)
            self.assertIn('pending_settings', report)

    def test_switch_uses_classic_widgets_and_preserves_monitor_state(self):
        worker = object()
        self.app.worker = worker
        stop_event = self.app.stop_event
        before = guard.get_state()
        self.app.show_voltage.set(False)
        self.app.ui_choice.set('Tk Classic')
        self.app.switch_ui()
        self.root.update()
        self.assertEqual('Button', self.app.pause.winfo_class())
        self.assertEqual('Scale', self.app.scale_slider.winfo_class())
        self.assertEqual('Canvas', self.app.gauges[0].winfo_class())
        self.assertIs(worker, self.app.worker)
        self.assertIs(stop_event, self.app.stop_event)
        self.assertFalse(stop_event.is_set())
        self.assertFalse(self.app.show_voltage.get())
        self.assertEqual(before, guard.get_state())
        self.app.ui_choice.set('TTK')
        self.app.switch_ui()
        self.assertEqual('TButton', self.app.pause.winfo_class())

    def test_fresh_zero_and_stale_readings(self):
        self.app.preview = False
        guard.set_state(status=guard.Status.OK, paused=False,
                        pins=[(guard.PIN_LABELS[0], 0.0, 'A')], sample_time=time.monotonic())
        self.app.refresh()
        self.assertEqual('0.00 A', self.app.readings[0].cget('text'))
        self.assertEqual('— A', self.app.readings[1].cget('text'))
        guard.set_state(sample_time=time.monotonic() - 10)
        self.app.refresh()
        self.assertEqual('— A', self.app.readings[0].cget('text'))

    def test_source_loss_clears_snapshot(self):
        guard.set_state(pins=[(guard.PIN_LABELS[0], 5.0, 'A')], sample_time=time.monotonic())
        guard.set_state(status=guard.Status.ERROR)
        self.assertEqual([], guard.get_state()['pins'])
        self.assertIsNone(guard.get_state()['sample_time'])

    def test_rail_telemetry_does_not_fill_pin_gauges_or_enable_protection(self):
        self.app.preview = False
        guard.set_state(status=guard.Status.ERROR, paused=False, hardware_supported=False)
        self.app.rail_monitor.state = dict(sample_time=time.monotonic(), error=None, devices=[
            dict(name='RTX 5060', bus=1, source='NVAPI', readings=[
                dict(channel=2, label='8-pin #1 input', kind='aligned sensor',
                     watts=5.9, volts=12.1, amps=0.48)])])
        self.app.refresh()
        self.assertIn('TELEMETRY', self.app.status.cget('text'))
        self.assertTrue(all(widget.cget('text') == '— A' for widget in self.app.readings))
        self.assertEqual([], guard.get_state()['pins'])
        self.assertFalse(guard.get_state()['hardware_supported'])
        self.assertIn('5.900 W', self.app.rail_text.get('1.0', 'end'))
        self.app.rail_monitor.state['sample_time'] -= 10
        self.app.refresh()
        self.assertNotIn('5.900 W', self.app.rail_text.get('1.0', 'end'))
        self.assertNotIn('TELEMETRY', self.app.status.cget('text'))

    def test_display_scale_follows_model_without_changing_threshold(self):
        threshold = guard.get_config_values()
        guard.set_state(gpu_model='Astral 5080')
        self.app.refresh()
        self.app.change_display_scale(24)
        guard.set_state(gpu_model='Astral 5090')
        self.app.refresh()
        self.assertEqual(20, self.app.scale_value.get())
        guard.set_state(gpu_model='Astral 5080')
        self.app.refresh()
        self.assertEqual(24, self.app.scale_value.get())
        self.assertEqual(threshold, guard.get_config_values())

    def test_voltage_control_requires_fresh_reported_voltage(self):
        self.app.preview = False
        guard.set_state(status=guard.Status.OK, paused=False, sample_time=time.monotonic(),
                        pins=[(guard.PIN_LABELS[0], 1.0, 'A')], pin_voltages=[12.1]*6)
        self.app.refresh()
        self.assertEqual('12.10 V', self.app.voltage_labels[0].cget('text'))
        guard.set_state(status=guard.Status.ERROR)
        self.app.refresh()
        self.assertEqual('', self.app.voltage_labels[0].cget('text'))
        self.assertIn('disabled', self.app.voltage_toggle.state())

    def test_unavailable_mitigation_shows_effective_shutdown(self):
        guard.set_state(mitigation='administrator rights required')
        with mock.patch.object(guard, 'get_response_mode', return_value=guard.RESPONSE_TIERED):
            self.app.refresh()
        self.assertIn('Effective: shutdown_only', self.app.details.cget('text'))

    def test_warning_window_contains_original_disclaimer_and_license(self):
        self.app.show_warnings()
        window = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))
        frame = next(widget for widget in window.winfo_children() if isinstance(widget, guard_tk.ttk.Frame))
        text = next(widget for widget in frame.winfo_children() if isinstance(widget, tk.Text))
        content = text.get('1.0', 'end')
        self.assertIn('GC-HPWR', content)
        self.assertIn('manual clock lock', content)
        self.assertIn('## Disclaimer & Legal Notice', content)
        self.assertIn('MIT License', content)
        self.assertEqual('disabled', text.cget('state'))
