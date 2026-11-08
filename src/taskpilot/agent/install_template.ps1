# Deskhand installer. The server filled in its own address and your one-time code.
# Per-user (no admin, no Python needed): a private copy of Python named Deskhand.exe, a hidden background helper that
# starts at sign-in and updates itself from your Deskhand server, a notification-area icon, and Start-menu shortcuts.
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
if (-not $NoService) { Stop-ScheduledTask -TaskName "DeskhandAgent" -ErrorAction SilentlyContinue; Stop-ScheduledTask -TaskName "DeskhandTray" -ErrorAction SilentlyContinue }
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

# 7. Start at sign-in, hidden, restarts if it dies; plus the notification-area icon
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$agent = New-ScheduledTaskAction -Execute $Exe -Argument "`"$(Join-Path $Dir 'launcher.py')`" `"$Dir`"" -WorkingDirectory $Dir
Register-ScheduledTask -TaskName "DeskhandAgent" -Force -InputObject (New-ScheduledTask -Action $agent -Settings $settings -Principal $principal -Trigger $trigger) | Out-Null
$trayAction = New-ScheduledTaskAction -Execute $Exe -Argument "`"$(Join-Path $Dir 'launcher.py')`" `"$Dir`" tray" -WorkingDirectory $Dir
$traySettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "DeskhandTray" -Force -InputObject (New-ScheduledTask -Action $trayAction -Settings $traySettings -Principal $principal -Trigger $trigger) | Out-Null

# 8. Start-menu shortcuts (Ctrl+Alt+P starts Deskhand, Ctrl+Alt+O opens the app)
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Deskhand"
New-Item -ItemType Directory -Force $menu | Out-Null
$sh = New-Object -ComObject WScript.Shell
function New-Link($n, $target, $a, $hotkey) {
  $l = $sh.CreateShortcut((Join-Path $menu "$n.lnk")); $l.TargetPath = $target; $l.Arguments = $a; $l.WindowStyle = 7
  if ($hotkey) { $l.Hotkey = $hotkey }; $l.Save()
}
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
New-Link "Deskhand - Start" $ps "-NoProfile -WindowStyle Hidden -Command Start-ScheduledTask -TaskName DeskhandAgent; Start-ScheduledTask -TaskName DeskhandTray" "Ctrl+Alt+P"
New-Link "Deskhand - Stop" $ps "-NoProfile -WindowStyle Hidden -Command Stop-ScheduledTask -TaskName DeskhandAgent" ""
New-Link "Deskhand - Open app" "$Server/app/" "" "Ctrl+Alt+O"

Start-ScheduledTask -TaskName "DeskhandAgent"
Start-ScheduledTask -TaskName "DeskhandTray"
Write-Host ""
Write-Host "Done. This computer is '$($join.device_id)' and will appear in your Deskhand app." -ForegroundColor Green
Write-Host "Look for the Deskhand icon near the clock. It starts at every sign-in and updates itself. Settings: $cfgPath"
