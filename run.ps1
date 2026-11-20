param(
    [switch]$SkipDatabaseSetup
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$pythonCandidates = @(
    "$env:LocalAppData\Programs\Python\Python312\python.exe",
    "$env:LocalAppData\Programs\Python\Python311\python.exe"
)
$basePython = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $basePython) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notlike "*WindowsApps*") {
        $basePython = $pythonCommand.Source
    }
}
if (-not $basePython) {
    throw "Python 3.11 or newer was not found. Install Python, then run this script again."
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $basePython -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the local Python environment." }
}

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

$frontendDir = Join-Path $root "frontend"
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) {
    throw "Node.js 20 or newer with npm was not found. Install Node.js, then run this script again."
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendDir "node_modules") -PathType Container)) {
    & $npmCommand.Source --prefix $frontendDir ci
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
}
& $npmCommand.Source --prefix $frontendDir run build
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

$envFile = Join-Path $root ".env"
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination $envFile
    Write-Host "Created .env from .env.example. Add database credentials, APP_SECRET_KEY, OTP_PEPPER, ADMIN_EMAIL and ADMIN_PASSWORD, then run this command again."
    exit 1
}

if (-not $SkipDatabaseSetup) {
    & $venvPython (Join-Path $root "bootstrap.py")
    if ($LASTEXITCODE -ne 0) { throw "Database initialization failed. Check PostgreSQL and .env settings." }
}

$serverSettings = & $venvPython -c "from config import get_settings; settings=get_settings(); print(settings.app_host); print(settings.app_port)"
if ($LASTEXITCODE -ne 0 -or $serverSettings.Count -lt 2) { throw "Could not read APP_HOST and APP_PORT from .env." }
$serverHost = [string]$serverSettings[0]
$serverPort = [string]$serverSettings[1]
if ($serverHost -eq "0.0.0.0" -or $serverHost -eq "::") {
    $localHost = "127.0.0.1"
    $lanAddresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -ExpandProperty IPAddress -Unique
} else {
    $localHost = $serverHost
    $lanAddresses = @()
}
Write-Host "Starting Dental Hospital CRM on $serverHost`:$serverPort"
Write-Host "LOCAL PORTALS"
Write-Host "Patient Portal:       http://${localHost}:$serverPort/book"
Write-Host "Staff Workspace:      http://${localHost}:$serverPort/staff"
Write-Host "OTP & Medicine Portal: http://${localHost}:$serverPort/secure"
foreach ($address in $lanAddresses) {
    Write-Host "LAN portals for devices on this network ($address)"
    Write-Host "Patient Portal:       http://${address}:$serverPort/book"
    Write-Host "Staff Workspace:      http://${address}:$serverPort/staff"
    Write-Host "OTP & Medicine Portal: http://${address}:$serverPort/secure"
}
if (-not $lanAddresses -or $serverHost -notin @("0.0.0.0", "::")) {
    Write-Host "LAN access needs APP_HOST=0.0.0.0 and firewall permission for TCP $serverPort."
}
& $venvPython -m uvicorn app:app --host $serverHost --port $serverPort
if ($LASTEXITCODE -ne 0) { throw "The CRM server stopped with an error." }
