# Keep Abogi's Discord bot running on this Windows PC (the bot itself runs in WSL).
#   powershell -ExecutionPolicy Bypass -File scripts\abogi-discord.ps1 -Install   # start now and at every sign-in
#   powershell -ExecutionPolicy Bypass -File scripts\abogi-discord.ps1 -Remove    # stop and forget
#   powershell -ExecutionPolicy Bypass -File scripts\abogi-discord.ps1            # run here, in this window
# It restarts the bot 30 seconds after it stops, and logs to %LOCALAPPDATA%\abogi\discord.log.
param([switch]$Install, [switch]$Remove)

$task = 'Abogi Discord'

if ($Remove) {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $task -Confirm:$false
    "Removed the '$task' task."
    return
}

if ($Install) {
    $run = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`""
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $run
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $task
    "Installed and started the '$task' task. It starts again at every sign-in."
    return
}

# The repo lives in WSL: \\wsl.localhost\<distro>\<path> -> the distro and its Linux path.
if ($PSScriptRoot -notmatch '^\\\\wsl(\.localhost|\$)\\([^\\]+)\\(.+)$') {
    throw "Run this from the repo's WSL path (\\wsl.localhost\...), not $PSScriptRoot."
}
$distro = $Matches[2]
$script = '/' + ($Matches[3] -replace '\\', '/') + '/abogi-discord.sh'

$dir = Join-Path $env:LOCALAPPDATA 'abogi'
New-Item -ItemType Directory -Force $dir | Out-Null
$log = Join-Path $dir 'discord.log'

while ($true) {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) {
        Set-Content -Encoding utf8 $log (Get-Content $log -Tail 500)  # keep the log small
    }
    Add-Content -Encoding utf8 $log "$(Get-Date -Format s) Starting the Discord bot."
    & wsl.exe -d $distro -e bash $script 2>&1 | ForEach-Object { Add-Content -Encoding utf8 $log "$_" }
    Add-Content -Encoding utf8 $log "$(Get-Date -Format s) The bot stopped; starting again in 30 seconds."
    Start-Sleep -Seconds 30
}
