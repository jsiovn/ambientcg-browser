# Record site\assets\guide.mp4 and guide-poster.webp: build, serve dist\ while the add-on installs from it into an
# isolated profile, record in a second GUI Blender (tools\record_guide.py record), encode in a background one.
# Keep the mouse and keyboard still while it records: it clicks in its own Blender window.
# Usage: powershell -ExecutionPolicy Bypass -File tools\record_guide.ps1 [-Work <scratch dir>] [-Blender <exe>] [-Port <n>]
param(
    [string]$Work = (Join-Path $env:TEMP "ambientcg-browser-guide"),
    [string]$Blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    [int]$Port = 8767
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$script = Join-Path $PSScriptRoot "record_guide.py"
$profileDir = Join-Path $Work "profile"

function Test-Port { [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }

& powershell -ExecutionPolicy Bypass -File (Join-Path $root "build.ps1") -Blender $Blender
if ($LASTEXITCODE -ne 0) { throw "build failed" }

# A fresh profile: the add-on comes from the repository every time. The cache folder in $Work stays.
Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $profileDir | Out-Null
$saved = $env:BLENDER_USER_RESOURCES
$env:BLENDER_USER_RESOURCES = $profileDir
try {
    # The real python.exe: a pyenv shim would leave the server running when it is stopped.
    $python = (& python -c "import sys; print(sys.executable)").Trim()
    if (Test-Port) { throw "port $Port is in use: pass -Port" }
    $server = Start-Process -FilePath $python -ArgumentList "-m", "http.server", $Port, "--bind", "127.0.0.1",
        "--directory", (Join-Path $root "dist") -PassThru -WindowStyle Hidden
    try {
        Start-Sleep -Seconds 1
        & $Blender --online-mode --command extension repo-add --clear-all --name "Local" --url "http://127.0.0.1:$Port/index.json" local
        if ($LASTEXITCODE -ne 0) { throw "repo-add failed" }
        & $Blender --online-mode --command extension install --sync --enable ambientcg_browser
        if ($LASTEXITCODE -ne 0) { throw "install failed" }
    } finally {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        if (Test-Port) { throw "the server on port $Port is still running" }
    }

    # The splash is a popup that swallows every click: off before the GUI starts.
    & $Blender --background --python-expr "import bpy; bpy.context.preferences.view.show_splash = False; bpy.ops.wm.save_userpref()"
    if ($LASTEXITCODE -ne 0) { throw "could not turn the splash off" }

    $recorder = Start-Process -FilePath $Blender -PassThru -Wait -ArgumentList "--online-mode", "--window-geometry",
        "0", "0", "1280", "720", "--python", "`"$script`"", "--", "record", "`"$Work`""
    if ($recorder.ExitCode -ne 0) { throw "record failed ($($recorder.ExitCode)): see $(Join-Path $Work 'frames.json')" }

    & $Blender --background --factory-startup --python-exit-code 1 --python $script -- encode $Work (Join-Path $root "site\assets")
    if ($LASTEXITCODE -ne 0) { throw "encode failed" }
} finally {
    $env:BLENDER_USER_RESOURCES = $saved
}
