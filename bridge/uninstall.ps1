<#
.SYNOPSIS
Removes the Kryptex bridge: the scheduled tasks, the program folder and the firewall rule.

.DESCRIPTION
Run in an administrator PowerShell as the user who installed the bridge (the firewall rule needs administrator
rights). Keeps %APPDATA%\kryptex-bridge (config.json with the API key, and the log) unless -RemoveConfig is given,
so a later install uses the same key.
#>
param([switch]$RemoveConfig)
$ErrorActionPreference = "Stop"
$TaskName = "Kryptex bridge"

# Step 1: stop the bridge and remove the task that starts it at logon.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
# Also the two tasks that start and close Kryptex.
Unregister-ScheduledTask -TaskName "$TaskName start" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "$TaskName stop" -Confirm:$false -ErrorAction SilentlyContinue

# Step 2: remove the firewall rule that let Home Assistant in.
Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue | Remove-NetFirewallRule

# Step 3: remove the program folder (kryptex_bridge.py).
Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA "kryptex-bridge") -ErrorAction SilentlyContinue

# Step 4 (only with -RemoveConfig): remove the settings, the API key and the log.
if ($RemoveConfig) { Remove-Item -Recurse -Force (Join-Path $env:APPDATA "kryptex-bridge") -ErrorAction SilentlyContinue }
Write-Host "Kryptex bridge removed."
