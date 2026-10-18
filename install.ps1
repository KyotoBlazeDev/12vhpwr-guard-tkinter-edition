param([string]$Python, [switch]$StartNow, [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
$basePath = $PSScriptRoot
if (-not $Python) {
    $runtimePath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $Python = if (Test-Path -LiteralPath $runtimePath) { $runtimePath } else { 'python' }
}
& $Python -c "import sys, tkinter; assert sys.version_info >= (3, 10)"
if ($LASTEXITCODE -ne 0) { throw 'A working Python 3.10+ installation with Tkinter is required.' }
$venvPath = Join-Path $basePath 'venv'
$venvExe = Join-Path $venvPath 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvExe)) {
    & $Python -m venv --without-pip $venvPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not create local virtual environment.' }
}
& $venvExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
if ($LASTEXITCODE -ne 0) {
    & $venvExe -m ensurepip --upgrade --default-pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not bootstrap pip in the local virtual environment.' }
}
& $venvExe -m pip install --disable-pip-version-check -r (Join-Path $basePath 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Startup task was not changed.' }
Push-Location -LiteralPath $basePath
try {
    & $venvExe -c "import tkinter, pystray, PIL, guard_tk; print('Imports verified; monitoring not started.')"
    if ($LASTEXITCODE -ne 0) { throw 'Application import check failed.' }
    if ($PrepareOnly) {
        Write-Host 'Runtime and dependencies prepared. No task installed; monitoring not started.'
        return
    }
    $isAdministrator = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $taskArguments = @((Join-Path $basePath 'startup_manager.py'), 'install')
    if ($isAdministrator) { $taskArguments += '--highest' }
    & $venvExe @taskArguments
    if ($LASTEXITCODE -ne 0) { throw 'Task installation failed.' }
    if ($isAdministrator) {
        & $venvExe (Join-Path $basePath 'eventlog_messages.py') --register
        if ($LASTEXITCODE -ne 0) { throw 'Event source repair failed; startup task was installed but event descriptions need repair.' }
    } else {
        Write-Warning 'Installed with standard-user privileges. GPU limiting may be unavailable; shutdown-only fallback remains.'
    }
    if ($StartNow) {
        Start-ScheduledTask -TaskName '12VHPWR Guard Tkinter Edition'
    }
    Write-Host 'Tkinter Edition startup task installed. Configuration and logs preserved. Original upstream task untouched.'
} finally { Pop-Location }
