"""Safe Tkinter launcher for 12VHPWR Guard diagnostics.

Read-only checks are always available. GPU-changing actions stay disabled until this
process is elevated and nvidia-smi identifies a supported ASUS ROG Astral subsystem.
"""

import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

SUPPORTED_SUBSYSTEMS = {
    0x89EA1043: "ROG Astral RTX 5090D OC",
    0x8A611043: "ROG Astral RTX 5090 Matrix",
    0x89EC1043: "ROG Astral RTX 5090 LC",
    0x89E31043: "ROG Astral RTX 5090 OC",
    0x89DE1043: "ROG Astral RTX 5080 OC",
    0x8A2E1043: "ROG Astral RTX 5090 OC White",
    0x8A2B1043: "ROG Astral RTX 5080 OC White",
    0x8A451043: "ROG Astral RTX 5080 OC Hatsune Miku",
    0x8A5A1043: "ROG Astral RTX 5090 BTF OC",
    0x8A3C1043: "ROG Astral RTX 5090 BTF OC",
    0x89DF1043: "ROG Astral RTX 5080 (non-OC)",
}


def is_elevated():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def find_nvidia_smi():
    found = shutil.which("nvidia-smi")
    if found:
        return found
    candidate = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe"
    )
    return candidate if os.path.exists(candidate) else None


def run_process(args):
    proc = subprocess.run(
        args,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.rstrip()


def inspect_system():
    lines = ["=== System check ===", f"Python: {sys.version.split()[0]}"]
    elevated = is_elevated()
    lines.append(f"Elevated: {elevated}")

    nvml_locations = [
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll"),
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "NVIDIA Corporation", "NVSMI", "nvml.dll",
        ),
    ]
    for path in nvml_locations:
        lines.append(f"NVML: {'FOUND' if os.path.exists(path) else 'missing'} - {path}")

    smi = find_nvidia_smi()
    if smi is None:
        lines.append("nvidia-smi: not found")
        lines.append("Supported Astral detected: no")
        return "\n".join(lines), None, elevated

    lines.append(f"nvidia-smi: {smi}")
    rc, output = run_process([
        smi,
        "--query-gpu=name,driver_version,pci.sub_device_id",
        "--format=csv,noheader",
    ])
    if rc != 0:
        lines.append(f"GPU query failed (exit {rc}):\n{output}")
        lines.append("Supported Astral detected: no")
        return "\n".join(lines), None, elevated

    supported = None
    lines.append("GPU inventory:")
    for raw_line in output.splitlines():
        lines.append(f"  {raw_line}")
        final_field = raw_line.rsplit(",", 1)[-1].strip()
        try:
            subsystem = int(final_field, 0)
        except ValueError:
            continue
        if subsystem in SUPPORTED_SUBSYSTEMS:
            supported = (subsystem, SUPPORTED_SUBSYSTEMS[subsystem])

    if supported:
        lines.append(
            f"Supported Astral detected: yes - {supported[1]} (0x{supported[0]:08X})"
        )
    else:
        lines.append("Supported Astral detected: no")
    return "\n".join(lines), supported, elevated


class DiagnosticApp:
    def __init__(self, root):
        self.root = root
        self.root.title("12VHPWR Guard Diagnostics")
        self.root.minsize(780, 560)
        self.events = queue.Queue()
        self.busy = False
        self.supported_gpu = None
        self.elevated = False

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="12VHPWR Guard Diagnostics",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="Read-only checks are safe on unsupported GPUs. Mitigation is hardware-gated.",
        ).pack(anchor="w", pady=(0, 10))

        controls = ttk.Frame(outer)
        controls.pack(fill="x")

        self.read_buttons = []
        for label, command in (
            ("Refresh system check", self.refresh),
            ("Run regression tests", self.run_tests),
            ("Probe Astral sensors (read-only)", self.probe_sensors),
            ("Show mitigation status (read-only)", self.mitigation_status),
        ):
            button = ttk.Button(controls, text=label, command=command)
            button.pack(side="left", padx=(0, 8), pady=(0, 8))
            self.read_buttons.append(button)

        actions = ttk.LabelFrame(outer, text="GPU-changing actions", padding=8)
        actions.pack(fill="x", pady=(2, 8))
        self.engage_button = ttk.Button(
            actions, text="10-second engage/release test", command=self.engage_test
        )
        self.engage_button.pack(side="left", padx=(0, 8))
        self.recover_button = ttk.Button(
            actions, text="Recover leftover mitigation", command=self.recover
        )
        self.recover_button.pack(side="left")

        self.status = tk.StringVar(value="Checking system...")
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=(0, 6))

        self.output = scrolledtext.ScrolledText(
            outer, wrap="word", font=("Consolas", 10), state="disabled"
        )
        self.output.pack(fill="both", expand=True)

        self._update_action_state()
        self.root.after(100, self._poll)
        self.refresh()

    def _append(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _start(self, title, worker):
        if self.busy:
            return
        self.busy = True
        self.status.set(title)
        self._append(f"\n{title}")
        self._update_action_state()

        def run():
            try:
                self.events.put(("done", worker()))
            except Exception as ex:
                self.events.put(("error", f"{type(ex).__name__}: {ex}"))

        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self.busy = False
                if kind == "system":
                    report, self.supported_gpu, self.elevated = payload
                    self._append(report)
                    self.status.set("System check complete")
                elif kind == "done":
                    rc, output = payload
                    self._append(output or "(no output)")
                    self._append(f"Exit code: {rc}")
                    self.status.set("Complete" if rc == 0 else f"Failed (exit {rc})")
                else:
                    self._append(payload)
                    self.status.set("Diagnostic failed")
                self._update_action_state()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _update_action_state(self):
        read_state = "disabled" if self.busy else "normal"
        for button in self.read_buttons:
            button.configure(state=read_state)

        allowed = not self.busy and self.supported_gpu is not None and self.elevated
        self.engage_button.configure(state="normal" if allowed else "disabled")
        marker = os.path.join(BASE_DIR, "mitigation.active")
        self.recover_button.configure(
            state="normal" if allowed and os.path.exists(marker) else "disabled"
        )

        if self.busy:
            return
        if self.supported_gpu is None:
            self.status.set("Read-only mode: no supported ASUS ROG Astral detected")
        elif not self.elevated:
            self.status.set("Read-only mode: restart as Administrator for mitigation testing")
        else:
            self.status.set(f"Hardware actions enabled: {self.supported_gpu[1]}")

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.status.set("Checking system...")
        self._update_action_state()

        def run():
            try:
                self.events.put(("system", inspect_system()))
            except Exception as ex:
                self.events.put(("error", f"{type(ex).__name__}: {ex}"))

        threading.Thread(target=run, daemon=True).start()

    def run_tests(self):
        self._start(
            "Running regression tests...",
            lambda: run_process([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
        )

    def probe_sensors(self):
        self._start(
            "Probing Astral sensors (read-only)...",
            lambda: run_process([sys.executable, os.path.join(TOOLS_DIR, "astral_poc.py"), "--once", "--raw"]),
        )

    def mitigation_status(self):
        self._start(
            "Reading mitigation status...",
            lambda: run_process([sys.executable, os.path.join(TOOLS_DIR, "mitigation_poc.py")]),
        )

    def engage_test(self):
        if self.supported_gpu is None or not self.elevated:
            return
        if not messagebox.askyesno(
            "Confirm GPU mitigation test",
            "This will lock the supported GPU to its floor clock and minimum power "
            "limit for 10 seconds, then restore it.\n\nSave your work, close games and GPU "
            "workloads, and continue only at the desktop.\n\nRun the test?",
            parent=self.root,
        ):
            return
        self._start(
            "Running 10-second mitigation round trip...",
            lambda: run_process([
                sys.executable,
                os.path.join(TOOLS_DIR, "mitigation_poc.py"),
                "--engage-test",
                "--seconds", "10",
            ]),
        )

    def recover(self):
        if self.supported_gpu is None or not self.elevated:
            return
        if not messagebox.askyesno(
            "Recover GPU state",
            "Attempt to restore clocks and the saved power limit from mitigation.active?",
            parent=self.root,
        ):
            return
        self._start(
            "Recovering leftover mitigation...",
            lambda: run_process([
                sys.executable, os.path.join(TOOLS_DIR, "mitigation_poc.py"), "--recover"
            ]),
        )


def main():
    root = tk.Tk()
    DiagnosticApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
