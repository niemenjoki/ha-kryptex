<#
.SYNOPSIS
Installs the Kryptex bridge for the current Windows user.

.DESCRIPTION
The easy way is to double-click install.cmd in this folder: it runs this script as administrator.

What this script does, step by step (each step is marked with a comment below):
1. Finds Python 3.10 or newer on this PC.
2. Chooses a free port: 5500, or the first free one of 5501-5510 if another program uses 5500.
3. Copies kryptex_bridge.py to %LOCALAPPDATA%\kryptex-bridge.
4. Creates %APPDATA%\kryptex-bridge\config.json with a new random API key (an existing file and its key are kept).
5. Registers a scheduled task "Kryptex bridge" that starts the bridge hidden at logon and restarts it if it stops,
   and two tasks "Kryptex bridge start" and "Kryptex bridge stop" that start and close Kryptex as administrator.
6. Adds an inbound firewall rule "Kryptex bridge" for the bridge's port, open only to your local network.
7. Checks that the bridge answers and prints what to enter in Home Assistant.

Nothing is downloaded and nothing is sent anywhere. The bridge runs in your user session because Kryptex does.
uninstall.ps1 removes everything this script adds.

.PARAMETER Port
TCP port of the bridge. Without it the installer keeps the saved port (first install: 5500) and picks a free
one of 5500-5510 if another program uses it. With it, that port is used and saved; if it is in use, the
installer stops before changing anything.

.PARAMETER FirewallRemoteAddress
Who may connect: LocalSubnet (default, your local network) or the IP address of Home Assistant.

.PARAMETER NoFirewall
Adds no firewall rule; add one yourself if Home Assistant cannot connect.

.PARAMETER Python
Full path to pythonw.exe, if it is not on PATH.

.EXAMPLE
.\install.ps1
.EXAMPLE
.\install.ps1 -FirewallRemoteAddress 192.168.1.10 -Port 5501
#>
param(
    [int]$Port = 5500,
    [string]$FirewallRemoteAddress = "LocalSubnet",
    [switch]$NoFirewall,
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$TaskName = "Kryptex bridge"
$AppDir = Join-Path $env:LOCALAPPDATA "kryptex-bridge"
$ConfigDir = Join-Path $env:APPDATA "kryptex-bridge"
$ConfigFile = Join-Path $ConfigDir "config.json"

# Step 0: checks before changing anything.
# The firewall rule needs administrator rights. Without them, stop here instead of installing halfway.
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Administrator rights are needed (for the tasks that start and close Kryptex, and the firewall rule). " +
        "Double-click install.cmd, or run this in an administrator PowerShell."
}
# If Windows asked for another account's password, this window runs as that account, and the bridge would be
# installed for it instead of for you. Kryptex runs in your own session, so stop.
$sessionUser = (Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue).UserName
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($sessionUser -and $sessionUser -ne $currentUser) {
    throw "This window runs as $currentUser, but $sessionUser is signed in. Install as $sessionUser " +
        "(see 'Install by hand' in the README if that account is not an administrator)."
}

# Step 1: find Python. pythonw.exe runs a script without a console window.
if (-not $Python) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "pythonw.exe was not found. Install Python 3.10 or newer from python.org and tick " +
            "'Add python.exe to PATH', then run the installer again."
    }
    $Python = $cmd.Source
}
$version = & ($Python -replace "pythonw\.exe$", "python.exe") -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]"3.10") { throw "Python $version found; 3.10 or newer is needed." }
Write-Host "Python $version found: $Python"

# Step 2: choose a free port. Stop an earlier install of the bridge first, so its own port counts as free.
$hadTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($hadTask) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1 }
function Get-PortUser([int]$number) {
    # Returns the program that listens on the port, or nothing when the port is free.
    $listener = Get-NetTCPConnection -LocalPort $number -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if ($process) { return "$($process.ProcessName) (process $($listener.OwningProcess))" }
    return "process $($listener.OwningProcess)"
}
$existing = if (Test-Path $ConfigFile) { Get-Content $ConfigFile -Raw | ConvertFrom-Json } else { $null }
$savedPort = if ($existing -and $existing.port) { [int]$existing.port } else { 0 }
$portNote = ""
if ($PSBoundParameters.ContainsKey("Port")) {
    # A port given with -Port is used as is, or the installer stops before changing anything.
    $user = Get-PortUser $Port
    if ($user) {
        if ($hadTask) { Start-ScheduledTask -TaskName $TaskName }
        throw "Port $Port is in use by $user. Choose another -Port, or leave -Port out to let the installer pick a free port."
    }
    $chosenPort = $Port
} else {
    # Otherwise keep the saved port (or 5500). If another program has it, take the first free port of 5500-5510.
    $wanted = if ($savedPort) { $savedPort } else { 5500 }
    $chosenPort = @($wanted) + (5500..5510 | Where-Object { $_ -ne $wanted }) |
        Where-Object { -not (Get-PortUser $_) } | Select-Object -First 1
    if (-not $chosenPort) {
        if ($hadTask) { Start-ScheduledTask -TaskName $TaskName }
        throw "Ports 5500-5510 are all in use. Run the installer again with -Port and a free port number."
    }
    if ($chosenPort -ne $wanted) {
        $portNote = "Port $wanted is in use by $(Get-PortUser $wanted), so the bridge uses port $chosenPort."
    }
}
if ($savedPort -and $savedPort -ne $chosenPort) {
    $portNote = ($portNote + " The port changed from $savedPort to ${chosenPort}: Home Assistant must use the new port.").Trim()
}
Write-Host "Port: $chosenPort"

# Step 3: copy the bridge (one Python file) to your user's program folder.
New-Item -ItemType Directory -Force $AppDir, $ConfigDir | Out-Null
Copy-Item (Join-Path $PSScriptRoot "kryptex_bridge.py") $AppDir -Force
Write-Host "Copied kryptex_bridge.py to $AppDir"

# Step 4: create the settings with a random API key from Windows' cryptographic random generator.
# An existing config.json is kept, so running the installer again (to update) keeps the same key and your own
# settings. Only its port is changed, if step 2 chose another one.
if (-not $existing) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $key = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    @{ port = $chosenPort; api_key = $key } | ConvertTo-Json | Set-Content -Encoding UTF8 $ConfigFile
    Write-Host "Created $ConfigFile"
} elseif ($savedPort -ne $chosenPort) {
    $existing | Add-Member -NotePropertyName port -NotePropertyValue $chosenPort -Force
    $existing | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $ConfigFile
    Write-Host "Kept the existing $ConfigFile (API key unchanged), port set to $chosenPort."
} else {
    Write-Host "Kept the existing $ConfigFile (API key unchanged)."
}
$config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
$configPort = $chosenPort

# Step 5: start the bridge at logon, hidden, and restart it every minute if it stops.
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$AppDir\kryptex_bridge.py`"" -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
Write-Host "Scheduled task '$TaskName' registered and started."

# Step 5b: two tasks that start and close the Kryptex app with the highest privileges. Kryptex runs as administrator,
# so the bridge (which runs without them) could not close it, nor start it. The tasks have no trigger: they run only
# when the bridge asks (schtasks /run), and each does one thing only.
$kryptexExe = (Get-Process -Name Kryptex -ErrorAction SilentlyContinue | Select-Object -First 1).Path
if (-not $kryptexExe) { $kryptexExe = Join-Path $env:ProgramFiles "Kryptex\Kryptex.exe" }
if (-not (Test-Path $kryptexExe)) { Write-Warning "Kryptex.exe was not found at $kryptexExe. Starting Kryptex from Home Assistant will not work." }
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
$quick = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
# "cmd /c start" returns at once, so the task does not stay running (and is not ended) while Kryptex runs.
$startAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c start `"`" `"$kryptexExe`""
$stopAction = New-ScheduledTaskAction -Execute "taskkill.exe" -Argument "/IM Kryptex.exe /F"
Register-ScheduledTask -TaskName "$TaskName start" -Action $startAction -Principal $taskPrincipal -Settings $quick -Force | Out-Null
Register-ScheduledTask -TaskName "$TaskName stop" -Action $stopAction -Principal $taskPrincipal -Settings $quick -Force | Out-Null
Write-Host "Scheduled tasks '$TaskName start' and '$TaskName stop' registered (Kryptex: $kryptexExe)."

# Step 6: let Home Assistant in. The rule allows only the bridge's port, only from the given addresses, and only
# on private and domain networks (not on public Wi-Fi).
if (-not $NoFirewall) {
    Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $TaskName -Direction Inbound -Protocol TCP -LocalPort $configPort `
        -RemoteAddress $FirewallRemoteAddress -Action Allow -Profile Private, Domain | Out-Null
    Write-Host "Firewall: TCP $configPort allowed from $FirewallRemoteAddress."
    $public = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object NetworkCategory -eq "Public"
    if ($public) {
        Write-Warning ("Your network '$($public[0].Name)' is set to Public, so the firewall rule does not apply " +
            "and Home Assistant cannot connect. Change it to Private: Settings > Network & internet > your " +
            "connection > Network profile type > Private network.")
    }
}

# Step 7: check that the bridge answers (it can take a few seconds to start).
$answered = $false
for ($i = 0; $i -lt 10 -and -not $answered; $i++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$configPort/health" -Headers @{ Authorization = "Bearer $($config.api_key)" }
        Write-Host "Bridge answers: version $($health.version)."
        $answered = $true
    } catch { }
}
if (-not $answered) { Write-Warning "The bridge did not answer yet. See $ConfigDir\bridge.log." }

$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.IPAddress -notlike "169.254.*" } |
    ForEach-Object IPAddress
Write-Host ""
Write-Host "Enter these in Home Assistant (Add integration > Kryptex):"
Write-Host "  Address: $($addresses -join ' or ')"
Write-Host "  Port:    $configPort"
Write-Host "  API key: $($config.api_key)"
if ($portNote) { Write-Host ""; Write-Warning $portNote }
