"""Tkinter-first frontend for the retained 12VHPWR Guard monitoring engine."""
import argparse
import math
import json
from pathlib import Path
import threading
import time
import webbrowser
import queue
import os
import sys
import copy
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import hwinfo_12vhpwr_guard as guard
from rail_telemetry import RailMonitor
from ui_widgets import Widgets, set_choices
from gpu_telemetry import GpuMonitor, METRICS, fresh_devices
from overlay_ui import Overlay
from tray_service import TrayService
import settings_service
import startup_manager

UPSTREAM_URL = "https://github.com/humza-khalid/12vhpwr-guard#disclaimer--legal-notice"
HWINFO_LIMIT_NOTE = (
    "HWiNFO64 Free (non-Pro): Shared Memory Support has a 12-HOUR LIMIT. "
    "Re-enable it manually in HWiNFO when it expires. If HWiNFO is the pin-current "
    "source, losing shared memory interrupts protection until valid readings return. "
    "Direct NVAPI/NVML readings do not depend on this HWiNFO interface."
)
WARNING_SUMMARY = (
    "Per-pin protection requires verified supported sensors (current backend: ASUS ROG Astral). "
    "Rail telemetry is display-only. BTF via GC-HPWR requires a different sensor path; "
    "near-zero GPU readings cannot establish protection.\n"
    "Supplementary protection only; keep firmware, driver and PSU protections active. "
    "Emergency shutdown force-closes apps and can lose unsaved work."
)
WARNING_DETAILS = (
    "Tkinter Edition — upstream warnings\n\n"
    + WARNING_SUMMARY + "\n\n"
    + HWINFO_LIMIT_NOTE + "\n\n"
    "Higher current thresholds or longer sustained times reduce protection and can allow "
    "overheating, connector melting, GPU damage and fire. Defaults are not a guarantee of safety.\n\n"
    "Tiered response can temporarily reduce GPU clocks and power limit. Administrative rights "
    "are required; unavailable mitigation falls back to shutdown-only. A pre-existing manual "
    "clock lock cannot be restored: release returns clocks to automatic management.\n\n"
    "Missing/stale sensors and pausing mean protection cannot be relied upon. Closing the "
    "GUI Exit stops monitoring. Optional startup/watchdog requires installation; hiding to tray keeps it running.\n\n"
    "Emulated high-current sensor input can cause a real shutdown. Use --preview for layout "
    "inspection; it does not start the engine.\n\n"
    "Provided AS IS without warranty. Testing is limited across hardware configurations. "
    "Read the upstream disclaimer and retained MIT license below.\n\n"
    "Source: " + UPSTREAM_URL + "\nChecked 2026-10-18; adaptation retains the local 1.3.1 engine."
)


def acquisition_state(state, fresh):
    """Keep detection evidence separate from protection response/severity."""
    pins = {label: (value, unit) for label, value, unit in state.get("pins", [])}
    complete = all(label in pins and pins[label][1] == "A"
                   and math.isfinite(pins[label][0]) and 0 <= pins[label][0] <= 30
                   for label in guard.PIN_LABELS)
    if fresh and complete:
        return "LIVE", "Valid, fresh sensor readings"
    if state.get("hardware_supported") is False:
        return "UNAVAILABLE", "No per-pin source available through the current backends"
    if state.get("hardware_supported") is True:
        return "ERROR", "Supported hardware, but sensor acquisition failed"
    return "WAITING", "Hardware compatibility has not been established"


def rail_display(snapshot, active, now=None):
    """Format the independent telemetry snapshot without claiming protection."""
    now = time.monotonic() if now is None else now
    stamp = snapshot.get('sample_time')
    if not active:
        return False, 'Rail telemetry paused / preview; no live readings.'
    if stamp is None or not 0 <= now - stamp <= 5:
        return False, 'Rail telemetry unavailable / stale. ' + (snapshot.get('error') or '')
    lines, available = [], False
    for device in snapshot.get('devices', []):
        lines.append(f"{device['name']} | PCI bus {device['bus']} | {device['source']}")
        if device.get('error'):
            lines.append(device['error'])
        for row in device.get('readings', []):
            values = [f"{row[key]:.3f} {unit}" for key, unit in
                      (('watts', 'W'), ('volts', 'V'), ('amps', 'A')) if row.get(key) is not None]
            available = available or bool(values)
            lines.append(f"  {row['label']} [channel {row['channel']}, {row['kind']}]: "
                         + (' | '.join(values) if values else 'Unavailable'))
    return available, '\n'.join(lines) or 'No GPU telemetry exposed through this backend.'


class MainThreadDialogs:
    def __init__(self, root):
        self.root = root

    def ask_float(self, title, prompt, initial):
        value = simpledialog.askfloat(title, prompt, initialvalue=initial,
                                     minvalue=0.1, parent=self.root)
        if value is not None and not math.isfinite(value):
            self.show_error('Invalid value', 'Enter a finite positive number.')
            return None
        return value

    def confirm(self, title, msg):
        return messagebox.askyesno(title, msg, parent=self.root)

    def show_error(self, title, msg):
        messagebox.showerror(title, msg, parent=self.root)


class GuardWindow:
    def __init__(self, root, preview=False, ui_style=None):
        self.root = root
        self.preview = preview
        self.stop_event = threading.Event()
        self.worker = None
        self.rail_worker = None
        self.rail_monitor = RailMonitor()
        self.gpu_monitor = GpuMonitor()
        self.gpu_worker = None
        self.commands = queue.Queue()
        self.tray = TrayService(self.commands)
        self.overlay = None
        self.monitor_failed = threading.Event()
        self.exit_code = 0
        self.startup_busy = False
        self.jobs = []
        self.startup_description = 'Not yet checked'
        self.last_task_check = 0
        self.metric_vars = {}
        self.selected_bus = None
        self.closing = False
        self.scale_key = None
        self.model_scales = {}
        self.preferences_path = Path(__file__).resolve().parent / "display_settings.json"
        try:
            saved = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.model_scales = {key: float(value) for key, value in saved.items()
                                     if isinstance(value, (int, float)) and math.isfinite(value) and 10 <= value <= 40}
        except (OSError, ValueError):
            pass
        self.ui_preferences_path = Path(__file__).resolve().parent / "ui_settings.json"
        self.ui_options = {}
        try:
            options = json.loads(self.ui_preferences_path.read_text(encoding="utf-8"))
            if isinstance(options, dict):
                self.ui_options = options
        except (OSError, ValueError):
            pass
        self.selected_bus = self.ui_options.get('selected_bus')
        metric_options = self.ui_options.get('metrics', {})
        if not isinstance(metric_options, dict):
            metric_options = {}
        for key in ('power', 'core_voltage', 'clock', 'memory_used', 'utilization', 'temperature', 'rails'):
            default = key != 'core_voltage'
            self.metric_vars[key] = tk.BooleanVar(root, value=bool(metric_options.get(key, default)))
        scale = self.ui_options.get('overlay_scale', 1.0)
        self.overlay_scale = tk.DoubleVar(root, value=scale if isinstance(scale, (float, int)) and math.isfinite(scale) and 0.6 <= scale <= 2 else 1.0)
        if ui_style is None:
            try:
                ui_style = json.loads(self.ui_preferences_path.read_text(encoding="utf-8")).get("style")
            except (OSError, ValueError, AttributeError):
                pass
        self.ui_style = ui_style if ui_style in ("TTK", "Tk Classic") else "TTK"
        self.refresh_id = None
        self.build_ui()

    def build_ui(self):
        root = self.root
        self.ui = Widgets(classic=self.ui_style == "Tk Classic")
        root.title("12VHPWR Guard | Tkinter Edition")
        if not getattr(self, 'built_once', False):
            root.geometry(f"960x{min(900, root.winfo_screenheight()-100)}")
            self.built_once = True
        root.minsize(800, 540)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind('<Destroy>', self.cancel_refresh)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
        style.configure("Reading.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 13, "bold"))
        viewport = tk.Canvas(root, highlightthickness=0)
        self.viewport = viewport
        scroll = self.ui.Scrollbar(root, command=viewport.yview)
        viewport.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        viewport.pack(side="left", fill="both", expand=True)
        main = self.ui.Frame(viewport, padding=22)
        item = viewport.create_window((0, 0), window=main, anchor="nw")
        main.bind('<Configure>', lambda event: viewport.configure(scrollregion=viewport.bbox('all')))
        def fit_content(event):
            viewport.itemconfigure(item, width=event.width)
            widgets = list(main.winfo_children())
            while widgets:
                widget = widgets.pop()
                widgets.extend(widget.winfo_children())
                if widget.winfo_class() in ('TLabel', 'Label') and float(str(widget.cget('wraplength') or 0)) > 0:
                    widget.configure(wraplength=max(200, event.width-65))
        viewport.bind('<Configure>', fit_content)
        root.bind('<MouseWheel>', lambda event: viewport.yview_scroll(-int(event.delta/120), 'units'))
        self.ui.Label(main, text="12VHPWR Guard", style="Title.TLabel").pack(anchor="w")
        self.ui.Label(main, text="Tkinter Edition  •  Per-pin protection and rail telemetry").pack(anchor="w")
        selector = self.ui.Frame(main)
        selector.pack(fill="x", pady=(4, 0))
        self.ui.Label(selector, text="Interface:").pack(side="left")
        self.ui_choice = tk.StringVar(value=self.ui_style)
        chooser = self.ui.Combobox(selector, textvariable=self.ui_choice,
                                   values=("TTK", "Tk Classic"), state="readonly", width=14)
        chooser.pack(side="left", padx=8)
        chooser.bind("<<ComboboxSelected>>", self.switch_ui)
        self.model_label = self.ui.Label(main, text="GPU model: Unavailable")
        self.model_label.pack(anchor="w")
        self.status = self.ui.Label(main, style="Status.TLabel", padding=(0, 16))
        self.status.pack(anchor="w")
        self.message = self.ui.Label(main, wraplength=760)
        self.message.pack(anchor="w", pady=(0, 12))
        grid = self.ui.Frame(main)
        grid.pack(fill="x")
        self.readings = []
        self.gauges = []
        self.voltage_labels = []
        for index in range(6):
            frame = self.ui.LabelFrame(grid, text=f"Pin {index + 1}", padding=12)
            frame.grid(row=0, column=index, sticky="nsew", padx=4, pady=4)
            grid.columnconfigure(index, weight=1, uniform="pin")
            gauge = self.ui.Progressbar(frame, orient="vertical", length=65, maximum=20)
            gauge.pack()
            self.gauges.append(gauge)
            reading = self.ui.Label(frame, text="— A", font=("Segoe UI", 16, "bold"))
            reading.pack(anchor="w")
            self.readings.append(reading)
            voltage = self.ui.Label(frame, text="")
            voltage.pack(anchor="w")
            self.voltage_labels.append(voltage)
        display = self.ui.Frame(main)
        display.pack(fill="x", pady=(8, 0))
        self.scale_value = tk.DoubleVar(value=20)
        self.scale_label = self.ui.Label(display, text="Gauge scale: 0–20 A")
        self.scale_label.pack(side="left")
        self.scale_slider = self.ui.Scale(display, from_=10, to=40, variable=self.scale_value,
                                     command=self.change_display_scale)
        self.scale_slider.pack(side="left", fill="x", expand=True, padx=12)
        self.scale_slider.bind("<ButtonRelease-1>", self.save_display_scale)
        self.scale_slider.bind("<KeyRelease>", self.save_display_scale)
        self.show_voltage = tk.BooleanVar(value=True)
        self.voltage_toggle = self.ui.Checkbutton(display, text="Pin voltage", variable=self.show_voltage)
        self.voltage_toggle.pack(side="right")
        rails = self.ui.LabelFrame(main, text="Rail telemetry — display only; no per-pin protection", padding=8)
        rails.pack(fill="x", pady=(8, 0))
        self.rail_text = tk.Text(rails, height=5, wrap="word", font=("Segoe UI", 10),
                                 state="disabled")
        rail_scroll = self.ui.Scrollbar(rails, command=self.rail_text.yview)
        self.rail_text.configure(yscrollcommand=rail_scroll.set)
        rail_scroll.pack(side="right", fill="y")
        self.rail_text.pack(fill="x", expand=True)
        metrics = self.ui.LabelFrame(main, text="GPU metrics — display only", padding=8)
        metrics.pack(fill="x", pady=(8, 0))
        self.device_choice = tk.StringVar(root, value='No GPU data')
        self.device_selector = self.ui.Combobox(metrics, textvariable=self.device_choice,
                                              values=('No GPU data',), state='readonly', width=55)
        self.device_selector.pack(anchor='w')
        self.device_selector.bind('<<ComboboxSelected>>', self.select_device)
        self.metric_text = self.ui.Label(metrics, text='Waiting for reported GPU metrics', wraplength=760, justify='left')
        self.metric_text.pack(anchor='w', pady=4)
        self.details = self.ui.Label(main, wraplength=760, justify="left")
        self.details.pack(fill="x", pady=14)
        controls = self.ui.Frame(main)
        controls.pack(fill="x")
        self.pause = self.ui.Button(controls, text="Pause monitoring", command=self.toggle_pause)
        self.pause.pack(side="left")
        self.ui.Button(controls, text="Open log", command=guard.open_log).pack(side="left", padx=8)
        self.ui.Button(controls, text="Warnings & compatibility", command=self.show_warnings).pack(side="left")
        self.ui.Button(controls, text="About", command=self.show_about).pack(side="left", padx=8)
        self.ui.Button(controls, text="Exit", command=self.close).pack(side="right")
        tools = self.ui.Frame(main)
        tools.pack(fill='x', pady=8)
        for label, command in [('Overlay', self.show_overlay), ('Display settings…', self.show_display_settings),
                               ('Hide to tray', self.hide_to_tray), ('Diagnostics…', self.show_diagnostics),
                               ('Startup / watchdog…', self.show_startup)]:
            self.ui.Button(tools, text=label, command=command).pack(side='left', padx=(0, 6))
        settings = self.ui.LabelFrame(main, text="Protection settings", padding=10)
        settings.pack(fill="x", pady=12)
        self.ui.Button(settings, text="Current threshold…", command=lambda: self.edit_primary('threshold_amps')).pack(side="left")
        self.ui.Button(settings, text="Sustained time…", command=lambda: self.edit_primary('sustained_seconds_required')).pack(side="left", padx=8)
        self.mode = tk.StringVar(value=guard.get_response_mode())
        mode = self.ui.Combobox(settings, textvariable=self.mode, state="readonly", width=18,
                            values=guard.VALID_RESPONSE_MODES)
        mode.pack(side="left")
        mode.bind("<<ComboboxSelected>>", self.change_response)
        self.ui.Button(settings, text="All settings…", command=self.show_settings).pack(side='left', padx=8)
        self.ui.Button(settings, text="Reset defaults…", command=self.reset_defaults).pack(side='left')
        if self.preview:
            for control in settings.winfo_children():
                control.configure(state='disabled')
        warnings = self.ui.LabelFrame(main, text="Important warnings", padding=10)
        warnings.pack(fill="x")
        self.ui.Label(warnings, text=WARNING_SUMMARY, wraplength=760, justify="left").pack(anchor="w")
        guard.dialog = MainThreadDialogs(root)
        self.refresh()

    def switch_ui(self, event=None):
        selected = self.ui_choice.get()
        if selected == self.ui_style or self.closing:
            return
        self.cancel_refresh()
        self.ui_style = selected
        voltage_visible = self.show_voltage.get()
        scale = self.scale_value.get()
        overlay_visible = self.overlay is not None and self.overlay.window.winfo_viewable()
        self.overlay = None
        for widget in self.root.winfo_children():
            widget.destroy()
        self.build_ui()
        self.show_voltage.set(voltage_visible)
        self.scale_value.set(scale)
        self.change_display_scale(scale)
        self.save_ui_preferences()
        if overlay_visible:
            self.show_overlay()

    def start(self):
        if not self.preview:
            logger = guard.setup_logger()
            self.worker = threading.Thread(target=self.monitor, args=(logger,), daemon=False)
            self.worker.start()
            self.rail_worker = threading.Thread(target=self.rail_monitor.run,
                args=(self.stop_event, lambda: guard.get_state().get('paused', False)), daemon=False)
            self.rail_worker.start()
            self.gpu_worker = threading.Thread(target=self.gpu_monitor.run,
                args=(self.stop_event, lambda: guard.get_state().get('paused', False)), daemon=False)
            self.gpu_worker.start()
            self.tray.start()

    def monitor(self, logger):
        try:
            guard.monitor_loop(self.stop_event, logger)
            if not self.stop_event.is_set():
                raise RuntimeError('Monitoring worker returned unexpectedly')
        except Exception:
            logger.exception("Monitoring stopped unexpectedly")
            guard.set_state(status=guard.Status.ERROR, last_message="Monitoring stopped unexpectedly; see log.")
            self.monitor_failed.set()

    def toggle_pause(self):
        if not self.preview:
            guard.toggle_pause(None, None)

    def edit_primary(self, key):
        if self.preview:
            return
        values = settings_service.editable()
        label = 'current threshold' if key == 'threshold_amps' else 'sustained time'
        unit = 'A' if key == 'threshold_amps' else 's'
        value = guard.dialog.ask_float('Protection settings', f'Enter {label} ({unit})', values[key])
        if value is None:
            return
        default = guard.DEFAULT_THRESHOLD_AMPS if key == 'threshold_amps' else guard.DEFAULT_SUSTAINED_SECONDS_REQUIRED
        if not guard.confirm_unsafe_value(label, value, default, unit):
            return
        values[key] = value
        try:
            settings_service.apply(values)
        except (ValueError, OSError) as error:
            messagebox.showerror('Settings not applied', str(error), parent=self.root)

    def change_response(self, event=None):
        if self.preview:
            return
        values = settings_service.editable()
        values['response_mode'] = self.mode.get()
        try:
            settings_service.apply(values)
        except (ValueError, OSError) as error:
            self.mode.set(guard.get_response_mode())
            messagebox.showerror('Response not applied', str(error), parent=self.root)

    def save_ui_preferences(self):
        if self.preview:
            return
        self.ui_options.update(style=self.ui_style, selected_bus=self.selected_bus,
                               overlay_scale=self.overlay_scale.get(),
                               metrics={key: variable.get() for key, variable in self.metric_vars.items()})
        path = self.ui_preferences_path.with_suffix('.json.tmp')
        try:
            path.write_text(json.dumps(self.ui_options, indent=2), encoding='utf-8')
            path.replace(self.ui_preferences_path)
        except OSError as error:
            messagebox.showerror('Display settings', str(error), parent=self.root)

    def select_device(self, event=None):
        self.selected_bus = getattr(self, 'device_labels', {}).get(self.device_choice.get())
        self.save_ui_preferences()

    def refresh_metrics(self, active):
        snapshot = self.gpu_monitor.snapshot()
        devices = fresh_devices(snapshot, active)
        labels = {f"{gpu['name']} | {gpu['bus']}": gpu['bus'] for gpu in devices}
        if labels != getattr(self, 'device_labels', None):
            self.device_labels = labels
            set_choices(self.device_selector, tuple(labels) or ('No fresh GPU data',))
        if not self.selected_bus and devices:
            self.selected_bus = devices[0]['bus']
        self.device_choice.set(next((label for label, bus in labels.items() if bus == self.selected_bus),
                                    'Selected GPU unavailable' if self.selected_bus else 'No fresh GPU data'))
        self.selected_gpu = next((gpu for gpu in devices if gpu['bus'] == self.selected_bus), None)
        self.metric_text.configure(text=self.selected_gpu['source'] + '\n' + '\n'.join(self.metric_rows()) if self.selected_gpu else
                                   'GPU metrics unavailable / stale. ' + (snapshot.get('error') or ''))

    def metric_rows(self):
        gpu = getattr(self, 'selected_gpu', None)
        values = gpu.get('metrics', {}) if gpu else {}
        rows = []
        for key, variable in self.metric_vars.items():
            if key == 'rails' or not variable.get():
                continue
            label, unit = METRICS[key]
            value = values.get(key)
            if key == 'memory_used' and value is not None:
                total = values.get('memory_total')
                reading = f"{value / 1024:.2f}" + (f" / {total / 1024:.2f}" if total is not None else '') + ' GiB'
            else:
                reading = f'{value:g} {unit}' if value is not None else 'Unavailable'
            rows.append(f'{label}: {reading}')
        return rows

    def show_display_settings(self):
        window = tk.Toplevel(self.root)
        window.title('Display & overlay settings')
        window.transient(self.root)
        frame = self.ui.Frame(window, padding=18)
        frame.pack(fill='both', expand=True)
        self.ui.Label(frame, text='Visible GPU readings (reported values only)').pack(anchor='w')
        gpu = getattr(self, 'selected_gpu', None)
        values = gpu.get('metrics', {}) if gpu else {}
        for key, variable in self.metric_vars.items():
            label = METRICS[key][0] if key != 'rails' else 'Rail channels (separate NVAPI source)'
            if key != 'rails' and values.get(key) is None:
                label += ' — currently unavailable'
            self.ui.Checkbutton(frame, text=label, variable=variable,
                                command=self.save_ui_preferences).pack(anchor='w', pady=3)
        self.ui.Label(frame, text='Overlay size (0.6–2.0; display only)').pack(anchor='w', pady=(12, 0))
        scale = self.ui.Scale(frame, from_=0.6, to=2.0, variable=self.overlay_scale)
        scale.pack(fill='x')
        scale.bind('<ButtonRelease-1>', lambda event: self.save_ui_preferences())
        scale.bind('<KeyRelease>', lambda event: self.save_ui_preferences())
        self.ui.Label(frame, text='Drag the overlay to move it; right-click for settings.\n'
                      'Overlay pin gauges always use the protection source, not board power.',
                      wraplength=500, justify='left').pack(anchor='w', pady=12)
        self.ui.Button(frame, text='Show overlay', command=self.show_overlay).pack(side='left')
        self.ui.Button(frame, text='Close', command=window.destroy).pack(side='right')

    def threshold_for_display(self):
        return guard.get_config_values()[0]

    def show_overlay(self):
        if self.overlay is None:
            self.overlay = Overlay(self)
        self.overlay.show()

    def update_overlay(self, state, fresh, status):
        pins = {label: value for label, value, unit in state.get('pins', [])
                if unit == 'A' and math.isfinite(value) and 0 <= value <= 30} if fresh else {}
        rows = []
        gpu = getattr(self, 'selected_gpu', None)
        if gpu:
            rows.append(f"Metrics: {gpu['name']} | {gpu['bus']}")
        rows.extend(self.metric_rows())
        if self.metric_vars['rails'].get():
            snapshot = self.rail_monitor.snapshot()
            live, _ = rail_display(snapshot, not self.preview and not state.get('paused'))
            if live:
                for device in snapshot.get('devices', []):
                    # Rail panel remains the full multi-GPU view. Overlay uses only
                    # a matching PCI bus; never silently combines different GPUs.
                    try:
                        selected_bus = int(str(self.selected_bus).split(':')[-2], 16)
                    except (ValueError, IndexError):
                        selected_bus = None
                    if selected_bus != device['bus']:
                        continue
                    for row in device.get('readings', []):
                        if row.get('volts') is not None:
                            rows.append(f"{row['label']}: {row['volts']:.2f} V [rail]")
        title = 'Pins: ' + (state.get('gpu_name') or state.get('source') or 'source unavailable')
        self.overlay.render(title, status, [pins.get(label) for label in guard.PIN_LABELS],
                            self.scale_value.get(), rows, self.overlay_scale.get())

    def show_dashboard(self):
        self.root.deiconify()
        self.root.lift()

    def hide_to_tray(self, quiet=False):
        if self.tray.available():
            self.root.withdraw()
        elif not quiet:
            messagebox.showinfo('Tray unavailable',
                'Install requirements.txt for pystray/Pillow support. The dashboard remains open.\n'
                + (self.tray.error or 'Tray is not running in preview.'), parent=self.root)

    def drain_commands(self):
        while not self.commands.empty():
            command = self.commands.get_nowait()
            if isinstance(command, tuple):
                _, value, error = command
                self.startup_busy = False
                self.startup_description = value if not error else str(error)
                if hasattr(self, 'startup_label') and self.startup_label.winfo_exists():
                    self.startup_label.configure(text=self.startup_description)
                if error:
                    messagebox.showerror('Startup / watchdog', str(error), parent=self.root)
            elif command == 'show':
                self.show_dashboard()
            elif command == 'overlay':
                self.show_overlay()
            elif command == 'pause':
                self.toggle_pause()
            elif command == 'exit':
                self.close()

    def startup_action(self, action):
        if self.startup_busy or (self.preview and action != 'status'):
            return
        self.startup_busy = True
        def work():
            try:
                if action == 'install':
                    startup_manager.install(highest=guard._is_elevated())
                    value = startup_manager.status()
                elif action == 'status':
                    value = startup_manager.status()
                else:
                    value = startup_manager.change(action)
                self.commands.put(('startup', value, None))
            except Exception as error:
                self.commands.put(('startup', None, error))
        thread = threading.Thread(target=work, daemon=False)
        self.jobs.append(thread)
        thread.start()

    def show_startup(self):
        window = tk.Toplevel(self.root)
        window.title('Startup / watchdog — Tkinter Edition')
        frame = self.ui.Frame(window, padding=18)
        frame.pack(fill='both', expand=True)
        self.startup_label = self.ui.Label(frame, text=self.startup_description, wraplength=550)
        self.startup_label.pack(anchor='w', pady=8)
        self.ui.Label(frame, text='Registers a separate task for this Windows user at logon.\n'
            'Restarts after failures (1 minute, up to 999 attempts); no 72-hour time limit.\n'
            'Runs on battery. Normal Exit does not trigger a restart.\n'
            'Run install.bat as administrator for elevated GPU mitigation.\n'
            'A standard-user task retains shutdown-only fallback. No upstream task is modified.',
            wraplength=550, justify='left').pack(anchor='w', pady=8)
        for label, action in [('Install for current user', 'install'), ('Enable', 'enable'),
                              ('Disable', 'disable'), ('Remove task', 'remove'), ('Refresh', 'status')]:
            self.ui.Button(frame, text=label, command=lambda action=action: self.startup_action(action),
                           state='disabled' if self.preview and action != 'status' else 'normal').pack(fill='x', pady=3)
        self.ui.Button(frame, text='Close', command=window.destroy).pack(anchor='e', pady=8)
        self.startup_action('status')

    def show_settings(self):
        window = tk.Toplevel(self.root)
        window.title('Protection settings')
        frame = self.ui.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        values = settings_service.editable()
        variables = {}
        for row, (key, value) in enumerate(values.items()):
            self.ui.Label(frame, text=key.replace('_', ' ').capitalize()).grid(row=row, column=0, sticky='w', padx=6, pady=3)
            variables[key] = tk.StringVar(window, value=str(value))
            if key in ('response_mode', 'sensor_backend'):
                choices = guard.VALID_RESPONSE_MODES if key == 'response_mode' else guard.VALID_SENSOR_BACKENDS
                control = self.ui.Combobox(frame, textvariable=variables[key], values=choices, state='readonly')
            else:
                control = self.ui.Entry(frame, textvariable=variables[key], width=24)
            control.grid(row=row, column=1, padx=6, pady=3)
        row = len(values)
        self.ui.Label(frame, text='Threshold, sustained time and response apply live.\n'
            'Backend and advanced tier settings apply at next launch.\n'
            'Higher current thresholds / longer trigger times reduce protection.', justify='left').grid(
                row=row, column=0, columnspan=2, pady=12)
        def save():
            try:
                result = settings_service.validate({key: value.get() for key, value in variables.items()})
                defaults = dict(threshold_amps=guard.DEFAULT_THRESHOLD_AMPS,
                                sustained_seconds_required=guard.DEFAULT_SUSTAINED_SECONDS_REQUIRED, **guard.DEFAULT_TIER_VALUES)
                weaker = [key for key in ('threshold_amps', 'sustained_seconds_required', 'critical_amps',
                          'critical_seconds', 'catastrophic_amps', 'mitigation_grace_seconds') if result[key] > defaults[key]]
                if weaker and not messagebox.askyesno('Reduced protection',
                    'Values above upstream defaults: ' + ', '.join(weaker)
                    + '. This can allow overheating, hardware damage or fire. Apply?', parent=window):
                    return
                settings_service.apply(result)
                self.mode.set(guard.get_response_mode())
                window.destroy()
            except (ValueError, OSError) as error:
                messagebox.showerror('Settings not applied', str(error), parent=window)
        self.ui.Button(frame, text='Apply', command=save, state='disabled' if self.preview else 'normal').grid(row=row+1, column=0)
        self.ui.Button(frame, text='Cancel', command=window.destroy).grid(row=row+1, column=1)

    def reset_defaults(self):
        if self.preview or not messagebox.askyesno('Reset protection settings?',
            'Restore all upstream protection defaults? Backend and advanced tiers take effect at next launch.', parent=self.root):
            return
        try:
            settings_service.apply(dict(threshold_amps=guard.DEFAULT_THRESHOLD_AMPS,
                sustained_seconds_required=guard.DEFAULT_SUSTAINED_SECONDS_REQUIRED,
                sensor_backend=guard.DEFAULT_SENSOR_BACKEND, response_mode=guard.DEFAULT_RESPONSE_MODE,
                **guard.DEFAULT_TIER_VALUES))
            self.mode.set(guard.get_response_mode())
        except (ValueError, OSError) as error:
            messagebox.showerror('Reset failed', str(error), parent=self.root)

    def export_diagnostics(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='Save local diagnostics',
                                           defaultextension='.json', filetypes=[('JSON', '*.json')])
        if not path:
            return
        state = guard.get_state()
        report = dict(edition='Tkinter Edition', upstream_engine=guard.APP_VERSION,
                      preview=self.preview, protection=state, settings=settings_service.current(),
                      pending_settings=guard.load_config(),
                      rails=self.rail_monitor.snapshot(), gpu=self.gpu_monitor.snapshot(),
                      startup=self.startup_description,
                      note='Local snapshot; no claim of physical protection or firmware enforcement.')
        try:
            Path(path).write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        except (OSError, ValueError) as error:
            messagebox.showerror('Export failed', str(error), parent=self.root)

    def show_diagnostics(self):
        window = tk.Toplevel(self.root)
        window.title('Diagnostics & logs')
        frame = self.ui.Frame(window, padding=18)
        frame.pack(fill='both', expand=True)
        state = guard.get_state()
        self.ui.Label(frame, text=f"Protection source: {state.get('source') or 'Unavailable'}\n"
            f"Mitigation: {state.get('mitigation') or 'Not checked'}\n"
            f"Last message: {state.get('last_message', '')}", wraplength=550, justify='left').pack(anchor='w', pady=8)
        def open_folder():
            try:
                Path(guard.LOG_DIR).mkdir(exist_ok=True)
                os.startfile(guard.LOG_DIR)
            except OSError as error:
                messagebox.showerror('Logs unavailable', str(error), parent=window)
        def event_viewer():
            try:
                os.startfile(str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'eventvwr.msc'))
            except OSError as error:
                messagebox.showerror('Event Viewer unavailable', str(error), parent=window)
        for label, command in [('Save local JSON snapshot…', self.export_diagnostics),
                               ('Open current log', guard.open_log),
                               ('Open logs & flight recordings folder', open_folder),
                               ('Windows Event Viewer', event_viewer)]:
            self.ui.Button(frame, text=label, command=command).pack(fill='x', pady=3)
        self.ui.Label(frame, text='Snapshots remain local. Logs may include hardware details; review before sharing.\n'
            'Read-only and explicit mitigation test tools are included in tools/.\n'
            'Emulator overloads can trigger a real shutdown; do not test against an armed guard.',
            wraplength=550, justify='left').pack(anchor='w', pady=12)
        self.ui.Button(frame, text='Close', command=window.destroy).pack(anchor='e')

    def change_display_scale(self, value):
        maximum = float(value)
        self.scale_label.configure(text=f"Gauge scale: 0–{maximum:.1f} A (display only)")
        for gauge in self.gauges:
            gauge.configure(maximum=maximum)
        if self.scale_key:
            self.model_scales[self.scale_key] = maximum

    def save_display_scale(self, event=None):
        if self.preview or not self.scale_key:
            return
        try:
            self.preferences_path.write_text(json.dumps(self.model_scales, indent=2), encoding="utf-8")
        except OSError as error:
            messagebox.showerror("Display settings", f"Could not save gauge scale: {error}", parent=self.root)

    def show_about(self):
        window = tk.Toplevel(self.root)
        window.title("About 12VHPWR Guard")
        window.resizable(False, False)
        window.transient(self.root)
        frame = self.ui.Frame(window, padding=24)
        frame.pack(fill="both", expand=True)
        self.ui.Label(frame, text="12VHPWR Guard", style="Title.TLabel").pack(anchor="w")
        self.ui.Label(frame, text="Tkinter Edition · Based on upstream v1.3.1").pack(anchor="w", pady=(0, 18))
        self.ui.Label(frame, text="Original author: humza-khalid\nTkinter adaptation: KyotoBlazeDev\n"
                  "Development assistance: Claude and other AI tools",
                  justify="left").pack(anchor="w")
        self.ui.Label(frame, text="Based on the open-source 12VHPWR Guard project by humza-khalid. "
                  "This edition introduces a Tkinter desktop interface for GPU monitoring and diagnostics.",
                  wraplength=550, justify="left").pack(anchor="w", pady=14)
        self.ui.Label(frame, text="License: MIT\nUpstream copyright: © 2025 12VHPWR Guard Contributors",
                  justify="left").pack(anchor="w", pady=(0, 14))
        self.ui.Label(frame, text="Original repository:").pack(anchor="w")
        self.ui.Button(frame, text="https://github.com/humza-khalid/12vhpwr-guard", command=lambda: webbrowser.open(
            "https://github.com/humza-khalid/12vhpwr-guard")).pack(anchor="w")
        self.ui.Label(frame, text="This adaptation is an independent project and is not an official upstream release.",
                  wraplength=550, justify="left").pack(anchor="w", pady=(14, 0))
        footer = self.ui.Frame(frame)
        footer.pack(fill="x", pady=(16, 0))
        try:
            logo = tk.PhotoImage(master=window, file=str(
                Path(__file__).resolve().parent / "assets" / "pwrdLogo150.gif"))
            logo_label = self.ui.Label(footer, image=logo)
            logo_label.image = logo  # Retain the Tk image for this window's lifetime.
            logo_label.pack(side="left")
        except tk.TclError:
            self.ui.Label(footer, text="Tcl powered").pack(side="left")
        self.ui.Button(footer, text="Close", command=window.destroy).pack(side="right", anchor="s")

    def show_warnings(self):
        window = tk.Toplevel(self.root)
        window.title("Warnings, compatibility & license")
        window.geometry("780x620")
        window.transient(self.root)
        frame = self.ui.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", font=("Segoe UI", 11), padx=12, pady=12)
        scroll = self.ui.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        content = WARNING_DETAILS
        base = Path(__file__).resolve().parent
        for filename in ("UPSTREAM_README.md", "LICENSE"):
            try:
                original = (base / filename).read_text(encoding="utf-8")
                if filename == "UPSTREAM_README.md":
                    original = original[original.index("## Disclaimer & Legal Notice"):]
                content += "\n\n" + original
            except (OSError, ValueError):
                content += f"\n\n{filename} unavailable. See {UPSTREAM_URL}"
        text.insert("1.0", content)
        text.configure(state="disabled")
        self.ui.Button(window, text="Close", command=window.destroy).pack(pady=(0, 12))

    def refresh(self):
        self.cancel_refresh()
        self.drain_commands()
        if self.closing:
            return
        if self.monitor_failed.is_set() and not self.closing:
            self.exit_code = 1
            self.closing = True
            self.stop_event.set()
            self.finish_close()
            return
        state = guard.get_state()
        paused = state.get("paused", False)
        stamp = state.get("sample_time")
        age = time.monotonic() - stamp if stamp is not None else None
        fresh = (not self.preview and not paused and age is not None and 0 <= age <= guard.RESUME_GRACE_SEC
                 and state["status"] not in (guard.Status.ERROR, guard.Status.MISSING))
        status = "PREVIEW — monitoring is off" if self.preview else "PAUSED — protection is off" if paused else state["status"]
        acquisition, meaning = acquisition_state(state, fresh)
        self.refresh_metrics(not self.preview and not paused)
        rail_live, rail_message = rail_display(self.rail_monitor.snapshot(), not self.preview and not paused)
        if self.rail_text.get('1.0', 'end-1c') != rail_message:
            position = self.rail_text.yview()[0]
            self.rail_text.configure(state="normal")
            self.rail_text.delete('1.0', 'end')
            self.rail_text.insert('1.0', rail_message)
            self.rail_text.configure(state="disabled")
            self.rail_text.yview_moveto(position)
        if not self.preview and not paused:
            gpu = getattr(self, 'selected_gpu', None)
            gpu_live = gpu and any(value is not None for value in gpu.get('metrics', {}).values())
            status = acquisition if acquisition == "LIVE" or not (rail_live or gpu_live) else "TELEMETRY — per-pin protection unavailable"
            if state['status'] in (guard.Status.OVER, guard.Status.MITIGATED, guard.Status.SHUTDOWN):
                status += f" | {state['status']}"
        self.status.configure(text=status)
        self.tray.update(f"12VHPWR Guard: {status}" +
                         (f" | max {state['max_val']:.2f} A | {state.get('source', '')}" if fresh else ''))
        self.message.configure(text="Layout preview; no sensor access or protection actions." if self.preview else
                               "Protection is off while paused." if paused else meaning + ". " + state["last_message"])
        pins = {label: (value, unit) for label, value, unit in state.get("pins", [])} if fresh else {}
        model = state.get("gpu_model")
        self.model_label.configure(text=f"GPU: {state.get('gpu_name') or 'Unavailable'}"
                                   + (f" [{model}]" if model else " — model not reported by source"))
        if model != self.scale_key:
            self.scale_key = model
            self.scale_value.set(self.model_scales.get(model, 20))
            self.change_display_scale(self.scale_value.get())
        self.scale_slider.configure(state="normal" if model and not self.preview else "disabled")
        volts = state.get("pin_voltages", []) if fresh else []
        self.voltage_toggle.configure(state="normal" if len(volts) == 6 else "disabled")
        for index, (label, widget) in enumerate(zip(guard.PIN_LABELS, self.readings)):
            value, unit = pins.get(label, (None, "A"))
            valid = value is not None and math.isfinite(value)
            widget.configure(text=f"{value:.2f} {unit}" if valid else "— A")
            self.gauges[index].configure(value=value if valid else 0)
            voltage = volts[index] if len(volts) == 6 else None
            self.voltage_labels[index].configure(text=(f"{voltage:.2f} V" if self.show_voltage.get()
                and voltage is not None and math.isfinite(voltage) else ""))
        threshold, sustained = guard.get_config_values()
        tiers = guard.get_tier_values()
        source = state.get("source") or "Unavailable"
        mitigation = state.get("mitigation") or "Not yet checked"
        configured_mode = guard.get_response_mode()
        effective = ("shutdown_only" if guard.effective_shutdown_only() else
                     "tiered" if mitigation == "ready" else "tiered (GPU limiting not yet verified)")
        self.details.configure(text=(f"Sensor source: {source}   |   Sample: "
            + (f"{age:.1f}s ago" if fresh else "Unavailable / stale")
            + f"\nTier 1: {threshold:g} A / {sustained:g} s   •   Tier 2: {tiers['critical_amps']:g} A / {tiers['critical_seconds']:g} s"
            + f"   •   Tier 3: {tiers['catastrophic_amps']:g} A"
            + f"\nResponse setting: {configured_mode}   |   Effective: {effective}"
            + f"\nGPU mitigation: {mitigation}"))
        if source == 'HWiNFO':
            self.details.configure(text=self.details.cget('text') + '\nHWiNFO64 Free: Shared Memory Support — 12-HOUR LIMIT.')
        self.pause.configure(text="Resume monitoring" if paused else "Pause monitoring",
                             state="disabled" if self.preview else "normal")
        if self.overlay is not None:
            self.update_overlay(state, fresh, status)
        if not self.closing:
            self.refresh_id = self.root.after(250, self.refresh)

    def close(self):
        if self.closing:
            return
        if not self.preview and not messagebox.askyesno("Stop protection?",
                "Exiting stops monitoring and attempts to release active GPU mitigation. "
                "A previous manual clock lock cannot be restored. Exit?", parent=self.root):
            return
        self.closing = True
        self.stop_event.set()
        self.status.configure(text="Stopping monitoring and releasing sensor resources…")
        self.finish_close()

    def cancel_refresh(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if self.refresh_id is not None:
            try:
                self.root.after_cancel(self.refresh_id)
            except tk.TclError:
                pass
            self.refresh_id = None

    def finish_close(self):
        if any(worker and worker.is_alive() for worker in (self.worker, self.rail_worker, self.gpu_worker, *self.jobs)):
            self.root.after(100, self.finish_close)
        else:
            self.cancel_refresh()
            self.tray.stop()
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="Show UI without starting the monitoring engine")
    parser.add_argument("--ui", choices=("ttk", "tk"), help="Override saved interface style")
    parser.add_argument("--tray", action="store_true", help="Start in tray when tray support is available")
    args = parser.parse_args()
    root = tk.Tk()
    if not args.preview and not guard.acquire_single_instance():
        root.withdraw()
        messagebox.showinfo("12VHPWR Guard", "A copy of the guard is already running.", parent=root)
        root.destroy()
        return
    app = GuardWindow(root, preview=args.preview, ui_style={"ttk": "TTK", "tk": "Tk Classic"}.get(args.ui))
    app.start()
    if args.tray and not args.preview:
        root.after(1000, lambda: app.hide_to_tray(quiet=True))
    root.mainloop()
    return app.exit_code


if __name__ == "__main__":
    sys.exit(main())
