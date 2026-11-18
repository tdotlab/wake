# Register two scheduled tasks for the current user (no admin, no stored password):
#   \Wake\Claude Code journal  daily at 01:00, runs late if the PC was off or asleep
#   \Wake\Vault sync               every 30 minutes
# Both run hidden through wake.hidden and log to ~/.wake/logs/. Rerun to update; -Remove to delete.
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
$folder = '\Wake\'
$tasks = @{
    'Claude Code journal' = @{
        Args    = '-m wake.hidden claude-code-journal wake cc-journal run'
        Trigger = New-ScheduledTaskTrigger -Daily -At '01:00'
        Limit   = New-TimeSpan -Hours 3
    }
    'Vault sync' = @{
        Args    = '-m wake.hidden vault-sync wake vault-sync --quiet'
        Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30)
        Limit   = New-TimeSpan -Minutes 10
    }
}

if ($Remove) {
    foreach ($name in $tasks.Keys) {
        Unregister-ScheduledTask -TaskPath $folder -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    }
    'Removed.'
    return
}
if (-not (Test-Path $pythonw)) { throw "No venv at $pythonw; see README (uv venv + uv pip install -e .)" }

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
foreach ($name in $tasks.Keys) {
    $t = $tasks[$name]
    # The cmdlet stores the start time in UTC, which shifts the run an hour at each DST change.
    # Rewrite it as local wall-clock time with no offset.
    $t.Trigger.StartBoundary = ([datetime]$t.Trigger.StartBoundary).ToString('s')
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $t.Args -WorkingDirectory $repo
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit $t.Limit -RunOnlyIfNetworkAvailable
    Register-ScheduledTask -TaskPath $folder -TaskName $name -Action $action -Trigger $t.Trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    $info = Get-ScheduledTaskInfo -TaskPath $folder -TaskName $name
    "$folder$name  next run $($info.NextRunTime)"
}
