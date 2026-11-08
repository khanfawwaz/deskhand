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
# only processes that run from Deskhand's own folder
Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item -Recurse -Force (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Deskhand")
Remove-Item -Force (Join-Path ([Environment]::GetFolderPath("Desktop")) "Deskhand.lnk")
Remove-Item -Force (Join-Path $dir "agent.token")
if ($Purge) { Remove-Item -Recurse -Force $dir }
Write-Host "Deskhand removed. (The computer is still listed in your app: use Settings > Your computers > Remove.)"
