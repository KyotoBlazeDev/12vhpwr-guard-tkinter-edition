$ErrorActionPreference = 'Stop'
$isAdministrator = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdministrator) { throw 'Right-click repair_eventlog.bat and choose Run as administrator.' }
$pythonPath = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Prepare the local environment using install.bat -PrepareOnly first.' }
& $pythonPath (Join-Path $PSScriptRoot 'eventlog_messages.py') --register
if ($LASTEXITCODE -ne 0) { throw 'Event source repair failed.' }
Write-Host 'Event source repaired and registry readback verified. Close and reopen Event Viewer, then reopen the saved log.'
Write-Host 'No event records were changed or cleared. Monitoring was not started.'
