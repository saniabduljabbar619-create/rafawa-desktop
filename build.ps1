# Build RafawaApp.exe and (if Inno Setup is installed) the installer.
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

$version = & $python -c "from shared.version import __version__; print(__version__)"
Write-Host "Building Rafawa Desktop $version"

& $python -m pip install -r requirements.txt pyinstaller
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed - not building." }

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $python -m PyInstaller RafawaApp.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc "/DAppVersion=$version" installer.iss
    Write-Host "Installer: RafawaAppSetup.exe"
} else {
    Write-Host "Inno Setup not found - skipped installer (dist\RafawaApp.exe is ready)."
}
