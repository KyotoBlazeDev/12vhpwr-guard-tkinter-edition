"""Read-only NVIDIA-SMI telemetry. Never supplies the protection engine."""
import copy
import ctypes as c
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

METRICS = {
    'power': ('Board power', 'W'), 'core_voltage': ('Core voltage', 'V'),
    'clock': ('Graphics clock', 'MHz'), 'memory_used': ('VRAM used', 'MiB'),
    'memory_total': ('VRAM total', 'MiB'), 'utilization': ('GPU load', '%'),
    'temperature': ('GPU temperature', '°C'),
}


def number(text, maximum, multiplier=1):
    if not text:
        return None
    match = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[^\d]*', text)
    if not match:
        return None
    value = float(match[1]) * multiplier
    return value if math.isfinite(value) and 0 <= value <= maximum else None


def parse_xml(text):
    root = ET.fromstring(text)
    devices = []
    for gpu in root.findall('gpu'):
        def get(*paths):
            return next((gpu.findtext(path) for path in paths if gpu.findtext(path)), None)
        devices.append(dict(
            name=get('product_name') or 'NVIDIA GPU',
            bus=get('pci/pci_bus_id') or gpu.get('id', 'Unknown'),
            source='NVIDIA-SMI (reported)',
            metrics=dict(
                power=number(get('gpu_power_readings/instant_power_draw', 'gpu_power_readings/power_draw',
                                 'power_readings/power_draw'), 3000),
                core_voltage=number(get('voltage/graphics_volt'), 5,
                    0.001 if 'mv' in (get('voltage/graphics_volt') or '').lower() else 1),
                clock=number(get('clocks/graphics_clock'), 10000),
                memory_used=number(get('fb_memory_usage/used'), 1_000_000),
                memory_total=number(get('fb_memory_usage/total'), 1_000_000),
                utilization=number(get('utilization/gpu_util'), 100),
                temperature=number(get('temperature/gpu_temp'), 150))))
    return devices


def find_smi():
    # Fixed driver locations; do not execute an untrusted local PATH shadow.
    candidates = [Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'nvidia-smi.exe',
                  Path(os.environ.get('ProgramW6432', r'C:\Program Files')) /
                  'NVIDIA Corporation' / 'NVSMI' / 'nvidia-smi.exe']
    return next((str(path) for path in candidates if path.is_file()), None)


class Memory(c.Structure):
    _fields_ = [('total', c.c_ulonglong), ('free', c.c_ulonglong), ('used', c.c_ulonglong)]


class Utilization(c.Structure):
    _fields_ = [('gpu', c.c_uint), ('memory', c.c_uint)]


def sample_nvml():
    """Public NVML getter fallback; deliberately binds no GPU-setting functions."""
    from gpu_mitigation import NvmlPciInfo  # Structure only; no mitigator instance.
    locations = [Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'nvml.dll',
                 Path(os.environ.get('ProgramW6432', r'C:\Program Files')) /
                 'NVIDIA Corporation' / 'NVSMI' / 'nvml.dll']
    path = next((path for path in locations if path.is_file()), None)
    if path is None:
        raise RuntimeError('Neither NVIDIA-SMI nor the NVIDIA NVML driver library is available')
    dll = c.WinDLL(str(path))
    def bind(name, args):
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, c.c_int
        return fn
    def checked(code):
        if code:
            raise RuntimeError(f'NVML identity query failed ({code})')
    init, close = bind('nvmlInit_v2', []), bind('nvmlShutdown', [])
    checked(init())
    try:
        count = c.c_uint()
        checked(bind('nvmlDeviceGetCount_v2', [c.POINTER(c.c_uint)])(c.byref(count)))
        if count.value > 64:
            raise RuntimeError('Invalid GPU count')
        result = []
        for index in range(count.value):
            handle = c.c_void_p()
            checked(bind('nvmlDeviceGetHandleByIndex_v2', [c.c_uint, c.POINTER(c.c_void_p)])(index, c.byref(handle)))
            name, pci = c.create_string_buffer(96), NvmlPciInfo()
            checked(bind('nvmlDeviceGetName', [c.c_void_p, c.c_char_p, c.c_uint])(handle, name, len(name)))
            checked(bind('nvmlDeviceGetPciInfo_v3', [c.c_void_p, c.POINTER(NvmlPciInfo)])(handle, c.byref(pci)))
            def optional(name, output, *arguments):
                try:
                    fn = bind(name, [c.c_void_p] + [c.c_uint]*len(arguments) + [c.POINTER(type(output))])
                    return output if fn(handle, *arguments, c.byref(output)) == 0 else None
                except AttributeError:
                    return None
            def scalar(name, maximum, factor=1, *arguments):
                value = optional(name, c.c_uint(), *arguments)
                return number(str(value.value), maximum, factor) if value is not None else None
            memory = optional('nvmlDeviceGetMemoryInfo', Memory())
            utilization = optional('nvmlDeviceGetUtilizationRates', Utilization())
            metrics = dict(power=scalar('nvmlDeviceGetPowerUsage', 3000, 0.001),
                           core_voltage=None,
                           clock=scalar('nvmlDeviceGetClockInfo', 10000, 1, 0),
                           temperature=scalar('nvmlDeviceGetTemperature', 150, 1, 0),
                           utilization=number(str(utilization.gpu), 100) if utilization is not None else None,
                           memory_used=number(str(memory.used), 1_000_000, 1/1048576) if memory is not None else None,
                           memory_total=number(str(memory.total), 1_000_000, 1/1048576) if memory is not None else None)
            result.append(dict(name=name.value.decode(errors='replace'),
                               bus=pci.busId.decode(errors='replace'), source='NVIDIA NVML (reported)', metrics=metrics))
        return result
    finally:
        close()


def sample_devices():
    executable = find_smi()
    if executable:
        try:
            result = subprocess.run([executable, '-q', '-x'], capture_output=True, text=True,
                                    timeout=4, creationflags=0x08000000)
            if not result.returncode:
                return parse_xml(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ET.ParseError):
            pass
    return sample_nvml()


class GpuMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = dict(devices=[], sample_time=None, error='Not yet checked')

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def run(self, stop, paused):
        while not stop.is_set():
            state = dict(devices=[], sample_time=None, error='Paused')
            if not paused():
                try:
                    devices = sample_devices()
                    state = dict(devices=devices, sample_time=time.monotonic(),
                                 error=None if devices else 'No NVIDIA GPUs enumerated')
                except Exception as error:
                    state['error'] = str(error)
            with self.lock:
                self.state = state
            stop.wait(2)


def fresh_devices(snapshot, active=True, now=None):
    now = time.monotonic() if now is None else now
    stamp = snapshot.get('sample_time')
    return snapshot.get('devices', []) if active and stamp is not None and 0 <= now-stamp <= 6 else []
