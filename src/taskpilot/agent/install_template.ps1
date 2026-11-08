# Deskhand helper installer. The server filled in its own address and your one-time code.
# Installs per-user (no admin): hidden background agent, starts at sign-in, updates itself from your Deskhand server.
$ErrorActionPreference = "Stop"
$Server = "__SERVER__"
$Code = "__CODE__"
$Dir = Join-Path $env:LOCALAPPDATA "Deskhand"
$Apps = Join-Path $Dir "apps"
New-Item -ItemType Directory -Force $Apps | Out-Null
Write-Host "Setting up Deskhand on this computer..." -ForegroundColor Cyan

# 1. Python 3.11+ (installed per-user with winget if missing)
function Find-Python {
  foreach ($c in @(@("py", "-3.12"), @("py", "-3.11"), @("py", "-3.13"), @("python"))) {
    try {
      $extra = if ($c.Count -gt 1) { @($c[1..($c.Count - 1)]) } else { @() }
      $out = & $c[0] @extra -c "import sys;print(sys.executable if sys.version_info>=(3,11) else '')" 2>$null
      if ($out -and (Test-Path $out)) { return $out }
    } catch {}
  }
  return $null
}
$py = Find-Python
if (-not $py) {
  Write-Host "Installing Python 3.11 (one time)..."
  winget install -e --id Python.Python.3.11 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
  $py = Find-Python
  if (-not $py) { throw "Python 3.11+ could not be installed automatically. Install it from python.org and run this again." }
}

# 2. Join: swap the one-time code for this laptop's own credentials
$name = $env:COMPUTERNAME
$join = Invoke-RestMethod -Method Post -Uri "$Server/agent/join" -ContentType "application/json" `
  -Body (@{ code = $Code; name = $name } | ConvertTo-Json)
$tokPath = Join-Path $Dir "agent.token"
Set-Content -Path $tokPath -Value $join.token -NoNewline -Encoding ascii
icacls $tokPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

# 3. Private environment + the current agent code from the server
$venv = Join-Path $Dir "venv"
if (-not (Test-Path (Join-Path $venv "Scripts\pythonw.exe"))) { & $py -m venv $venv }
$hdr = @{ "X-Device-Id" = $join.device_id; "X-Device-Token" = $join.token }
$meta = Invoke-RestMethod -Uri "$Server/agent/version" -Headers $hdr
$zip = Join-Path $env:TEMP "deskhand-agent.zip"
Invoke-WebRequest -Uri "$Server/agent/bundle.zip" -Headers $hdr -OutFile $zip
if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $meta.sha256) { throw "download did not match its checksum" }
$vdir = Join-Path $Apps $meta.version
Remove-Item -Recurse -Force $vdir -ErrorAction SilentlyContinue
Expand-Archive -Path $zip -DestinationPath $vdir -Force
Remove-Item $zip
Set-Content -Path (Join-Path $Apps "current.txt") -Value $meta.version -NoNewline
Copy-Item (Join-Path $vdir "taskpilot\agent\launcher.py") (Join-Path $Dir "launcher.py") -Force
Write-Host "Installing components (first time takes a minute)..."
& (Join-Path $venv "Scripts\python.exe") -m pip install --disable-pip-version-check -q -r (Join-Path $vdir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "could not install components" }
(Get-FileHash (Join-Path $vdir "requirements.txt") -Algorithm SHA256).Hash.ToLower() | Set-Content (Join-Path $Dir "requirements.sha") -NoNewline

# 4. Settings (kept if you re-run this later). Starts with a small, safe allowlist; edit it to allow more.
$cfgPath = Join-Path $Dir "agent.json"
$docs = Join-Path $env:USERPROFILE "Documents\Deskhand"
New-Item -ItemType Directory -Force $docs | Out-Null
if (-not (Test-Path $cfgPath)) {
  [ordered]@{
    server_url = $join.ws_url; device_id = $join.device_id; heartbeat_s = 10; update_check_s = 900
    policy = [ordered]@{
      approved_apps = @{ notepad = "C:\Windows\System32\notepad.exe"; calc = "C:\Windows\System32\calc.exe" }
      allowed_dirs = @($docs); ui_allowed_windows = @("Calculator", "Notepad")
      restrict_input_to_approved = $true; cloud_safe = $true
    }
  } | ConvertTo-Json -Depth 6 | Set-Content -Path $cfgPath -Encoding utf8
} else {
  $c = Get-Content $cfgPath -Raw | ConvertFrom-Json
  $c.server_url = $join.ws_url; $c.device_id = $join.device_id
  $c | ConvertTo-Json -Depth 8 | Set-Content -Path $cfgPath -Encoding utf8
}

# 5. Start at sign-in, hidden, restarts if it dies; below-normal priority is set by the agent itself
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute (Join-Path $venv "Scripts\pythonw.exe") `
  -Argument "`"$(Join-Path $Dir 'launcher.py')`" `"$Dir`"" -WorkingDirectory $Dir
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Settings $settings `
  -Principal (New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited) `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $user)
Register-ScheduledTask -TaskName "DeskhandAgent" -InputObject $task -Force | Out-Null

# 6. Start-menu shortcuts (Ctrl+Alt+P starts the agent, Ctrl+Alt+O opens the app)
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Deskhand"
New-Item -ItemType Directory -Force $menu | Out-Null
$sh = New-Object -ComObject WScript.Shell
function New-Link($n, $target, $a, $hotkey) {
  $l = $sh.CreateShortcut((Join-Path $menu "$n.lnk")); $l.TargetPath = $target; $l.Arguments = $a; $l.WindowStyle = 7
  if ($hotkey) { $l.Hotkey = $hotkey }; $l.Save()
}
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
New-Link "Deskhand - Start" $ps "-NoProfile -WindowStyle Hidden -Command Start-ScheduledTask -TaskName DeskhandAgent" "Ctrl+Alt+P"
New-Link "Deskhand - Stop" $ps "-NoProfile -WindowStyle Hidden -Command Stop-ScheduledTask -TaskName DeskhandAgent" ""
New-Link "Deskhand - Open app" "$Server/app/" "" "Ctrl+Alt+O"

Start-ScheduledTask -TaskName "DeskhandAgent"
Write-Host ""
Write-Host "Done. This computer is '$($join.device_id)' and will appear in your Deskhand app." -ForegroundColor Green
Write-Host "It starts at every sign-in and updates itself. Settings: $cfgPath"
