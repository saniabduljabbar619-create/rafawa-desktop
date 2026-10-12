# Build RafawaApp.exe and the installer (RafawaAppSetup.exe).
#   powershell -ExecutionPolicy Bypass -File build.ps1              # tests -> exe -> installer
#   powershell -ExecutionPolicy Bypass -File build.ps1 -SkipTests   # same, without running the tests
#   powershell -ExecutionPolicy Bypass -File build.ps1 -ExeOnly     # just dist\RafawaApp.exe
# From VS Code: Terminal > Run Task > "Build installer" (see .vscode/tasks.json).
param(
    [switch]$SkipTests,
    [switch]$ExeOnly
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

$version = & $python -c "from shared.version import __version__; print(__version__)"
Write-Host "Building Rafawa Desktop $version"
if (Test-Path ".env") { Write-Host "Found .env - it will be bundled inside the exe (the exe carries its own database settings)." }
elseif (Test-Path ".env.template") { Write-Host "Found .env.template - it will be bundled inside the exe as .env." }
else { Write-Host "No .env here - the exe will carry the .env.example placeholder; put a real .env next to the exe." }

& $python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Installing the requirements failed." }

if (-not $SkipTests) {
    & $python -m pip install -r requirements-dev.txt
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed - not building." }
}

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $python -m PyInstaller RafawaApp.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
Write-Host "Executable: dist\RafawaApp.exe"
if ($ExeOnly) { return }

# Inno Setup: PATH, the usual install folders, or the per-user install
$candidates = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$iscc = $candidates | Select-Object -First 1
if ($iscc) {
    & $iscc "/DAppVersion=$version" installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
    Write-Host "Installer: RafawaAppSetup.exe"
} else {
    Write-Host "Inno Setup not found - install it (https://jrsoftware.org/isdl.php) to build the installer."
    Write-Host "dist\RafawaApp.exe is ready."
}
