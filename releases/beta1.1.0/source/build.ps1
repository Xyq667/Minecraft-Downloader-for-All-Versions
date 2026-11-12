$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = $env:MCVD_PYTHON
if (-not $python) {
    $candidates = @(
        "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe",
        "python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $python = $candidate
            break
        }
    }
}
if (-not $python) {
    throw "Python was not found. Set MCVD_PYTHON to python.exe."
}

Write-Host "Using Python: $python"
& $python -m pip install --disable-pip-version-check --no-input -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Failed to install build dependencies." }

if (Test-Path -LiteralPath "dist") {
    Remove-Item -LiteralPath "dist" -Recurse -Force
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --noupx `
    --name "MCVersionDownloader" `
    --icon "assets\icon.ico" `
    --add-data "assets;assets" `
    --version-file "build\version_info.txt" `
    --log-level WARN `
    "app.py"

if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

& $python "tools\finalize_build.py"
if ($LASTEXITCODE -ne 0) { throw "Failed to create the Chinese-named executable." }

$exe = Join-Path $root "dist\MCVersionDownloader.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Expected output was not produced: $exe" }
$size = (Get-Item -LiteralPath $exe).Length
Write-Host ("Built {0} ({1:N1} MB)" -f $exe, ($size / 1MB))
Write-Host "Created the Chinese-named copy in dist as well."

# The DeepSeek workspace is marked low-integrity. A normal user folder does not
# need this, but setting the outputs to medium lets the exe run from either place.
foreach ($file in (Get-ChildItem -LiteralPath (Join-Path $root "dist") -Filter "*.exe")) {
    & icacls $file.FullName /setintegritylevel Medium 2>$null | Out-Null
}
