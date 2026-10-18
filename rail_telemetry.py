"""Read-only, display-only NVIDIA connector telemetry; never a protection source.

Private ABI reference: JustAMan/pynvraw, src/pynvraw/nvapi_api.py,
NV_POWER_MONITOR_INFO v3 and NV_POWER_MONITOR_STATUS v1.
These interfaces are not guaranteed by NVIDIA's public SDK. Unknown rails retain
their numeric IDs; no connector type or physical sensor is inferred from a name.
"""
import ctypes as c
import json
import threading
import time


class Channel(c.Structure):
    _fields_ = [(name, c.c_uint32) for name in
                ('device_mask', 'offset', 'limit', 'kind', 'rail', 'fixed_voltage',
                 'power_slope', 'current_slope', 'current_offset')] + [
                    ('reserved', c.c_uint8 * 8), ('data', c.c_uint32 * 4)]


class Relation(c.Structure):
    _fields_ = [('kind', c.c_int32), ('channel', c.c_uint8), ('data', c.c_uint32 * 8)]


class MonitorInfo(c.Structure):
    _fields_ = [('version', c.c_uint32), ('supported', c.c_bool),
                ('period', c.c_uint32), ('count', c.c_uint32),
                ('mask', c.c_uint32), ('relation_mask', c.c_uint32),
                ('total_mask', c.c_uint32), ('total_index', c.c_uint8),
                ('reserved', c.c_uint8 * 8), ('channels', Channel * 32),
                ('relations', Relation * 32)]


class Reading(c.Structure):
    _pack_ = 1
    _fields_ = [('power', c.c_uint32), ('minimum', c.c_uint32),
                ('maximum', c.c_uint32), ('current', c.c_uint32),
                ('voltage', c.c_uint32), ('energy', c.c_uint64),
                ('reserved', c.c_uint8 * 16)]


class MonitorStatus(c.Structure):
    _fields_ = [('version', c.c_uint32), ('mask', c.c_uint32),
                ('total', c.c_uint32), ('reserved', c.c_uint8 * 16),
                ('readings', Reading * 32)]


RAILS = {250: '8-pin #1 input', 251: '8-pin #2 input',
         228: '8-pin #3 input', 229: '8-pin #4 input',
         230: '8-pin #5 input', 231: '8-pin #6 input',
         252: '6-pin #1 input', 253: '6-pin #2 input',
         255: 'PCIe +12V input', 222: 'PCIe +12V #2 input',
         254: 'PCIe +3.3V input', 245: 'Total board input',
         223: 'Total board #2 input', 1: 'NVVDD output', 2: 'FBVDD output'}
KINDS = {0: 'default', 1: 'sum', 2: 'estimated', 3: 'slow', 4: 'correction',
         5: 'C1X', 6: 'sensor', 7: 'estimated LUT', 8: 'aligned sensor'}


def decode_readings(info, status):
    """Use only channels present in both masks; totals never become pin currents."""
    result = []
    for index in range(32):
        if not (info.mask & status.mask & (1 << index)):
            continue
        channel, reading = info.channels[index], status.readings[index]
        # Private APIs may return sentinel values. Reject invalid fields rather
        # than presenting garbage as a reading or substituting a fixed voltage.
        watts = reading.power / 1000
        volts = reading.voltage / 1_000_000
        amps = reading.current / 1000
        result.append(dict(channel=index, rail=channel.rail,
                           label=RAILS.get(channel.rail, f'Unknown rail ({channel.rail})'),
                           kind=KINDS.get(channel.kind, f'unknown ({channel.kind})'),
                           watts=watts if watts <= 3000 else None,
                           volts=volts if channel.kind in (6, 8) and 0 < volts <= 60 else None,
                           amps=amps if channel.kind in (6, 8) and amps <= 300 else None))
    return result


class NvidiaRails:
    """Only initialization, identity, power monitor getters and unload are bound."""
    def __init__(self):
        self.initialized = False
        self.dll = c.WinDLL('nvapi64.dll')
        self.query = self.dll.nvapi_QueryInterface
        self.query.argtypes = [c.c_uint32]
        self.query.restype = c.c_void_p

    def bind(self, ident, *args):
        address = self.query(ident)
        if not address:
            raise RuntimeError(f'NVAPI getter unavailable ({ident:#x})')
        return c.CFUNCTYPE(c.c_int, *args)(address)

    @staticmethod
    def check(code):
        if code != 0:
            raise RuntimeError(f'NVAPI returned {code}; telemetry unavailable through this backend')

    def connect(self):
        if (c.sizeof(MonitorInfo), c.sizeof(MonitorStatus)) != (3240, 1436):
            raise RuntimeError('Unexpected NVAPI structure layout')
        self.check(self.bind(0x0150E828)())
        self.initialized = True
        enum = self.bind(0xE5AC921F, c.POINTER(c.c_void_p), c.POINTER(c.c_uint32))
        name = self.bind(0xCEEE8E9F, c.c_void_p, c.c_char_p)
        bus = self.bind(0x1BE0B8E5, c.c_void_p, c.POINTER(c.c_uint32))
        pci = self.bind(0x2DDFB66E, c.c_void_p, *([c.POINTER(c.c_uint32)] * 4))
        self.get_info = self.bind(0xC12EB19E, c.c_void_p, c.POINTER(MonitorInfo))
        self.get_status = self.bind(0xF40238EF, c.c_void_p, c.POINTER(MonitorStatus))
        handles, count = (c.c_void_p * 64)(), c.c_uint32()
        self.check(enum(handles, c.byref(count)))
        if count.value > 64:
            raise RuntimeError('Invalid NVIDIA GPU count')
        if not count.value:
            raise RuntimeError('No NVIDIA GPUs currently enumerated')
        self.devices = []
        for handle in handles[:count.value]:
            title, bus_id = c.create_string_buffer(64), c.c_uint32()
            self.check(name(handle, title))
            self.check(bus(handle, c.byref(bus_id)))
            ids = [c.c_uint32() for _ in range(4)]
            self.check(pci(handle, *(c.byref(value) for value in ids)))
            self.devices.append((handle, dict(name=title.value.decode(errors='replace'),
                                             bus=bus_id.value, device_id=hex(ids[0].value),
                                             subsystem_id=hex(ids[1].value))))

    def sample(self):
        devices = []
        for handle, identity in self.devices:
            device = dict(identity, source='NVIDIA NVAPI (private)', granularity='rail',
                          readings=[], error=None)
            try:
                info = MonitorInfo()
                info.version = c.sizeof(info) | (3 << 16)
                self.check(self.get_info(handle, c.byref(info)))
                if not info.supported or not info.mask:
                    raise RuntimeError('No rail channels exposed through this backend')
                status = MonitorStatus()
                status.version = c.sizeof(status) | (1 << 16)
                status.mask = info.mask
                self.check(self.get_status(handle, c.byref(status)))
                device['readings'] = decode_readings(info, status)
            except RuntimeError as ex:
                device['error'] = str(ex)
            devices.append(device)
        return devices

    def close(self):
        if self.initialized:
            self.initialized = False
            self.bind(0xD22BDD7E)()


class RailMonitor:
    """Independent snapshot, deliberately never passed into the guard engine."""
    def __init__(self):
        self.lock = threading.Lock()
        self.state = dict(devices=[], sample_time=None, error='Not yet checked')

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def run(self, stop, paused):
        backend = None
        try:
            while not stop.is_set():
                if paused():
                    with self.lock:
                        self.state = dict(devices=[], sample_time=None, error='Paused')
                    stop.wait(0.25)
                    continue
                try:
                    if backend is None:
                        backend = NvidiaRails()
                        backend.connect()
                    devices = backend.sample()
                    with self.lock:
                        self.state = dict(devices=devices, sample_time=time.monotonic(), error=None)
                except Exception as ex:
                    with self.lock:
                        self.state = dict(devices=[], sample_time=None, error=str(ex))
                    if backend:
                        backend.close()
                    backend = None
                stop.wait(2 if backend else 5)
        finally:
            if backend:
                backend.close()


if __name__ == '__main__':
    backend = NvidiaRails()
    try:
        backend.connect()
        print(json.dumps(backend.sample(), indent=2))
    finally:
        backend.close()
