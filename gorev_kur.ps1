# Hafta ici her gun 11:55'te (TR, kis saati; yaz saatinde 10:55 yap) liste_yenile.bat'i baslatan Windows gorevi kurar.
# Program NY kapanisinda (TR kis saati 00:00) ve ABD tatillerinde kendiliginden kapanir.
# Kaldirmak icin: Unregister-ScheduledTask -TaskName "Trade Liste Yenile" -Confirm:$false
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat = Join-Path $dir "liste_yenile.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "11:55"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 14) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Trade Liste Yenile" -Action $action -Trigger $trigger -Settings $settings `
    -Description "bugraaa_premarket ve bugraaa_trade TradingView listelerini yeniler (premarket 2 dk, piyasa 40 sn)" -Force |
    Select-Object TaskName, State
"Sonraki calisma: " + (Get-ScheduledTask -TaskName "Trade Liste Yenile" | Get-ScheduledTaskInfo).NextRunTime
