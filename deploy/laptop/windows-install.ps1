<#
  Run ON THE LAPTOP when it is a Windows machine (PowerShell as your normal user, from the repo folder).
  Sets up two background jobs that start when you sign in and restart themselves if they stop:
    Deskhand Server  - the Deskhand server, listening on this laptop only (127.0.0.1:8765)
    Deskhand Tunnel  - an outbound SSH tunnel so the Oracle server can reach it (no port is opened at home)

    .\deploy\laptop\windows-install.ps1 -OracleIp 1.2.3.4
    .\deploy\laptop\windows-install.ps1 -OracleIp 1.2.3.4 -Remove      # take the jobs away again

  Needs: Python 3.11 with the repo installed (`pip install -e ".[server]"` in .venv), and the Windows OpenSSH client
  (Settings > Apps > Optional features; it is there on a normal Windows 10/11).
#>
param(
  [Parameter(Mandatory = $true)][string]$OracleIp,
  [string]$OracleUser = "ubuntu",
  [string]$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path,
  [string]$Data = "$env:ProgramData\Deskhand",
  [switch]$Remove
)
$ErrorActionPreference = "Stop"
if ($Remove) {
  foreach ($n in "Deskhand Server", "Deskhand Tunnel") { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
  Write-Host "Removed both jobs. Your data in $Data is untouched."; return
}

New-Item -ItemType Directory -Force $Data | Out-Null
$envFile = Join-Path $Data "deskhand.env"
if (-not (Test-Path $envFile)) {
  Copy-Item "$Repo\deploy\laptop\laptop.env.example" $envFile
  Write-Host "Created $envFile : open it, fill in the keys, then run this script again." -ForegroundColor Yellow
  notepad $envFile; return
}

# 1. a key that can only open the tunnel
$key = Join-Path $env:USERPROFILE ".ssh\deskhand_tunnel"
if (-not (Test-Path $key)) {
  New-Item -ItemType Directory -Force (Split-Path $key) | Out-Null
  ssh-keygen -t ed25519 -f $key -N '""' -C "deskhand-laptop-tunnel" | Out-Null
  Write-Host "`nA new tunnel key was made. On the ORACLE server run this ONE line, then run this script again:`n" -ForegroundColor Yellow
  Write-Host ("  bash deploy/laptop/oracle-setup.sh authorize ""{0}""`n" -f (Get-Content "$key.pub" -Raw).Trim())
  return
}

# 2. the two small runner scripts
$server = Join-Path $Data "run-server.ps1"
@"
Get-Content '$envFile' | ForEach-Object { if (`$_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$' -and -not `$_.TrimStart().StartsWith('#')) { [Environment]::SetEnvironmentVariable(`$Matches[1], (`$Matches[2] -replace '\s+#.*$','').Trim(), 'Process') } }
Set-Location '$Repo'
while (`$true) {
  & '$Repo\.venv\Scripts\taskpilot-server.exe' --host 127.0.0.1 --port 8765 --policy '$Repo\server_policy.json' --db '$Data\deskhand.db' --provider openai
  Start-Sleep 3
}
"@ | Set-Content -Encoding UTF8 $server

$tunnel = Join-Path $Data "run-tunnel.ps1"
@"
while (`$true) {
  & ssh -N -T -i '$key' -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -o BatchMode=yes -R 127.0.0.1:18765:127.0.0.1:8765 $OracleUser@$OracleIp
  Start-Sleep 5
}
"@ | Set-Content -Encoding UTF8 $tunnel

# 3. start them at sign-in, hidden, and let them run on battery too
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
foreach ($job in @(@("Deskhand Server", $server), @("Deskhand Tunnel", $tunnel))) {
  $act = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$($job[1])`""
  Register-ScheduledTask -TaskName $job[0] -Action $act -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Settings $set -Force | Out-Null
  Start-ScheduledTask -TaskName $job[0]
}
Write-Host "`nBoth jobs are running. Check the server:  curl http://127.0.0.1:8765/v1/version"
Write-Host "Then on the Oracle server:  bash deploy/laptop/oracle-setup.sh check"
Write-Host "To keep the laptop reachable: Settings > System > Power: never sleep when plugged in; keep it on a charger."
