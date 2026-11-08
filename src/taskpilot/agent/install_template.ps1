# Deskhand installer. The server filled in its own address and your one-time code.
# Per-user (no admin, no Python needed): a private copy of Python named Deskhand.exe, run as the Deskhand APP (its own
# window, notification-area icon and logo; listed under Apps in Task Manager) that starts at sign-in, keeps the helper
# running and updates itself from your Deskhand server. Plus Start-menu and desktop shortcuts.
# (DESKHAND_HOME / DESKHAND_NO_SERVICE exist for testing only: install somewhere else and skip tasks/shortcuts.)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"            # Windows PowerShell 5.1 is extremely slow with the download bar on
$Server = "__SERVER__"
$Code = "__CODE__"
$Dir = if ($env:DESKHAND_HOME) { $env:DESKHAND_HOME } else { Join-Path $env:LOCALAPPDATA "Deskhand" }
$NoService = [bool]$env:DESKHAND_NO_SERVICE
$Apps = Join-Path $Dir "apps"
$Runtime = Join-Path $Dir "runtime"
$Exe = Join-Path $Runtime "Deskhand.exe"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)      # Windows PowerShell's "utf8" adds a BOM that breaks JSON readers
New-Item -ItemType Directory -Force $Apps | Out-Null
Write-Host "Setting up Deskhand on this computer..." -ForegroundColor Cyan

if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") { throw "Deskhand supports 64-bit Intel/AMD Windows for now (this one is $($env:PROCESSOR_ARCHITECTURE))." }

# 0. A reinstall must not fight the running copy: stop only Deskhand's own task and processes
if (-not $NoService) { foreach ($t in "Deskhand", "DeskhandAgent", "DeskhandTray") { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue } }
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

# 1. Join: swap the one-time code for this computer's own credentials
$join = Invoke-RestMethod -Method Post -Uri "$Server/agent/join" -ContentType "application/json" `
  -Body (@{ code = $Code; name = $env:COMPUTERNAME } | ConvertTo-Json)
$tokPath = Join-Path $Dir "agent.token"
[System.IO.File]::WriteAllText($tokPath, $join.token, $utf8NoBom)
icacls $tokPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

# 2. Deskhand's own Python (the official embeddable build, checksum pinned): no system Python is needed or touched
if (-not (Test-Path $Exe)) {
  Write-Host "Downloading Python runtime (one time, about 11 MB)..."
  $zip = Join-Path $env:TEMP "deskhand-python.zip"
  Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" -OutFile $zip
  if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b") {
    Remove-Item $zip -Force; throw "the Python download did not match its expected checksum"
  }
  Remove-Item $Runtime -Recurse -Force -ErrorAction SilentlyContinue
  Expand-Archive -Path $zip -DestinationPath $Runtime -Force
  Remove-Item $zip -Force
  [System.IO.File]::WriteAllText((Join-Path $Runtime "python311._pth"), "python311.zip`r`n.`r`nLib\site-packages`r`nimport site`r`n", $utf8NoBom)
  Copy-Item (Join-Path $Runtime "pythonw.exe") $Exe -Force                 # the windowless copy that will carry the Deskhand name
  $pip = Join-Path $env:TEMP "get-pip.py"
  Invoke-WebRequest -UseBasicParsing -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $pip
  & (Join-Path $Runtime "python.exe") $pip --no-warn-script-location -q
  if ($LASTEXITCODE -ne 0) { throw "could not set up the package installer" }
  Remove-Item $pip -Force
}
$py = Join-Path $Runtime "python.exe"

# 3. The current helper code from the server, checksum-verified
$hdr = @{ "X-Device-Id" = $join.device_id; "X-Device-Token" = $join.token }
$meta = Invoke-RestMethod -Uri "$Server/agent/version" -Headers $hdr
$zip = Join-Path $env:TEMP "deskhand-agent.zip"
Invoke-WebRequest -UseBasicParsing -Uri "$Server/agent/bundle.zip" -Headers $hdr -OutFile $zip
if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $meta.sha256) { throw "download did not match its checksum" }
$vdir = Join-Path $Apps $meta.version
Remove-Item -Recurse -Force $vdir -ErrorAction SilentlyContinue
Expand-Archive -Path $zip -DestinationPath $vdir -Force
Remove-Item $zip
[System.IO.File]::WriteAllText((Join-Path $Apps "current.txt"), $meta.version, $utf8NoBom)
Copy-Item (Join-Path $vdir "taskpilot\agent\launcher.py") (Join-Path $Dir "launcher.py") -Force
Copy-Item (Join-Path $vdir "taskpilot\agent\boot.py") (Join-Path $Dir "boot.py") -Force

Write-Host "Installing components (first time takes a minute)..."
& $py -m pip install --disable-pip-version-check --no-warn-script-location -q -r (Join-Path $vdir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "could not install components" }
(Get-FileHash (Join-Path $vdir "requirements.txt") -Algorithm SHA256).Hash.ToLower() | Set-Content (Join-Path $Dir "requirements.sha") -NoNewline

# 4. Put the Deskhand name and logo on the program, so Task Manager lists "Deskhand", not "Python"
& $py (Join-Path $Dir "boot.py") $vdir "taskpilot.agent.brand" $Exe
if ($LASTEXITCODE -ne 0) { Write-Host "(could not apply the Deskhand name to the program; it works the same)" -ForegroundColor Yellow }

# 5. Settings (kept if you re-run this later). Starts with a small, safe allowlist; edit it to allow more.
$cfgPath = Join-Path $Dir "agent.json"
$docs = Join-Path $env:USERPROFILE "Documents\Deskhand"
New-Item -ItemType Directory -Force $docs | Out-Null
if (-not (Test-Path $cfgPath)) {
  [ordered]@{
    server_url = $join.ws_url; device_id = $join.device_id; heartbeat_s = 10; update_check_s = 900
    policy = [ordered]@{
      approved_apps = @{ notepad = "C:\Windows\System32\notepad.exe"; calc = "C:\Windows\System32\calc.exe" }
      allowed_dirs = @($docs); ui_allowed_windows = @("Calculator", "Notepad")
      restrict_input_to_approved = $true; cloud_safe = $true; require_site_approval = $true
    }
  } | ConvertTo-Json -Depth 6 | ForEach-Object { [System.IO.File]::WriteAllText($cfgPath, $_, $utf8NoBom) }
} else {
  $c = [System.IO.File]::ReadAllText($cfgPath).TrimStart([char]0xFEFF) | ConvertFrom-Json
  $c.server_url = $join.ws_url; $c.device_id = $join.device_id
  if ($c.policy -and -not $c.policy.PSObject.Properties['require_site_approval']) { $c.policy | Add-Member -NotePropertyName require_site_approval -NotePropertyValue $true }
  [System.IO.File]::WriteAllText($cfgPath, ($c | ConvertTo-Json -Depth 8), $utf8NoBom)
}

# 6. Older installs used a Python virtual environment: it is no longer needed
Remove-Item (Join-Path $Dir "venv") -Recurse -Force -ErrorAction SilentlyContinue

if ($NoService) {
  Write-Host "Installed for testing in $Dir (no sign-in task, no shortcuts)." -ForegroundColor Green
  return
}

# 7. One app: starts at sign-in in the notification area (no window until you open it), restarts if it dies
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$boot = "`"$(Join-Path $Dir 'boot.py')`" --root `"$Dir`" taskpilot.agent.app"
$action = New-ScheduledTaskAction -Execute $Exe -Argument "$boot --minimized" -WorkingDirectory $Dir
Register-ScheduledTask -TaskName "Deskhand" -Force -InputObject (New-ScheduledTask -Action $action -Settings $settings -Principal $principal -Trigger $trigger) | Out-Null
# older installs had two separate tasks: the app replaces both
foreach ($old in "DeskhandAgent", "DeskhandTray") { Unregister-ScheduledTask -TaskName $old -Confirm:$false -ErrorAction SilentlyContinue }

# 8. Start-menu shortcuts: "Deskhand" opens the app window (Ctrl+Alt+P), and one opens the web app (Ctrl+Alt+O)
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Deskhand"
Remove-Item $menu -Recurse -Force -ErrorAction SilentlyContinue                          # drop the old Start/Stop shortcuts
New-Item -ItemType Directory -Force $menu | Out-Null
$sh = New-Object -ComObject WScript.Shell
function New-Link($n, $target, $a, $hotkey, $icon) {
  $l = $sh.CreateShortcut((Join-Path $menu "$n.lnk")); $l.TargetPath = $target; $l.Arguments = $a; $l.WorkingDirectory = $Dir
  if ($icon) { $l.IconLocation = "$icon,0" }; if ($hotkey) { $l.Hotkey = $hotkey }; $l.Save()
}
New-Link "Deskhand" $Exe $boot "Ctrl+Alt+P" $Exe
New-Link "Deskhand - Open in browser" "$Server/app/" "" "Ctrl+Alt+O" $Exe
Copy-Item (Join-Path $menu "Deskhand.lnk") (Join-Path ([Environment]::GetFolderPath("Desktop")) "Deskhand.lnk") -Force   # also on the desktop

Start-ScheduledTask -TaskName "Deskhand"
Start-Sleep -Seconds 3
Start-Process -FilePath $Exe -ArgumentList $boot -WorkingDirectory $Dir                    # show the window once, so you see it
Write-Host ""
Write-Host "Done. This computer is '$($join.device_id)' and will appear in your Deskhand app." -ForegroundColor Green
Write-Host "Deskhand is now an app: find it in Task Manager under Apps, and in the Start menu. Closing its window keeps it running near the clock."
