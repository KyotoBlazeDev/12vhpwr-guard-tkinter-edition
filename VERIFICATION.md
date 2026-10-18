# Tkinter Edition verification — 2026-10-18

## Passed

- `venv\Scripts\python.exe -m unittest discover -s tests -q`: **39 tests, OK, exit 0** after the event-message resource fix.
- Tests cover original safety regressions, fresh/stale and zero-value handling, model display scales, independent rail/GPU telemetry, multi-GPU parsing, getter-only NVML access, worker failure signaling, configuration validation and atomic persistence, task ownership and XML/watchdog settings, local diagnostics export, overlay rendering, all auxiliary windows and switching between TTK and Classic widgets.
- Both UI styles were constructed and updated at an 800 × 540 dashboard size with a scrollable content area and no captured Tk callback exceptions.
- Preview isolates all three monitoring workers and automatic tray startup; settings mutation is disabled. Explicit test-only tray startup is separate from the default preview path.
- Declared dependencies installed into the project-local `venv`: pywin32 312, winotify 1.1.0, pystray 0.19.5, Pillow 13.0.0 and six 1.17.0. The launcher prefers this environment.
- `install.ps1 -PrepareOnly` completed import checks and returned success without registering a task or starting monitoring.
- PowerShell parser accepted install.ps1, uninstall.ps1 and Launch.ps1.
- Python compilation accepted the new frontend, telemetry, settings, startup, overlay and tray modules.
- Preview-only Windows shell smoke outside the sandbox: tray started, its shell icon became visible, overlay contained 28 rendered canvas items, no callback errors were captured, and the protection worker remained unstarted. The test then closed the windows and stopped the tray.
- Windows Task Scheduler COM API accepted the generated task XML into an **in-memory** task definition: restart count 999, execution time limit PT0S, elevated run level and one action. No task was registered.
- Windows LoadLibraryEx/FormatMessage successfully rendered every event ID emitted by the guard using the bundled resource-only DLL, including 1000/1003 and Unicode/percent insertion text. The repair script passed PowerShell syntax parsing. Registry repair was not applied in this run: the session is not elevated. No event record was emitted, deleted or rewritten for these resource tests.

## Not verified in this run

- A read-only GPU query found no NVIDIA-SMI or NVML driver library in the supported driver locations, including outside the sandbox. New general GPU parsing and NVML bindings are tested with fixtures/fakes, not live sensor results on this host.
- No protection-engine monitoring session, GPU clock/power-limit change, mitigation recovery experiment, emergency shutdown or physical connector test was run.
- No Task Scheduler installation/removal, logon launch, elevation/standard-account behavior or restart-after-process-failure experiment was executed against registered tasks. XML/API definition checks do not prove these runtime behaviors.
- Toast delivery and Event Viewer entry delivery were not exercised; their dependencies imported successfully. No unsolicited notification was sent.
- Earlier RTX 5060 rail measurements retained in README.md are historical project notes, not measurements reproduced in this run.

The software feature implementation and its automated checks are complete for the documented local v1.3.1 baseline. Hardware capability and physical enforcement remain separate validation work.

## Clone migration — 2026-10-18

Transferred into the cloned KyotoBlazeDev/12vhpwr-guard-tkinter-edition repository. Preserved its initial Git history, remote, adaptation copyright and upstream MIT notice. Recreated the local virtual environment, verified copied source/test/tool/asset hashes, and reran all 39 tests successfully from the cloned location. Dependency check passed. Local preferences, logs, runtime and Visual Studio state are excluded from Git. The OneDrive source remains as a backup. No commit, push, startup-task installation or event-source registration was performed during migration.

## Commit review fixes — 2026-10-18

- Regression suite: **46 tests, OK** after fixing incomplete/duplicate/wrong-unit HWiNFO samples, acquisition failures retaining the mitigation recovery window, and GPU identity fallback after explicit mismatches.
- Recovery tests drive the monitoring loop with fake sensor failures and mocked response evaluation; GPU matching tests use fake NVML enumeration. Multi-GPU mitigation now falls back to shutdown-only until physical-card identity is available.
- No live monitoring, GPU-setting calls, emergency shutdown, task registration, or physical enforcement experiment was performed for these fixes.
