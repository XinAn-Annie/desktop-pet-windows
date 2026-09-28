$ErrorActionPreference = "Stop"

# Some Windows installations keep py.exe after the Python installation it
# points to has been removed. Prefer a real python.exe and validate it first.
$candidates = @()
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand -and (Test-Path -LiteralPath $pythonCommand.Source)) {
    $candidates += $pythonCommand.Source
}
$known = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" -ErrorAction SilentlyContinue
$candidates += $known.FullName

$pythonExe = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    try {
        & $candidate -c "import sys; print(sys.executable)" *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonExe = $candidate
            break
        }
    } catch {
        # Ignore broken Python aliases and continue searching.
    }
}

if (-not $pythonExe) {
    throw "No usable Python was found. Install Python 3.10+ from https://www.python.org/downloads/windows/ and enable Add python.exe to PATH."
}

Write-Host "Using Python: $pythonExe"
& $pythonExe -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Write-Host "Dependencies installed."
Write-Host "Install Tesseract OCR separately, including chi_sim and eng language packs."
Write-Host "Run: .\.venv\Scripts\python.exe app.py"
