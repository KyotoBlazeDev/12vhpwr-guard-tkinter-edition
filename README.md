# 12VHPWR Guard: Tkinter Edition
This software with a GUI and overlay lets you monitor and shut down if GPU pins are overheating. No safety was changed; this is made for a new repository. A short name was introduced as _12VHPWR: GTE_

## Implemented feature set

| Feature | Tkinter Edition |
| --- | --- |
| Protection engine | Local upstream v1.3.1 tiered response, mitigation recovery, emergency shutdown, flight recorder and pause reminders |
| Main UI | Switchable TTK / Tk Classic, scrollable dashboard, six pin gauges, model display scale and optional pin voltages |
| Background operation | Tray icon, status color/tooltip, restore dashboard, overlay, pause/resume and Exit |
| Startup and watchdog | Separate current-user logon task; enable/disable/remove/refresh controls and installer/uninstaller |
| Settings | Current threshold, sustained time, response mode, backend selection, advanced tiers and reset defaults |
| Overlay | Draggable, always on top, scalable, six pin gauges, selected GPU metrics and reported rail voltages |
| General GPU metrics | Board power, graphics clock, VRAM, load, temperature and core voltage when reported |
| Multi-GPU display | Explicit name/PCI-bus selection; display telemetry never arms pin protection |
| Diagnostics | Local JSON snapshot, logs, flight recordings folder and Event Viewer shortcuts |
| Notifications | Upstream toast notifications and Windows Event Viewer support with installed dependencies |
| Attribution | About credits, independent-project notice, retained MIT license and Tcl Powered logo |
| Development tools | Original diagnostic launcher, read-only Astral probe, explicit mitigation tests and HWiNFO emulator under tools/ |

The application feature set is implemented. Hardware support remains bounded by the available backends; this does not add per-pin sensors to other cards or validate physical protection. The source is based on the local v1.3.1 copy, not a synchronization with later upstream versions.

## Run and install

The project-local `venv` has been prepared with the declared dependencies. `Launch.bat` prefers that environment, then the bundled runtime, then Python on PATH. **Normal launch starts real monitoring**, including the inherited GPU-limiting and emergency-shutdown responses. Use `Launch.bat --preview` to inspect the UI with all monitoring workers and tray startup disabled. Protection-setting changes are disabled in preview.

- `Launch.bat --ui ttk` or `Launch.bat --ui tk`: select an interface.
- `Launch.bat --tray`: start hidden only if tray support starts successfully; otherwise keep the dashboard visible.
- `install.bat -PrepareOnly`: create/repair the local environment and install dependencies, without installing a task or starting monitoring.
- `install.bat`: prepare dependencies and register **12VHPWR Guard Tkinter Edition** for this user at logon. Run as administrator for elevated GPU limiting. A standard-user task retains shutdown-only fallback.
- `install.bat -StartNow`: additionally start the registered task immediately; this starts real monitoring.
- `uninstall.bat`: remove this edition's startup task, retaining source, settings and logs. Exit the existing dashboard/tray to stop the current session and release mitigation.

Startup is **not enabled merely by preparing the environment**. No startup task was installed during this implementation. The GUI's Startup / watchdog window can install the current-user task, enable, disable, remove it, or query its state. It rejects a task belonging to another Tkinter Edition folder. The upstream task is never changed. If an older guard is installed, exit it and disable its startup through its own controls before enabling this edition, since both guards share the original single-instance mutex.

The task has no 72-hour time limit, runs on battery, and requests failure restarts every minute up to 999 attempts. An unexpected protection-worker failure closes this process with exit code 1 after cleanup, allowing Task Scheduler to restart it. A normal Exit returns 0 and deliberately does not restart. Failure recovery still depends on Task Scheduler/Windows functioning; an OS or driver hang is not covered by these checks.

## Settings, display and diagnostics

Threshold, sustained time and response-mode changes apply live only after the configuration is saved successfully. Invalid/non-finite values are rejected; increasing trigger thresholds/times above upstream defaults retains a reduced-protection confirmation. Backend and advanced tier changes, including resetting their defaults, apply at next launch to avoid changing an active mitigation deadline. Unknown INI keys are preserved.

Display settings control the optional overlay, its size and visible metrics. Availability follows the selected GPU's fresh data. Core voltage and other unsupported fields show Unavailable, without substituting inferred measurements. Pin telemetry remains separately sourced from the protection backend. The overlay can be dragged, hidden with its × control, or configured by right-clicking. Double-clicking it restores the dashboard. Hiding the dashboard to tray keeps all workers running; Exit stops them and waits for cleanup.

Diagnostics exports a local JSON snapshot with active/pending settings, source/status and independent telemetry snapshots. Logs and flight recordings are accessible from the diagnostics window. No upload occurs. Review exported hardware details and logs before public sharing.

### Event Viewer description repair

If a guard event shows “the message resource is present but the message was not found in the message table,” its event ID does not match the registered generic message resource. This edition now bundles a deterministic, resource-only `assets/guard_events_v1.dll` containing insertion-string templates for IDs 1000–9999. It has no executable code, imports or entry point. The original event IDs and text are retained. `eventlog_messages.py` rebuilds it without Windows SDK tools and verifies it using Windows FormatMessage; elevated startup/installation registers this specific message resource.

For an existing installation, close Event Viewer, right-click **repair_eventlog.bat → Run as administrator**, then reopen Event Viewer and your saved log. The repair verifies the registry's EventMessageFile value and neither clears nor rewrites any records. Archived events using this source and covered IDs can then be rendered using the local resource; archives opened on another computer still require the matching provider resource there. Keep the resource file at its registered location. The source name is shared with the original guard; launching an older elevated version can register its generic resource again.

Choose **Interface: TTK** for themed widgets or **Tk Classic** for classic Tk controls, including classic buttons, checkboxes, scales, option menus and canvas gauges. Switching rebuilds the interface while keeping the existing monitoring worker running. About and warnings follow the selected style. Normal runs save the choice in `ui_settings.json`; preview runs do not save changes. Launch with `Launch.bat --ui ttk` or `Launch.bat --ui tk` to override the saved choice. Native message and input dialogs retain platform styling.

Windows desktop adaptation of the local `12vhpwr-guard-main` source archive, whose engine reports version 1.3.1. Copied on 2026-10-18; the source folder is preserved. See LICENSE and UPSTREAM_README.md for the original project and hardware setup.

Upstream: [humza-khalid/12vhpwr-guard](https://github.com/humza-khalid/12vhpwr-guard). Its current README warnings were read through GitHub on 2026-10-18 (README blob `c0028666719f498914f6e68ceea58198abaa87f5`). This warning review does not synchronize the local engine with current upstream code.

**Compatibility and limitations:** upstream supports ASUS ROG Astral RTX 5080/5090 hardware with per-pin sensors. On BTF cards powered through GC-HPWR, the load bypasses those sensors: near-zero readings cannot establish protection. This software provides supplementary protection with no guarantee; keep firmware, driver and PSU protections enabled. Forced shutdown can lose unsaved work. Higher thresholds or longer delays reduce protection. GPU limiting requires elevation and replaces any existing manual clock lock, which cannot be restored on release. The GUI now includes a persistent warning panel and a scrollable **Warnings & compatibility** window containing the original disclaimer and MIT license. It distinguishes the configured response from shutdown-only fallback when mitigation is unavailable.

Double-click **Launch.bat** to open the dashboard and start real monitoring. Run `Launch.bat --preview` for a layout preview with no sensor access, GPU limiting, or shutdown actions. The launcher uses the installed Codex Python runtime when present, otherwise `python`. With a standard Python installation you can also run `python guard_tk.py`.

The six pin cards show available readings from the retained Astral direct backend or HWiNFO shared memory backend. Missing, paused, or stale samples display a dash. Unsupported GPUs do not gain per-pin telemetry from this interface. Source and GPU mitigation availability appear below the cards.

**HWiNFO64 Free (non-Pro) — SHARED MEMORY SUPPORT: 12-HOUR LIMIT.** This limit applies to HWiNFO's shared-memory interface, not to all monitoring in this application. Re-enable Shared Memory Support manually in HWiNFO when it expires; HWiNFO Pro removes the runtime limit. If the guard relies on HWiNFO for pin currents, shared-memory loss interrupts protection until valid readings return. The direct Astral NVAPI backend and independent NVAPI/NVML/NVIDIA-SMI telemetry do not use this interface. The application does not bypass the limit or show an estimated expiry countdown. See the [HWiNFO author's announcement](https://www.hwinfo.com/forum/threads/important-changes-to-hwinfo64-coming-soon.7092/).

Acquisition states: **LIVE** means all six valid, fresh pin currents are available; **UNAVAILABLE** means no per-pin source was found through the current backends, without claiming that the board lacks sensors; **ERROR** means compatible per-pin hardware/telemetry was previously identified but current acquisition is missing, invalid, incomplete or stale. **WAITING** is used while compatibility is unknown, including driver/API failures that prevent identification. **TELEMETRY — per-pin protection unavailable** means independent rail readings are live while the protection source is unavailable. Preview and pause remain explicit. Protection severity (OVER, MITIGATED, SHUTDOWN) is shown alongside LIVE rather than being hidden. Mitigation and shutdown thresholds are unchanged.

The separate **Rail telemetry** panel reads NVIDIA's private NVAPI power-monitor interface directly, without HWiNFO or its shared-memory time limit. It lists each enumerated GPU separately, with PCI bus identity, source, channel ID, rail label and driver channel type (including summed or estimated channels). These are driver-reported classifications, not physical verification of a sensor chip. Unknown rail IDs stay unknown; they are not guessed from the GPU model. Per-channel voltage and current are shown only for sensor/aligned-sensor types with plausible fields; aggregate and estimated channels show power only. Channels can overlap, so do not add every row to calculate total power. Scroll the panel to see additional rails.

Rail readings are display-only and are never passed to the protection engine or six pin gauges. Pause and preview hide them, failed acquisition clears old readings, and samples older than five seconds are hidden. Closing joins both monitoring workers. NVIDIA does not guarantee this private ABI; a failed query means unavailable through this backend, not proof that the hardware lacks sensors. No additional manufacturer, PSU or BTF motherboard interfaces are implemented. An earlier project note recorded a 2026-10-18 read-only probe of PCIe +12V and 8-pin #1 power, voltage and aggregate current on an RTX 5060 (driver 617.14, subsystem 2000:7377). The current execution environment has no accessible NVIDIA-SMI/NVML installation, so that historical result was not reverified in this implementation. This does not establish per-contact monitoring or physical connector safety. Reproduce a read-only probe using `python rail_telemetry.py`; it does not start the guard engine or bind GPU-setting functions.

Research references: [NVAPI public SDK](https://github.com/NVIDIA/nvapi), [power-monitor ABI implementation reference](https://github.com/JustAMan/pynvraw/blob/main/src/pynvraw/nvapi_api.py), [HWiNFO author on board-specific rails](https://www.hwinfo.com/forum/threads/new-beta-version-v6-27-4185-lots-of-extra-gpu-stuff.6460/), [ASUS per-pin monitoring](https://www.asus.com/us/blog/beyond-psu-protection-a-system-level-approach-to-pc-power-safety/), [ZOTAC sense-pin seating detection](https://www.zotac.com/kr/news/50-series-graphics-cards-%E2%80%93-12v-2x6-connector-and-power-safety), and [MSI PSU-side per-pin monitoring](https://www.msi.com/blog/msi-gpu-safeguard-msi-afterburner-protect-your-gpu-by-reducing-power-during-abnormal-current). Capability depends on board design, sensor location and accessible software interface.

The six gauges include a display-only 10–40 A scale slider. Scale preferences are saved per reported Astral model in `display_settings.json`; they do not change protection thresholds. The initial 20 A scale is a display convention, not a model safety rating. If the source does not identify a model (including the current HWiNFO adapter), the model slider is disabled. Direct Astral samples can also show reported per-pin voltage; its toggle is disabled when fresh voltage data is unavailable. This is per-pin sensor voltage, not a separately measured generic 12 V rail. General GPU metrics now have a separate read-only provider: NVIDIA-SMI XML queries, with public NVML getters as fallback. All returned GPUs retain their PCI bus identity. Unsupported individual fields remain unavailable. These values are independent of the per-pin protection source. The provider binds no clock/power-setting functions. See [NVIDIA-SMI documentation](https://docs.nvidia.com/deploy/nvidia-smi/index.html) and [NVML device queries](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlDeviceQueries.html).

GPU limiting and mitigation recovery currently require exactly one enumerated NVIDIA GPU. Explicit subsystem-ID mismatches disable limiting, and multi-GPU systems use shutdown-only fallback because subsystem IDs identify board models rather than physical cards. The independent multi-GPU telemetry display remains available. HWiNFO protection requires six unique valid current readings; missing, duplicate or wrong-unit entries invalidate the sample. Failed acquisition interrupts the continuous clean-reading window required to release mitigation.

The retained engine applies its original tiered mitigation and Windows shutdown responses when monitoring runs. The dashboard exposes the current threshold, sustained time, response mode, pause/resume, and log. Settings and logs belong to this edition's folder. Increasing thresholds retains the original confirmation dialog. Closing asks to stop protection, then waits for the engine to finish resource cleanup and mitigation release.

Tkinter owns the main thread; protection, rail acquisition, and general GPU acquisition run in separate workers. Tray callbacks queue commands for the Tk thread. The old script filename also opens the Tkinter dashboard and propagates its exit code. Tray support is optional for dashboard use and included in requirements.txt for the full application.

Optional notifications/Event Viewer dependencies are in requirements.txt. Backend and hardware requirements, permissions, tier semantics, and sensor setup remain documented in UPSTREAM_README.md. Software tests do not establish connector safety or verify physical GPU limiting/shutdown.

Verification: `python -m unittest discover -s tests -v`.


## Validation scope

See VERIFICATION.md for the current checks. Automated tests and a preview-only tray/overlay smoke test cover the implemented software behavior. No live mitigation, emergency shutdown, scheduled-task registration, logon launch, or restart-after-failure experiment was performed during this implementation. Compatible-hardware enforcement is unverified. Optional fields, manufacturer/PSU/BTF interfaces not exposed by these providers, and non-NVIDIA general GPU telemetry remain unavailable rather than fabricated.

## Credits and Attribution

### Original Project
**Humza Khalid (Human)**: Original 12VHPWR Guard developer.

**Claude (AI)**: AI-assisted development of the original project.

### Community GUI Adaptation
**Lunosama**: Developer of 12VHPWR Guard + Overlay, a graphical adaptation of the original watchdog.

### Tkinter Edition
**KyotoBlazeDev (Human):** Tkinter Edition developer, interface design, integration, and testing.

**OpenAI Codex (AI)**: AI-assisted implementation and development.

### Upstream Repositories
**Original:** https://github.com/humza-khalid/12vhpwr-guard

**GUI Overlay:** https://github.com/Lunosama/12vhpwr-guard-with-GUI

### License
Released under the MIT License. Original copyright notices and applicable upstream license terms are preserved.

This is an independent community adaptation, not an official release by the upstream developers.
