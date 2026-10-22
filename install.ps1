# Bonesy's Song Player installer for Windows. Run in PowerShell:
#   irm https://raw.githubusercontent.com/bradleyevans1989-svg/yt-player/main/install.ps1 | iex
$ErrorActionPreference = 'Stop'
$Repo = 'bradleyevans1989-svg/yt-player'
$Dir = Join-Path $env:LOCALAPPDATA 'BonesySongPlayer'
$Name = "Bonesy's Song Player"

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Find-Python {
    Refresh-Path
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if (-not $launcher) { return $null }
    $exe = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
    return $null
}

Write-Host "Installing $Name..." -ForegroundColor Cyan

$python = Find-Python
if (-not $python) {
    Write-Host 'Installing Python (one time)...'
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements | Out-Null
    $python = Find-Python
    if (-not $python) { throw 'Python install did not finish. Close PowerShell, open a new one, and run the install line again.' }
}
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'

Write-Host 'Downloading the player...'
$zip = Join-Path $env:TEMP 'tunes.zip'
$extract = Join-Path $env:TEMP 'tunes-extract'
Invoke-WebRequest "https://github.com/$Repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Expand-Archive $zip -DestinationPath $extract -Force
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Copy-Item (Join-Path $extract 'yt-player-main\*') $Dir -Recurse -Force
Remove-Item $zip, $extract -Recurse -Force

Write-Host 'Installing dependencies...'
& $python -m pip install -q --disable-pip-version-check -r (Join-Path $Dir 'requirements.txt')

Write-Host 'Creating shortcuts...'
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "$Name.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "$Name.lnk")
)
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = '"' + (Join-Path $Dir 'launcher.pyw') + '"'
    $lnk.WorkingDirectory = $Dir
    $lnk.IconLocation = (Join-Path $Dir 'static\icon.ico')
    $lnk.Description = 'Ad-free YouTube song player'
    $lnk.Save()
}

Write-Host "Done! Opening $Name. Next time, just double-click its icon on your desktop." -ForegroundColor Green
Start-Process $pythonw -ArgumentList ('"' + (Join-Path $Dir 'launcher.pyw') + '"') -WorkingDirectory $Dir
