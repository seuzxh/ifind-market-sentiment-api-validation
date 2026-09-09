#requires -Version 7.2
[CmdletBinding()]
param(
    [string[]]$TestId = @('history_single', 'calendar'),
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'requests.json') -Raw | ConvertFrom-Json -Depth 50
$knownIds = @($manifest.tests.id)
foreach ($id in $TestId) {
    if ($id -notin $knownIds) { throw "未知用例编号：$id" }
}
if ($manifest.origin -cne 'https://quantapi.51ifind.com') { throw '仅允许固定官方 HTTPS 地址。' }
$selected = @($manifest.tests | Where-Object { $_.id -in $TestId })
$runId = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$outputDir = Join-Path $PSScriptRoot "evidence/retest-$runId"
$null = New-Item -ItemType Directory -Path $outputDir

function Save-Json([string]$Path, $Value) {
    $Value | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $Path -Encoding utf8NoBOM
}
function Get-TextSha256([string]$Value) {
    [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($Value))).ToLowerInvariant()
}
function Get-Schema($Value, [int]$Depth = 0) {
    if ($null -eq $Value) { return 'null' }
    if ($Depth -ge 8) { return 'depth_limit' }
    if ($Value -is [System.Collections.IDictionary]) {
        $shape = [ordered]@{}
        foreach ($key in $Value.Keys) { $shape[$key] = Get-Schema $Value[$key] ($Depth + 1) }
        return $shape
    }
    if ($Value -is [array]) {
        return @{ type = 'array'; count = $Value.Count; firstItem = $(if ($Value.Count) { Get-Schema $Value[0] ($Depth + 1) } else { 'empty' }) }
    }
    return $Value.GetType().Name
}

# 仅读取两个明确授权的变量入口，不输出变量值。
$token = [Environment]::GetEnvironmentVariable('IFIND_ACCESS_TOKEN', 'Process')
$tokenSource = 'Process'
if ([string]::IsNullOrWhiteSpace($token)) {
    $token = [Environment]::GetEnvironmentVariable('IFIND_ACCESS_TOKEN', 'User')
    $tokenSource = 'User'
}
$hasToken = -not [string]::IsNullOrWhiteSpace($token)
Save-Json (Join-Path $outputDir 'preflight.json') @{
    checkedAt = [DateTimeOffset]::Now.ToString('o')
    testIds = @($selected.id)
    tokenPresent = $hasToken
    tokenSource = $(if ($hasToken) { $tokenSource } else { $null })
    preflightOnly = [bool]$PreflightOnly
    status = '尚未执行业务请求'
}
if (-not $hasToken) {
    Write-Host '无法复验：进程和当前用户环境变量均没有 IFIND_ACCESS_TOKEN。未发送业务请求。'
    Write-Host "预检报告：$outputDir"
    exit 2
}
if ($PreflightOnly) {
    Write-Host "预检完成，未发送业务请求。报告：$outputDir"
    exit 0
}

$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(25)
$results = [Collections.Generic.List[object]]::new()
$fatal = $false
try {
    foreach ($test in $selected) {
        if ($test.endpoint -cnotin @('history_data', 'get_trade_dates', 'smart_stock_picking', 'basic_data_service', 'data_pool', 'real_time_quotation', 'high_frequency')) {
            throw '请求端点不在允许列表。'
        }
        if ($results.Count -gt 0) { Start-Sleep -Seconds 1 }
        $url = 'https://quantapi.51ifind.com/api/v1/' + $test.endpoint
        $requestText = $test.body | ConvertTo-Json -Depth 50 -Compress
        [IO.File]::WriteAllText((Join-Path $outputDir ($test.id + '.request.json')), $requestText, [Text.UTF8Encoding]::new($false))
        $record = [ordered]@{
            testId = $test.id
            endpoint = $url
            startedAt = [DateTimeOffset]::Now.ToString('o')
            finishedAt = $null
            httpStatus = $null
            errorcode = $null
            requestSha256 = Get-TextSha256 $requestText
            responseSha256 = $null
            responseHashScope = '脱敏后 UTF-8 保存正文；非原始响应哈希'
            responseRedacted = $false
            schema = $null
            status = '未完成'
            acceptance = '数据验收未完成'
        }
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, $url)
        $request.Content = [Net.Http.StringContent]::new($requestText, [Text.Encoding]::UTF8, 'application/json')
        $null = $request.Headers.TryAddWithoutValidation('access_token', $token)
        $response = $null
        try {
            $response = $client.SendAsync($request).GetAwaiter().GetResult()
            $record.httpStatus = [int]$response.StatusCode
            $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            $safe = $raw.Replace($token, '<REDACTED>')
            $encodedToken = [uri]::EscapeDataString($token)
            if ($encodedToken -ne $token) { $safe = $safe.Replace($encodedToken, '<REDACTED>') }
            $safe = [regex]::Replace($safe, '(?i)("(?:access_token|refresh_token)"\s*:\s*")[^"]*(")', '$1<REDACTED>$2')
            $record.responseRedacted = $safe -cne $raw
            $raw = $null
            [IO.File]::WriteAllText((Join-Path $outputDir ($test.id + '.response.txt')), $safe, [Text.UTF8Encoding]::new($false))
            $record.responseSha256 = Get-TextSha256 $safe
            $parsed = $null
            try { $parsed = ConvertFrom-Json -InputObject $safe -AsHashtable -Depth 60 } catch { }
            $record.schema = Get-Schema $parsed
            if ($parsed -is [System.Collections.IDictionary] -and $parsed.Contains('errorcode')) {
                $record.errorcode = $parsed['errorcode']
            }
            if ($record.httpStatus -ge 300 -and $record.httpStatus -lt 400) {
                $record.status = '拒绝重定向，已停止'
                $fatal = $true
            } elseif ($record.httpStatus -eq 401 -or [string]$record.errorcode -in @('-1302', '-1303')) {
                $record.status = '认证或设备限制，已停止'
                $fatal = $true
            } elseif ($record.httpStatus -eq 200 -and $null -ne $record.errorcode -and [string]$record.errorcode -eq '0') {
                $record.status = '请求成功，待数据验收'
            } else {
                $record.status = '请求未通过'
            }
        } catch {
            # 不记录异常消息，避免 HTTP 库把认证头拼入异常文本。
            $record.status = '网络异常或超时，已停止'
            $record['exceptionType'] = $_.Exception.GetType().FullName
            $fatal = $true
        } finally {
            $record.finishedAt = [DateTimeOffset]::Now.ToString('o')
            $results.Add([pscustomobject]$record)
            Save-Json (Join-Path $outputDir ($test.id + '.meta.json')) $record
            $request.Dispose()
            if ($null -ne $response) { $response.Dispose() }
        }
        Write-Host ($test.id + '：' + $record.status)
        if ($fatal) { break }
    }
} finally {
    $client.Dispose()
    $handler.Dispose()
    $token = $null
    Save-Json (Join-Path $outputDir 'summary.json') @{
        finishedAt = [DateTimeOffset]::Now.ToString('o')
        results = @($results.ToArray())
        unexecuted = @($selected.id | Where-Object { $_ -notin @($results.testId) })
        acceptance = '请求成功不代表数据验收通过；需另外核对字段、单位、日期、覆盖和业务口径'
    }
}
Write-Host "复验记录：$outputDir"
if ($fatal -or @($results | Where-Object { $_.status -ne '请求成功，待数据验收' }).Count) { exit 1 }
exit 0
