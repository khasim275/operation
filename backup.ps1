param(
    [string]$OutputPath = "backups"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Run .\run.ps1 once first to create the local Python environment."
}
& $python (Join-Path $root "backup.py") backup --output $OutputPath
if ($LASTEXITCODE -ne 0) { throw "CRM backup failed." }
