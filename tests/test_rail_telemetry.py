import threading
import unittest
from unittest import mock

import guard_tk
import rail_telemetry as rails


class RailTelemetryTests(unittest.TestCase):
    def test_channels_are_matched_by_masks_and_keep_identity(self):
        info, status = rails.MonitorInfo(), rails.MonitorStatus()
        info.mask = (1 << 2) | (1 << 4)
        status.mask = (1 << 2) | (1 << 3)
        info.channels[2].rail = 250
        info.channels[2].kind = 8
        status.readings[2].power = 5977
        status.readings[2].voltage = 12173000
        status.readings[2].current = 491
        rows = rails.decode_readings(info, status)
        self.assertEqual(1, len(rows))
        self.assertEqual(('8-pin #1 input', 5.977, 12.173, 0.491),
                         tuple(rows[0][key] for key in ('label', 'watts', 'volts', 'amps')))
        self.assertEqual('aligned sensor', rows[0]['kind'])

    def test_estimated_and_summed_voltage_is_not_a_live_sensor(self):
        info, status = rails.MonitorInfo(), rails.MonitorStatus()
        info.mask = status.mask = 1
        for kind in (1, 2, 7):
            info.channels[0].kind = kind
            status.readings[0].voltage = 3300
            row = rails.decode_readings(info, status)[0]
            self.assertIsNone(row['volts'])
            self.assertIsNone(row['amps'])
        info.channels[0].kind = 8
        info.channels[0].rail = 999
        status.readings[0].power = status.readings[0].current = status.readings[0].voltage = 0xFFFFFFFF
        row = rails.decode_readings(info, status)[0]
        self.assertEqual('Unknown rail (999)', row['label'])
        self.assertTrue(all(row[key] is None for key in ('watts', 'volts', 'amps')))

    def test_pause_failure_and_cleanup_clear_rail_snapshot(self):
        monitor, stop = rails.RailMonitor(), threading.Event()
        monitor.state = dict(sample_time=1, devices=[{'old': True}], error=None)
        backend = mock.Mock()
        backend.connect.side_effect = RuntimeError('Unavailable through backend')
        with mock.patch.object(rails, 'NvidiaRails', return_value=backend), \
             mock.patch.object(stop, 'wait', side_effect=lambda _: stop.set()):
            monitor.run(stop, lambda: False)
        self.assertEqual([], monitor.snapshot()['devices'])
        self.assertIsNone(monitor.snapshot()['sample_time'])
        backend.close.assert_called_once()
        backend.sample.assert_not_called()
        stop.clear()
        with mock.patch.object(rails, 'NvidiaRails') as constructor, \
             mock.patch.object(stop, 'wait', side_effect=lambda _: stop.set()):
            monitor.run(stop, lambda: True)
        constructor.assert_not_called()
        self.assertEqual('Paused', monitor.snapshot()['error'])

    def test_stale_paused_and_failed_data_cannot_appear_live(self):
        state = dict(sample_time=10, devices=[dict(name='GPU A', bus=1, source='NVAPI',
                     readings=[dict(channel=2, label='8-pin #1', kind='sensor', watts=0,
                                    volts=12, amps=0)])])
        self.assertTrue(guard_tk.rail_display(state, True, now=11)[0])
        for active, now in ((False, 11), (True, 20), (True, 9)):
            live, text = guard_tk.rail_display(state, active, now=now)
            self.assertFalse(live)
            self.assertNotIn('12.000 V', text)
        self.assertFalse(guard_tk.rail_display(dict(sample_time=10, devices=[
            dict(name='GPU B', bus=2, source='NVAPI', error='Not supported', readings=[])]),
            True, now=11)[0])


if __name__ == '__main__':
    unittest.main()
