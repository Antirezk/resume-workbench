param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskUrl = 'http://127.0.0.1:8765'
try {
    $taskHealth = Invoke-RestMethod -Uri ($taskUrl + '/health') -TimeoutSec 2
    if ($taskHealth.ok -and $taskHealth.application -eq 'resume-workbench') {
        if (-not $NoBrowser) { Start-Process -FilePath $taskUrl }
        Write-Output ('Already running: ' + $taskUrl)
        exit 0
    }
} catch { }
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskBootstrap = Get-Command python -ErrorAction SilentlyContinue
    if (-not $taskBootstrap) { throw 'Python 3.10 or newer is required. Install from python.org first.' }
    & $taskBootstrap.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
$taskRequirements = Join-Path $taskRoot 'requirements.lock.txt'
if (-not (Test-Path -LiteralPath $taskRequirements)) { $taskRequirements = Join-Path $taskRoot 'requirements.txt' }
$taskMarker = Join-Path $taskRoot '.venv\requirements.sha256'
$taskHash = (Get-FileHash -LiteralPath $taskRequirements -Algorithm SHA256).Hash
$taskInstalled = if (Test-Path -LiteralPath $taskMarker) { (Get-Content -LiteralPath $taskMarker -Raw).Trim() } else { '' }
if ($taskInstalled -ne $taskHash) {
    & $taskPython -m pip install --disable-pip-version-check -r $taskRequirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and try again.' }
    Set-Content -LiteralPath $taskMarker -Value $taskHash -Encoding ascii
}
$taskArguments = @('app.py')
if ($NoBrowser) { $taskArguments += '--no-browser' }
& $taskPython @taskArguments
