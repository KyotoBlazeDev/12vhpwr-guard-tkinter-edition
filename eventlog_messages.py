"""Resource-only PE message DLL for classic Windows Event Log insertion strings.

Contains no executable code, imports or entry point. IDs 1000–9999 format the
existing event's first insertion string, preserving historical guard event IDs.
The deterministic builder avoids requiring Windows SDK build tools at install.
"""
import argparse
import ctypes as c
from pathlib import Path
import struct

FIRST_ID, LAST_ID = 1000, 9999
MESSAGE_PATH = Path(__file__).resolve().parent / 'assets' / 'guard_events_v1.dll'


def resource_dll():
    text = '%1\r\n\0'.encode('utf-16le')
    length = (4 + len(text) + 3) & ~3
    entry = struct.pack('<HH', length, 1) + text
    entry += b'\0' * (length-len(entry))
    table = struct.pack('<IIII', 1, FIRST_ID, LAST_ID, 16) + entry*(LAST_ID-FIRST_ID+1)
    directory = struct.pack('<IIHHHH', 0, 0, 0, 0, 0, 1)
    # Type RT_MESSAGETABLE (11), name 1, neutral language (0).
    resource = directory + struct.pack('<II', 11, 0x80000018)
    resource += directory + struct.pack('<II', 1, 0x80000030)
    resource += directory + struct.pack('<II', 0, 72)
    resource += struct.pack('<IIII', 0x1000+88, len(table), 0, 0) + table
    raw_size = (len(resource)+511) & ~511
    dos = bytearray(128)
    dos[:2] = b'MZ'
    struct.pack_into('<I', dos, 60, 128)
    coff = struct.pack('<HHIIIHH', 0x8664, 1, 0, 0, 0, 240, 0x2022)
    optional = bytearray(240)
    struct.pack_into('<H', optional, 0, 0x20B)
    struct.pack_into('<I', optional, 8, raw_size)
    struct.pack_into('<Q', optional, 24, 0x180000000)
    struct.pack_into('<II', optional, 32, 4096, 512)
    struct.pack_into('<H', optional, 40, 6)
    struct.pack_into('<H', optional, 48, 6)
    struct.pack_into('<II', optional, 56, (0x1000+len(resource)+4095) & ~4095, 512)
    struct.pack_into('<HH', optional, 68, 2, 0x100)
    struct.pack_into('<QQQQ', optional, 72, 1048576, 4096, 1048576, 4096)
    struct.pack_into('<I', optional, 108, 16)
    struct.pack_into('<II', optional, 128, 0x1000, len(resource))
    section = struct.pack('<8sIIIIIIHHI', b'.rsrc\0\0\0', len(resource), 0x1000,
                          raw_size, 512, 0, 0, 0, 0, 0x40000040)
    header = bytes(dos) + b'PE\0\0' + coff + bytes(optional) + section
    return header.ljust(512, b'\0') + resource.ljust(raw_size, b'\0')


def ensure_resource():
    expected = resource_dll()
    if not MESSAGE_PATH.exists() or MESSAGE_PATH.read_bytes() != expected:
        MESSAGE_PATH.parent.mkdir(exist_ok=True)
        MESSAGE_PATH.write_bytes(expected)
    return MESSAGE_PATH


def format_message(path, event_id, message):
    kernel = c.WinDLL('kernel32', use_last_error=True)
    load = kernel.LoadLibraryExW
    load.argtypes, load.restype = [c.c_wchar_p, c.c_void_p, c.c_uint32], c.c_void_p
    free = kernel.FreeLibrary
    free.argtypes, free.restype = [c.c_void_p], c.c_int
    formatter = kernel.FormatMessageW
    formatter.argtypes = [c.c_uint32, c.c_void_p, c.c_uint32, c.c_uint32,
                          c.c_wchar_p, c.c_uint32, c.c_void_p]
    formatter.restype = c.c_uint32
    module = load(str(path), None, 0x2 | 0x20)  # data/image resource only; never execute.
    if not module:
        raise c.WinError(c.get_last_error())
    try:
        output = c.create_unicode_buffer(max(4096, len(message)+128))
        inserts = (c.c_wchar_p*1)(message)
        if not formatter(0x800 | 0x2000, module, event_id, 0,
                         output, len(output), c.cast(inserts, c.c_void_p)):
            raise c.WinError(c.get_last_error())
        return output.value
    finally:
        free(module)


def register_source(source='12VHPWR Guard'):
    import win32evtlogutil
    import winreg
    path = ensure_resource()
    for event_id in (FIRST_ID, 1003, 5090, 9001, LAST_ID):
        if format_message(path, event_id, 'Message resource verified.').strip() != 'Message resource verified.':
            raise RuntimeError('Event message resource verification failed')
    key_path = rf'SYSTEM\CurrentControlSet\Services\EventLog\Application\{source}'
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            actual, _ = winreg.QueryValueEx(key, 'EventMessageFile')
        if Path(actual) == path:
            return path  # Once repaired, standard-user launches need no registry writes.
    except FileNotFoundError:
        pass
    win32evtlogutil.AddSourceToRegistry(source, msgDLL=str(path), eventLogType='Application')
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
        actual, _ = winreg.QueryValueEx(key, 'EventMessageFile')
    if Path(actual) != path:
        raise RuntimeError('EventMessageFile registration could not be verified')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--register', action='store_true', help='Repair source registration (administrator required)')
    args = parser.parse_args()
    path = register_source() if args.register else ensure_resource()
    print(format_message(path, 1000, '12VHPWR Guard started.').strip())
    print(f'Resource verified: IDs {FIRST_ID}–{LAST_ID}. Registration changed: {args.register}')
