# 简历猎手 Windows 更新全流程测试（GitHub Actions 专用）
#
# 用法：
#   pwsh scripts/ci/update-flow-windows.ps1 -ExeDir <v10 产物目录> -FromVersion 1.0.6 -ToVersion 1.0.7 [-Port 18686] [-TimeoutSeconds 150] [-ResumeDir tests/fixtures/resume_samples]
#
# 在真 Windows 机器上跑 v10 → v11 的完整更新：启动旧版、查新版、
# 后台下载、校验、apply 换文件、等新版重开、核对换文件结果、
# 再测简历上传，最后确认不再重复提示更新。
# 不登录任何真实招聘平台、不投递真实简历、不填 AI Key、
# 不用真机器码薅试用（启动时设 JIANLI_NO_AUTH_SYNC=1，跳过中转授权同步）。
#
# 注意：update.bat 重开新版时不带任何启动参数（见 updater.py
# build_update_script），所以重开后的新进程跑在默认端口 8686，
# 不在启动时的 $Port 上。restart/result/resume/no-loop/stop
# 各阶段一律走 8686，收尾杀进程也按 8686 的监听者找。
#
# 每个阶段失败都打印 "UPDATEFLOW_FAIL stage=<阶段> <原因>" 并以 exit 1
# 结束；全部通过打印 "UPDATEFLOW_OK from=<旧版> to=<新版>" 并 exit 0。
# 每个阶段都打印耗时，方便看出下载、换文件、重开各花了多久。

param(
  [Parameter(Mandatory = $true)][string]$ExeDir,
  [Parameter(Mandatory = $true)][string]$FromVersion,
  [Parameter(Mandatory = $true)][string]$ToVersion,
  [int]$Port = 18686,
  [int]$TimeoutSeconds = 150,
  [string]$ResumeDir = 'tests/fixtures/resume_samples'
)
$ErrorActionPreference = 'Stop'

# 重开后的新进程跑在默认端口 8686（update.bat 不传 --port）。
$RestartPort = 8686
$ReadyTimeoutSeconds = 300
$RestartTimeoutSeconds = 180

$tempDir = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$logOut = Join-Path $tempDir 'updateflow-exe-out.log'
$logErr = Join-Path $tempDir 'updateflow-exe-err.log'
$updateLog = Join-Path $ExeDir 'update.log'

function Show-LogTail {
  param([string]$Path)
  if (Test-Path $Path) {
    Get-Content $Path -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  exe-log: $_" }
  }
}

function Show-UpdateLogTail {
  if (Test-Path $updateLog) {
    Get-Content $updateLog -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  update-log: $_" }
  }
}

function Show-UpdateResult {
  # update.bat 换失败时会把 verdict 写进 update-result.json
  #（status "failed" + reason_code）；换成功但新版没起来时也可能
  # 已经写了。失败时打出来，不用连远程桌面也能看到原因。
  $resultPath = Join-Path $ExeDir 'update-result.json'
  if (Test-Path $resultPath) {
    $raw = Get-Content $resultPath -Raw -ErrorAction SilentlyContinue
    if ($raw) {
      Write-Host "  update-result.json: $raw"
    } else {
      Write-Host '  update-result.json: （文件存在但读不到内容）'
    }
  } else {
    Write-Host '  update-result.json: （文件不存在，bat 还没写 verdict 或还没跑到那一步）'
  }
}

function Show-StuckProcesses {
  # 换文件失败时看看 bat 还在不在、旧版 exe 还有没有残留：
  # bat 还在 + exe 已死 = bat 卡在等退出的循环里（timeout 不返回）；
  # 两个都不在 = bat 已经死了。两种原因的修法不一样，先分清。
  try {
    $cmds = Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -match 'update\.bat' }
    if ($cmds) {
      foreach ($c in $cmds) { Write-Host "  stuck-proc: 跑 update.bat 的 cmd.exe 还在：PID $($c.ProcessId)，$($c.CommandLine)" }
    } else {
      Write-Host '  stuck-proc: 没有跑 update.bat 的 cmd.exe（bat 已退出或已死）'
    }
  } catch {
    Write-Host "  stuck-proc: 查 cmd.exe 失败：$($_.Exception.Message)"
  }
  try {
    $exes = Get-Process -Name '简历猎手' -ErrorAction SilentlyContinue
    if ($exes) {
      foreach ($e in $exes) { Write-Host "  stuck-proc: 简历猎手.exe 还在跑：PID $($e.Id)" }
    } else {
      Write-Host '  stuck-proc: 没有简历猎手.exe 在跑'
    }
  } catch {
    Write-Host "  stuck-proc: 查简历猎手.exe 失败：$($_.Exception.Message)"
  }
}

function Show-BatDiagnostics {
  # restart 失败时的深挖诊断：两次失败现场一模一样（update.log 只有
  # 第一行、bat 的 cmd.exe 还在、无 exe 在跑），v14 的 PID+进程名
  # 双重匹配已被证实确实在跑的 bat 里，说明问题不在退出条件，而
  # 是某条命令本身在一次迭代里就不返回（tasklist / findstr / ping
  # 三者之一）。这里查 bat 进程的子进程、CPU 时间，再实测 ping
  # 和 tasklist 各要多久，直接定性卡在哪条命令。
  # 每项探针都有超时保护，诊断本身绝不能把测试再卡住；任何探针
  # 失败只打印一行，不抛异常。
  try {
    $cmds = Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -match 'update\.bat' }
    if (-not $cmds) {
      Write-Host '  diag-bat: 没有跑 update.bat 的 cmd.exe（bat 已退出或已死）'
    } else {
      foreach ($c in $cmds) {
        $batPid = $c.ProcessId
        Write-Host "  diag-bat: bat cmd.exe PID $batPid，启动于 $($c.CreationDate)"
        try {
          $p = Get-Process -Id $batPid -ErrorAction SilentlyContinue
          if ($p) {
            Write-Host "  diag-bat: bat cmd.exe CPU 累计 $($p.CPU) 秒（CPU 高=循环空转，CPU 低=卡在一个调用上）"
          }
        } catch {
          Write-Host "  diag-bat: 查 CPU 时间失败：$($_.Exception.Message)"
        }
        try {
          $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $batPid" -ErrorAction SilentlyContinue
          if ($children) {
            foreach ($child in $children) {
              Write-Host "  diag-bat: bat 的子进程还活着：$($child.Name) PID $($child.ProcessId)，$($child.CommandLine)"
            }
          } else {
            Write-Host '  diag-bat: bat 没有活着的子进程（tasklist/ping/findstr 都已返回，卡在循环本身）'
          }
        } catch {
          Write-Host "  diag-bat: 查子进程失败：$($_.Exception.Message)"
        }
        try {
          $batPath = $null
          if ($c.CommandLine -match '([A-Za-z]:\\[^\s"]+\.bat)') { $batPath = $Matches[1] }
          if (-not $batPath -or -not (Test-Path $batPath)) {
            foreach ($cand in @((Join-Path $ExeDir 'update.bat'), (Join-Path $env:RUNNER_TEMP 'update-run\update.bat'), (Join-Path $env:TEMP 'update-run\update.bat'))) {
              if ($cand -and (Test-Path $cand)) { $batPath = $cand; break }
            }
          }
          if ($batPath -and (Test-Path $batPath)) {
            Write-Host "  diag-bat: update.bat 路径 $batPath，内容如下："
            Get-Content $batPath -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  diag-bat: $_" }
          } else {
            Write-Host '  diag-bat: 找不到 update.bat 文件（命令行和默认位置都没找到）'
          }
        } catch {
          Write-Host "  diag-bat: dump update.bat 失败：$($_.Exception.Message)"
        }
      }
    }
  } catch {
    Write-Host "  diag-bat: 诊断失败：$($_.Exception.Message)"
  }
  # ping 探针：bat 每次循环都跑 ping -n 2，实测它在当前机器上要多久。
  try {
    $t0 = Get-Date
    $job = Start-Job -ScriptBlock { ping.exe -n 2 127.0.0.1 } -ErrorAction SilentlyContinue
    if ($job) {
      $done = Wait-Job $job -Timeout 15
      $elapsed = ((Get-Date) - $t0).TotalSeconds
      if ($done) {
        $null = Receive-Job $job -ErrorAction SilentlyContinue
        Write-Host "  diag-ping: ping -n 2 127.0.0.1 用了 $($elapsed.ToString('0.0')) 秒，正常返回"
      } else {
        Write-Host "  diag-ping: TIMEOUT，ping -n 2 127.0.0.1 在 15 秒内没返回（bat 可能就卡在 ping 上）"
      }
      Stop-Job $job -ErrorAction SilentlyContinue | Out-Null
      Remove-Job $job -Force -ErrorAction SilentlyContinue | Out-Null
    } else {
      Write-Host '  diag-ping: 启动探针作业失败'
    }
  } catch {
    Write-Host "  diag-ping: 探针失败：$($_.Exception.Message)"
  }
  # tasklist 探针：bat 里同款双过滤，实测它要多久、返回什么。
  try {
    $oldPid = $null
    if ($proc) { $oldPid = $proc.Id }
    if ($oldPid) {
      $t0 = Get-Date
      $job = Start-Job -ScriptBlock {
        param($OldPid)
        tasklist /FI "PID eq $OldPid" /FI "IMAGENAME eq 简历猎手.exe"
      } -ArgumentList $oldPid -ErrorAction SilentlyContinue
      if ($job) {
        $done = Wait-Job $job -Timeout 15
        $elapsed = ((Get-Date) - $t0).TotalSeconds
        if ($done) {
          $out = Receive-Job $job -ErrorAction SilentlyContinue
          Write-Host "  diag-tasklist: 双过滤 tasklist 用了 $($elapsed.ToString('0.0')) 秒，输出如下："
          $out | ForEach-Object { Write-Host "  diag-tasklist: $_" }
        } else {
          Write-Host "  diag-tasklist: TIMEOUT，双过滤 tasklist 在 15 秒内没返回（bat 可能就卡在 tasklist 上）"
        }
        Stop-Job $job -ErrorAction SilentlyContinue | Out-Null
        Remove-Job $job -Force -ErrorAction SilentlyContinue | Out-Null
      } else {
        Write-Host '  diag-tasklist: 启动探针作业失败'
      }
    } else {
      Write-Host '  diag-tasklist: 旧进程 PID 未知（$proc 为空），跳过探针'
    }
  } catch {
    Write-Host "  diag-tasklist: 探针失败：$($_.Exception.Message)"
  }
}

function Stop-ListeningProcess {
  param([int]$ListenPort)
  $conns = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
  }
}

function Measure-Stage {
  param([string]$Name, [scriptblock]$Body)
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    & $Body
  } finally {
    $sw.Stop()
    Write-Host "stage-time $Name $($sw.Elapsed.TotalSeconds.ToString('0.0'))s"
  }
}

$proc = $null
$base = "http://127.0.0.1:$Port"
$restartBase = "http://127.0.0.1:$RestartPort"
try {
  # ---- 阶段 package：产物齐不齐 ----
  Measure-Stage 'package' {
    $exe = Join-Path $ExeDir '简历猎手.exe'
    if (-not (Test-Path $exe)) { throw 'stage=package 打包坏了：找不到 简历猎手.exe' }
    $indexHtml = Get-ChildItem -Path $ExeDir -Recurse -Filter 'index.html' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $indexHtml) { throw 'stage=package 打包坏了：产物里没有面板首页 index.html' }
    Write-Host "stage=package OK：exe 与面板首页都在"
  }

  # ---- 阶段 port：两个端口都要空闲（18686 给旧版，8686 给重开的新版） ----
  Measure-Stage 'port' {
    foreach ($p in @($Port, $RestartPort)) {
      $client = New-Object System.Net.Sockets.TcpClient
      try {
        $connect = $client.BeginConnect('127.0.0.1', $p, $null, $null)
        $connected = $connect.AsyncWaitHandle.WaitOne(2000)
        if ($connected -and $client.Connected) { throw "stage=port 端口 $p 已被占用，换台干净机器或等一会儿再跑" }
      } catch {
        if ($_.Exception.Message -like 'stage=port*') { throw $_ }
        # 连不上就是空闲，继续。
      } finally {
        $client.Close()
      }
    }
    Write-Host "stage=port OK：$Port 与 $RestartPort 都空闲"
  }

  # ---- 阶段 start：旧版 exe 能不能起来 ----
  Measure-Stage 'start' {
    # JIANLI_NO_AUTH_SYNC=1：跳过启动时的中转授权同步，不拿这台临时机器的机器码去薅试用。
    $env:JIANLI_NO_AUTH_SYNC = '1'
    Remove-Item $logOut, $logErr -ErrorAction SilentlyContinue
    $exePath = Join-Path $ExeDir '简历猎手.exe'
    $script:proc = Start-Process -FilePath $exePath -ArgumentList 'web', '--port', "$Port", '--no-open' `
      -WorkingDirectory $ExeDir -RedirectStandardOutput $logOut -RedirectStandardError $logErr `
      -WindowStyle Hidden -PassThru
    $proc = $script:proc
    Start-Sleep -Seconds 5
    if ($proc.HasExited) {
      Show-LogTail $logOut
      Show-LogTail $logErr
      throw "stage=start 旧版 exe 起不来：启动 5 秒就退出了（exit $($proc.ExitCode)）"
    }
    Write-Host "stage=start OK：旧版 exe 已启动（PID $($proc.Id)）"
  }

  # ---- 阶段 panel：旧版面板能不能打开 ----
  Measure-Stage 'panel' {
    $homePage = $null
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
      if ($proc.HasExited) {
        Show-LogTail $logOut
        Show-LogTail $logErr
        throw "stage=start 旧版 exe 起不来：等面板时进程退出了（exit $($proc.ExitCode)）"
      }
      try {
        $homePage = Invoke-WebRequest -Uri "$base/" -UseBasicParsing -TimeoutSec 5
        if ($homePage.StatusCode -eq 200) { break }
      } catch {
        # 还没起来，继续等。
      }
      Start-Sleep -Seconds 5
    }
    if (-not $homePage -or $homePage.StatusCode -ne 200) {
      Show-LogTail $logOut
      Show-LogTail $logErr
      throw "stage=panel 旧版面板打不开：${TimeoutSeconds} 秒内 http://127.0.0.1:$Port/ 一直没返回 200"
    }
    Write-Host 'stage=panel OK：旧版面板返回 200'
  }

  # ---- 阶段 version：旧版版本号对不对 ----
  Measure-Stage 'version' {
    try {
      $health = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 10
    } catch {
      throw "stage=version 版本查不到：/api/health 请求失败：$($_.Exception.Message)"
    }
    if ($health.version -ne $FromVersion) {
      throw "stage=version 旧版版本号对不上：面板报 v$($health.version)，期望 v$FromVersion"
    }
    Write-Host "stage=version OK：旧版面板版本 v$($health.version)"
  }

  # ---- 阶段 check：能查到目标新版 ----
  Measure-Stage 'check' {
    try {
      $check = Invoke-RestMethod -Uri "$base/api/update/check?force=1" -TimeoutSec 30
    } catch {
      throw "stage=check 更新检查失败：/api/update/check 请求失败：$($_.Exception.Message)"
    }
    if ($check.update_available -ne $true -or $check.version -ne $ToVersion) {
      throw "stage=check 查不到新版 v$ToVersion：update_available=$($check.update_available)，version=$($check.version)，status=$($check.status)，error=$($check.error)"
    }
    Write-Host "stage=check OK：查到新版 v$($check.version)，size=$($check.size)"
  }

  # ---- 阶段 start-download：开始后台下载 ----
  Measure-Stage 'start-download' {
    try {
      $started = Invoke-RestMethod -Uri "$base/api/update/start" -Method Post -TimeoutSec 30
    } catch {
      throw "stage=start-download 开始下载失败：/api/update/start 请求失败：$($_.Exception.Message)"
    }
    if ($started.status -notin @('downloading', 'verifying', 'ready')) {
      throw "stage=start-download 开始下载后状态不对：status=$($started.status)，error=$($started.error)"
    }
    Write-Host "stage=start-download OK：后台下载已开始（status=$($started.status)）"
  }

  # ---- 阶段 ready：等下载 + 校验完成 ----
  Measure-Stage 'ready' {
    $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
    $lastStatus = $null
    while ((Get-Date) -lt $deadline) {
      try {
        $lastStatus = Invoke-RestMethod -Uri "$base/api/update/status" -TimeoutSec 10
      } catch {
        throw "stage=ready 下载状态查不到：/api/update/status 请求失败：$($_.Exception.Message)"
      }
      if ($lastStatus.status -eq 'ready') { break }
      if ($lastStatus.status -eq 'error') {
        throw "stage=ready 下载/校验失败：$($lastStatus.error)"
      }
      Start-Sleep -Seconds 10
    }
    if (-not $lastStatus -or $lastStatus.status -ne 'ready') {
      throw "stage=ready 下载超时：${ReadyTimeoutSeconds} 秒内状态一直没到 ready（最后 status=$($lastStatus.status)，progress=$($lastStatus.progress)）"
    }
    Write-Host "stage=ready OK：新版下好了并通过校验（version=$($lastStatus.version)）"
  }

  # ---- 阶段 apply：换文件并重开 ----
  Measure-Stage 'apply' {
    try {
      $applied = Invoke-RestMethod -Uri "$base/api/update/apply" -Method Post -TimeoutSec 30
    } catch {
      throw "stage=apply 换文件失败：/api/update/apply 请求失败：$($_.Exception.Message)"
    }
    if ($applied.applying -ne $true) {
      throw "stage=apply 换文件没开始：返回 applying=$($applied.applying)，error=$($applied.error)"
    }
    $script:applyAt = Get-Date
    Write-Host 'stage=apply OK：已开始换文件，旧进程将退出并重开新版'
  }

  # ---- 阶段 restart：等旧进程退出、新版在 8686 上起来并报新版 ----
  Measure-Stage 'restart' {
    $applyAt = $script:applyAt
    if ($proc -and -not $proc.HasExited) {
      if (-not $proc.WaitForExit(120000)) {
        Show-UpdateLogTail
        throw 'stage=restart 旧进程退不掉：apply 后 120 秒还在跑'
      }
    }
    Write-Host "stage=restart 旧进程已退出（PID $($proc.Id)）"
    $deadline = (Get-Date).AddSeconds($RestartTimeoutSeconds)
    $newHealth = $null
    while ((Get-Date) -lt $deadline) {
      try {
        $newHealth = Invoke-RestMethod -Uri "$restartBase/api/health" -TimeoutSec 5
        if ($newHealth.version -eq $ToVersion) { break }
        $newHealth = $null
      } catch {
        # 新版还没起来，继续等。
      }
      Start-Sleep -Seconds 5
    }
    if (-not $newHealth -or $newHealth.version -ne $ToVersion) {
      Show-UpdateLogTail
      Show-LogTail $logOut
      Show-LogTail $logErr
      throw "stage=restart 新版没起来：${RestartTimeoutSeconds} 秒内 http://127.0.0.1:$RestartPort/api/health 一直没报 v$ToVersion"
    }
    $restartSeconds = ((Get-Date) - $applyAt).TotalSeconds
    Write-Host "stage=restart OK：新版已在 $RestartPort 上起来，版本 v$($newHealth.version)，从 apply 到新版可用共 $($restartSeconds.ToString('0.0')) 秒"
  }

  # ---- 阶段 result：换文件结果文件 verdict 为 ok，pending 已消费 ----
  # 注意顺序：这一步必须在 no-loop 之前，因为 force 检查会清掉
  # update-result.json（clear_update_result），先读结果再查更新。
  Measure-Stage 'result' {
    $resultPath = Join-Path $ExeDir 'update-result.json'
    $pendingPath = Join-Path $ExeDir 'update-pending.json'
    $verdict = $null
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
      if (Test-Path $resultPath) {
        try { $verdict = Get-Content $resultPath -Raw | ConvertFrom-Json } catch { $verdict = $null }
        if ($verdict -and $verdict.status -eq 'ok') { break }
        $verdict = $null
      }
      Start-Sleep -Seconds 2
    }
    if (-not $verdict -or $verdict.status -ne 'ok') {
      Show-UpdateLogTail
      $raw = if (Test-Path $resultPath) { Get-Content $resultPath -Raw } else { '(文件不存在)' }
      throw "stage=result 换文件结果不对：update-result.json 内容为 $raw"
    }
    if ($verdict.expected_version -ne $ToVersion -or $verdict.actual_version -ne $ToVersion) {
      throw "stage=result 换文件结果里的版本不对：expected=$($verdict.expected_version)，actual=$($verdict.actual_version)，期望 v$ToVersion"
    }
    if (Test-Path $pendingPath) {
      throw 'stage=result pending 标记没被消费：update-pending.json 还在'
    }
    Write-Host 'stage=result OK：update-result.json 为 ok，update-pending.json 已消费'
  }

  # ---- 阶段 resume：新版上传 .docx 和 .pdf，断言解析内容 ----
  Measure-Stage 'resume' {
    $resumeDirResolved = (Resolve-Path $ResumeDir -ErrorAction SilentlyContinue).Path
    if (-not $resumeDirResolved) { throw "stage=resume 简历样本目录找不到：$ResumeDir" }
    $cases = @(
      @{ file = 'sdt.docx'; expects = @('张三', '工作经历', '负责后端开发与接口设计'); minLen = 200 },
      @{ file = 'word-chinese.pdf'; expects = @('张三', '工作经历', 'Python 工程师'); minLen = 0 }
    )
    foreach ($case in $cases) {
      $filePath = Join-Path $resumeDirResolved $case.file
      if (-not (Test-Path $filePath)) { throw "stage=resume 样本文件找不到：$filePath" }
      try {
        $resp = Invoke-RestMethod -Uri "$restartBase/api/resume/upload" -Method Post -Form @{ file = (Get-Item $filePath) } -TimeoutSec 60
      } catch {
        throw "stage=resume 上传 $($case.file) 失败：请求失败：$($_.Exception.Message)"
      }
      if ($resp.success -ne $true) {
        throw "stage=resume 上传 $($case.file) 失败：返回 success=$($resp.success)，error=$($resp.error)"
      }
      $content = $resp.content
      if (-not $content) { throw "stage=resume 上传 $($case.file) 后解析内容为空" }
      foreach ($expect in $case.expects) {
        if ($content -notmatch [regex]::Escape($expect)) {
          throw "stage=resume 上传 $($case.file) 后解析内容缺失 $expect（解析出 $($content.Length) 字）"
        }
      }
      if ($content.Length -lt $case.minLen) {
        throw "stage=resume 上传 $($case.file) 后解析内容太短：$($content.Length) 字，期望至少 $($case.minLen) 字"
      }
      Write-Host "stage=resume $($case.file) OK：解析出 $($content.Length) 字，关键内容都在"
    }
    Write-Host 'stage=resume OK：sdt.docx 与 word-chinese.pdf 都上传并解析成功'
  }

  # ---- 阶段 no-loop：新版再查一次更新，确认无新版（不循环提示） ----
  Measure-Stage 'no-loop' {
    try {
      $again = Invoke-RestMethod -Uri "$restartBase/api/update/check?force=1" -TimeoutSec 30
    } catch {
      throw "stage=no-loop 再次检查更新失败：/api/update/check 请求失败：$($_.Exception.Message)"
    }
    if ($again.update_available -ne $false) {
      throw "stage=no-loop 更新后还在提示新版：update_available=$($again.update_available)，version=$($again.version)"
    }
    Write-Host 'stage=no-loop OK：新版再查无新版，不循环提示'
  }
} catch {
  Write-Host "UPDATEFLOW_FAIL $_"
  Show-LogTail $logOut
  Show-LogTail $logErr
  Show-UpdateLogTail
  Show-UpdateResult
  Show-StuckProcesses
  Show-BatDiagnostics
  if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  }
  Stop-ListeningProcess -ListenPort $Port
  Stop-ListeningProcess -ListenPort $RestartPort
  exit 1
}

# 正常收尾：按端口找重开后的新进程杀掉，确认它退干净了。
# 注意：apply 后原 $proc 已退出，新进程是 update.bat 拉起来的，
# 不能只杀旧 $proc，必须按 8686 的监听者找。
Measure-Stage 'stop' {
  $conns = Get-NetTCPConnection -LocalPort $RestartPort -State Listen -ErrorAction SilentlyContinue
  if ($conns) {
    $listenerPids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($listenerPid in $listenerPids) {
      Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
      $left = Get-NetTCPConnection -LocalPort $RestartPort -State Listen -ErrorAction SilentlyContinue
      if (-not $left) { break }
      Start-Sleep -Seconds 1
    }
    $left = Get-NetTCPConnection -LocalPort $RestartPort -State Listen -ErrorAction SilentlyContinue
    if ($left) {
      Write-Host 'UPDATEFLOW_FAIL stage=stop 新版进程关不掉：杀掉 8686 监听者后 10 秒端口还在被占'
      exit 1
    }
  }
  if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  }
  Write-Host 'stage=stop OK：新版进程已退出'
}
Write-Host "UPDATEFLOW_OK from=$FromVersion to=$ToVersion"
exit 0
