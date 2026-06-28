# Registers a Scheduled Task that sends the "PC is shutting down" Telegram
# notification (to users who have a job in the queue) when the PC shuts down or restarts.
#
# Run ONCE as Administrator:
#   Right-click this file -> "Run with PowerShell"
#   or:  powershell -ExecutionPolicy Bypass -File install_offline_task.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
$script = Join-Path $here "notify_offline.py"

# Trigger: System log event 1074 (shutdown/restart initiated)
$query = @"
<QueryList>
  <Query Id="0" Path="System">
    <Select Path="System">*[System[Provider[@Name='User32'] and (EventID=1074)]]</Select>
  </Query>
</QueryList>
"@

$trigger = New-CimInstance -CimClass (Get-CimClass `
  -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler) `
  -ClientOnly
$trigger.Subscription = $query
$trigger.Enabled = $true

$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`"" -WorkingDirectory $here
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "ComfyBot-OfflineNotify" -Trigger $trigger `
  -Action $action -Settings $settings -RunLevel Highest -Force `
  -Description "Notifies bot users when the PC shuts down."

Write-Host "Done! Task 'ComfyBot-OfflineNotify' registered." -ForegroundColor Green
