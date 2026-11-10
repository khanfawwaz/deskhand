"""Makes Deskhand an ordinary Windows app: listed in Settings > Apps > Installed apps, with an Uninstall button.

The app calls register() every time it starts (it is cheap and safe to repeat), so an install made before this existed
gets the entry too, through the normal automatic update. Nothing here needs administrator rights (per-user entry).

Uninstall runs `uninstall.ps1`, written next to the app. It asks first, then stops Deskhand, removes its sign-in task,
shortcuts, the Installed-apps entry and the whole Deskhand folder (including the app's own separate browser profile).
It never touches your real Chrome, your Documents\\Deskhand folder or the Deskhand server.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

KEY_NAME = "Deskhand"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"

SCRIPT = r'''# Deskhand uninstaller (written by the app; safe to run by hand).
param([switch] $Quiet, [switch] $Test, [string] $KeyName = "Deskhand")
$ErrorActionPreference = "SilentlyContinue"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$dirSlash = $dir.TrimEnd('\') + '\'
Add-Type -AssemblyName System.Windows.Forms
function Ask($text) { if ($Quiet) { return $true }; return ([System.Windows.Forms.MessageBox]::Show($text, "Uninstall Deskhand", "YesNo", "Question") -eq "Yes") }
function Tell($text) { if (-not $Quiet) { [void][System.Windows.Forms.MessageBox]::Show($text, "Deskhand", "OK", "Information") } }

if (-not (Ask "Remove Deskhand from this computer?`n`nIt stops, and its settings, sign-in and the app's own browser profile are deleted.`nYour files, your normal Chrome and your Deskhand account are not touched.")) { return }

if (-not $Test) {
  foreach ($t in "Deskhand", "DeskhandAgent", "DeskhandTray") { Stop-ScheduledTask -TaskName $t; Unregister-ScheduledTask -TaskName $t -Confirm:$false }
}
# only Deskhand's own processes: the ones that run from its folder or were started with its folder as an argument
Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and (
      ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($dirSlash, [StringComparison]::OrdinalIgnoreCase)) -or
      ($_.CommandLine -and $_.CommandLine.IndexOf($dirSlash, [StringComparison]::OrdinalIgnoreCase) -ge 0))
  } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
if (-not $Test) {
  Remove-Item -Recurse -Force (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Deskhand")
  Remove-Item -Force (Join-Path ([Environment]::GetFolderPath("Desktop")) "Deskhand.lnk")
}
Remove-Item -Recurse -Force ("HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\" + $KeyName)

# the folder holds this script, so it is removed a moment after this window has gone
Start-Process -WindowStyle Hidden -WorkingDirectory $env:TEMP -FilePath "cmd.exe" -ArgumentList ('/c ping -n 3 127.0.0.1 >nul & rmdir /s /q "' + $dir + '"')
Tell "Deskhand was removed.`n`nThis computer may still be listed in your Deskhand app: open Settings > Your computers and remove it there."
'''


def uninstall_command(home: Path) -> str:
    return f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{home / "uninstall.ps1"}"'


def register(home: Path, key_name: str = KEY_NAME) -> bool:
    """Write uninstall.ps1 and the Installed-apps entry. Returns True when the entry is in place."""
    if os.name != "nt" or (os.environ.get("DESKHAND_DEV") and key_name == KEY_NAME):
        return False                                          # a development copy never shows up as an installed app
    import winreg

    home = Path(home)
    script = home / "uninstall.ps1"
    # Windows PowerShell 5.1 reads a script without a byte-order mark as the local code page: write one so every name survives
    data = b"\xef\xbb\xbf" + SCRIPT.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    if not script.exists() or script.read_bytes() != data:
        script.write_bytes(data)
    try:
        version = (home / "apps" / "current.txt").read_text(encoding="utf-8").strip() or "1"
    except OSError:
        version = "1"
    exe = home / "runtime" / "Deskhand.exe"
    icon = str(exe) if exe.exists() else sys.executable
    cmd = uninstall_command(home)
    values = {
        "DisplayName": ("Deskhand", winreg.REG_SZ), "DisplayVersion": (version[:16], winreg.REG_SZ),
        "Publisher": ("Deskhand", winreg.REG_SZ), "DisplayIcon": (icon, winreg.REG_SZ),
        "InstallLocation": (str(home), winreg.REG_SZ), "UninstallString": (cmd, winreg.REG_SZ),
        "QuietUninstallString": (cmd + " -Quiet", winreg.REG_SZ),
        "NoModify": (1, winreg.REG_DWORD), "NoRepair": (1, winreg.REG_DWORD),
    }
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY + "\\" + key_name, 0, winreg.KEY_SET_VALUE) as k:
        for name, (val, kind) in values.items():
            winreg.SetValueEx(k, name, 0, kind, val)
    return True
