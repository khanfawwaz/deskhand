<#
.SYNOPSIS  Remove Deskhand from this computer: stops it, removes the sign-in tasks, shortcuts and token.
           Your settings and logs are kept unless you pass -Purge (which deletes the whole Deskhand folder).
#>
param([switch] $Purge)
$ErrorActionPreference = "SilentlyContinue"
$dir = Join-Path $env:LOCALAPPDATA "Deskhand"
foreach ($t in "Deskhand", "DeskhandAgent", "DeskhandTray") {
  Stop-ScheduledTask -TaskName $t
  Unregister-ScheduledTask -TaskName $t -Confirm:$false
}
# only Deskhand's own processes: ones that run from its folder, or whose command line points into it (an old install's
# real Python lives elsewhere and is started with Deskhand's folder as an argument)
Get-CimInstance Win32_Process | Where-Object {
    ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase)) -or
    ($_.CommandLine -and $_.CommandLine.IndexOf($dir, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and $_.ProcessId -ne $PID)
  } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item -Recurse -Force (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Deskhand")
Remove-Item -Force (Join-Path ([Environment]::GetFolderPath("Desktop")) "Deskhand.lnk")
Remove-Item -Force (Join-Path $dir "agent.token")
if ($Purge) { Remove-Item -Recurse -Force $dir }
Write-Host "Deskhand removed. (The computer is still listed in your app: use Settings > Your computers > Remove.)"
