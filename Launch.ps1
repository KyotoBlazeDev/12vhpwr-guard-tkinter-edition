$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$venvPython = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    & $venvPython (Join-Path $PSScriptRoot 'guard_tk.py') @args
} elseif (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython (Join-Path $PSScriptRoot 'guard_tk.py') @args
} else {
    & python (Join-Path $PSScriptRoot 'guard_tk.py') @args
}
if ($LASTEXITCODE -ne 0) { throw "12VHPWR Guard exited with code $LASTEXITCODE" }
