"""Isolated current-user logon task with Task Scheduler failure restart settings."""
import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

TASK_NAME = '12VHPWR Guard Tkinter Edition'
NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
BASE = Path(__file__).resolve().parent


def task_xml(python, script, user, highest=False):
    ET.register_namespace('', NS)
    root = ET.Element(f'{{{NS}}}Task', version='1.2')
    def node(parent, tag, text=None, **attributes):
        element = ET.SubElement(parent, tag, attributes)
        element.text = text
        return element
    registration = node(root, 'RegistrationInfo')
    node(registration, 'Description', 'Tkinter Edition; logon startup and failure watchdog. Independent of upstream task.')
    triggers = node(root, 'Triggers')
    trigger = node(triggers, 'LogonTrigger')
    node(trigger, 'Enabled', 'true')
    node(trigger, 'UserId', user)
    principals = node(root, 'Principals')
    principal = node(principals, 'Principal', id='Author')
    node(principal, 'UserId', user)
    node(principal, 'LogonType', 'InteractiveToken')
    node(principal, 'RunLevel', 'HighestAvailable' if highest else 'LeastPrivilege')
    settings = node(root, 'Settings')
    for tag, value in dict(MultipleInstancesPolicy='IgnoreNew', DisallowStartIfOnBatteries='false',
                           StopIfGoingOnBatteries='false', StartWhenAvailable='true', Enabled='true',
                           ExecutionTimeLimit='PT0S').items():
        node(settings, tag, value)
    restart = node(settings, 'RestartOnFailure')
    node(restart, 'Interval', 'PT1M')
    node(restart, 'Count', '999')
    actions = node(root, 'Actions', Context='Author')
    action = node(actions, 'Exec')
    node(action, 'Command', str(python))
    node(action, 'Arguments', subprocess.list2cmdline([str(script), '--tray']))
    node(action, 'WorkingDirectory', str(Path(script).parent))
    return ET.tostring(root, encoding='unicode')


def run(*args):
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'schtasks.exe'
    return subprocess.run([str(executable), *args], capture_output=True, timeout=15,
                          creationflags=0x08000000)


def status():
    result = run('/query', '/tn', TASK_NAME, '/xml', 'ONE')
    if result.returncode:
        return 'Not installed / inaccessible'
    import hwinfo_12vhpwr_guard as guard
    try:
        root = ET.fromstring(guard._clean_task_xml(result.stdout))
        command = root.findtext(f'.//{{{NS}}}Exec/{{{NS}}}Arguments') or ''
        if str(BASE / 'guard_tk.py').lower() not in command.lower():
            return 'Different installation owns this task'
        enabled = root.findtext(f'{{{NS}}}Settings/{{{NS}}}Enabled', 'true')
        return 'Enabled' if enabled.lower() == 'true' else 'Disabled'
    except (ValueError, ET.ParseError):
        return 'Task query failed'


def install(highest=False):
    if status() == 'Different installation owns this task':
        raise RuntimeError('Another Tkinter Edition installation owns this task. Remove it there first.')
    domain, name = os.environ.get('USERDOMAIN'), os.environ['USERNAME']
    user = domain + '\\' + name if domain else name
    python = Path(sys.executable).with_name('pythonw.exe')
    if not python.is_file():
        python = Path(sys.executable)
    path = BASE / 'startup-task.xml'
    try:
        path.write_text(task_xml(python, BASE / 'guard_tk.py', user, highest), encoding='utf-16')
        result = run('/create', '/tn', TASK_NAME, '/xml', str(path), '/f')
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace') or 'Task installation failed')
    finally:
        path.unlink(missing_ok=True)
    if status() != 'Enabled':
        raise RuntimeError('Could not verify installed task')


def change(action):
    state = status()
    if state not in ('Enabled', 'Disabled'):
        raise RuntimeError(state)
    result = run('/delete', '/tn', TASK_NAME, '/f') if action == 'remove' else run(
        '/change', '/tn', TASK_NAME, '/enable' if action == 'enable' else '/disable')
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace') or 'Task change failed')
    after = status()
    if action in ('enable', 'disable') and after != ('Enabled' if action == 'enable' else 'Disabled'):
        raise RuntimeError('Task change could not be verified')
    return after


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'enable', 'disable', 'remove', 'status'))
    parser.add_argument('--highest', action='store_true')
    args = parser.parse_args()
    if args.action == 'install':
        install(args.highest)
    elif args.action != 'status':
        change(args.action)
    print(status())
