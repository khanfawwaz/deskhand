<#
.SYNOPSIS  Remove Deskhand from this computer: stops it, removes the sign-in task and shortcuts, deletes the token.
           Your config and logs are kept unless you pass -Purge.
#>
param([switch] $Purge)
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "DeskhandAgent"
Unregister-ScheduledTask -TaskName "DeskhandAgent" -Confirm:$false
Remove-Item -Recurse -Force (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Deskhand")
$dir = Join-Path $env:LOCALAPPDATA "Deskhand"
Remove-Item -Force (Join-Path $dir "agent.token")
if ($Purge) { Remove-Item -Recurse -Force $dir }
Write-Host "Deskhand removed. (The device is still enrolled on the server: revoke it there too.)"
