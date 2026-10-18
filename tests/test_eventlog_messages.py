import ast
from pathlib import Path
import unittest

import eventlog_messages as messages


class EventMessageTests(unittest.TestCase):
    def test_every_guard_event_id_formats_with_windows_message_api(self):
        source = Path(__file__).resolve().parents[1] / 'hwinfo_12vhpwr_guard.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        ids = {node.args[1].value for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name) and node.func.id == 'eventlog_write'
               and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)}
        self.assertIn(1000, ids)
        self.assertIn(1003, ids)
        path = messages.ensure_resource()
        for event_id in ids | {messages.FIRST_ID, messages.LAST_ID}:
            with self.subTest(event_id=event_id):
                text = f'Event {event_id}: pin current 0 A — 100% ✓'
                self.assertEqual(text, messages.format_message(path, event_id, text).rstrip('\r\n'))

    def test_resource_is_deterministic_and_contains_no_entrypoint(self):
        import struct
        data = messages.resource_dll()
        self.assertEqual(data, messages.resource_dll())
        self.assertEqual(b'MZ', data[:2])
        optional_offset = 128+4+20
        self.assertEqual(0, struct.unpack_from('<I', data, optional_offset+16)[0])
        self.assertEqual(0, struct.unpack_from('<I', data, optional_offset+112+8)[0])  # no imports


if __name__ == '__main__':
    unittest.main()
