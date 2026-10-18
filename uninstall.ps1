$ErrorActionPreference = 'Stop'
$basePath = $PSScriptRoot
$pythonPath = Join-Path $basePath 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = 'python' }
& $pythonPath (Join-Path $basePath 'startup_manager.py') remove
if ($LASTEXITCODE -ne 0) { throw 'Task removal failed; see output.' }
Write-Host 'Startup task removed. Exit the dashboard/tray to stop this session. Source, settings and logs preserved.'
