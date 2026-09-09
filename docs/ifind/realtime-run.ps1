$ErrorActionPreference = 'Stop'
$token = [Environment]::GetEnvironmentVariable('IFIND_ACCESS_TOKEN', 'User')
if ([string]::IsNullOrWhiteSpace($token)) { throw 'IFIND_ACCESS_TOKEN missing' }
$dir = Join-Path $PSScriptRoot ('evidence/realtime-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$null = New-Item -ItemType Directory -Path $dir
$cases = @(
  @{ id='realtime_index'; uri='real_time_quotation'; body=@{codes='883957.TI'; indicators='riseCount,fallCount,upLimitCount,downLimitCount,suspensionCount,tradeDate,tradeTime,preClose,open,high,low,latest,latestAmount,latestVolume,volume,amount,changeRatio,change,swing'} },
  @{ id='highfreq_original'; uri='high_frequency'; body=@{codes='883957.TI'; indicators='open,high,low,close,avgPrice,volume,amount,change,changeRatio,changeRatio_accumulated'; functionpara=@{Fill='Original'}; starttime='2026-09-09 09:15:00'; endtime='2026-09-09 15:15:00'} }
)
foreach ($case in $cases) {
  $body = $case.body | ConvertTo-Json -Compress -Depth 10
  [IO.File]::WriteAllText((Join-Path $dir ($case.id + '.request.json')), $body)
  $headers = @{'Content-Type'='application/json'; access_token=$token}
  try {
    $resp = Invoke-WebRequest -Uri ('https://quantapi.51ifind.com/api/v1/' + $case.uri) -Method Post -Headers $headers -Body $body -SkipHttpErrorCheck -TimeoutSec 30
    $safe = $resp.Content.Replace($token, '<REDACTED>')
    [IO.File]::WriteAllText((Join-Path $dir ($case.id + '.response.txt')), $safe)
    Write-Output ("{0}: HTTP {1} {2}" -f $case.id, $resp.StatusCode, $safe.Substring(0, [Math]::Min(600, $safe.Length)))
  } catch {
    [IO.File]::WriteAllText((Join-Path $dir ($case.id + '.error.txt')), 'network error')
    Write-Output ($case.id + ': network error')
  }
}
Write-Output ('Evidence: ' + $dir)
