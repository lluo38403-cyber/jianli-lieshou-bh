# 简历猎手 Windows 更新全流程测试（GitHub Actions 专用）
#
# 用法：
#   pwsh scripts/ci/update-flow-windows.ps1 -ExeDir <v10 产物目录> -FromVersion 1.0.6 -ToVersion 1.0.7 [-Port 18686] [-TimeoutSeconds 150] [-ResumeDir tests/fixtures/resume_samples] [-Scenario Main|Lock|Retry]
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
# 场景（-Scenario，默认 Main）：
#   Main：15 阶段正测（含 swap-watch，见下）。
#   Lock：Main 全链 + lock/lock-assert（共 17 阶段）。ready 后、
#     apply 前拉起占位 node.exe（直接跑安装目录里
#     _internal\patchright\driver\node.exe，跑起来的 exe 自己锁住
#     镜像文件——就是生产上孤儿 node.exe 的真实形态），断言换文件
#     步骤杀得掉它、换得成新版。注意占位不用 PowerShell
#     [IO.File]::Open 去占文件：powershell.exe 在 System32 里，
#     产品按 ExecutablePath 只杀安装目录里的进程，杀不到它，
#     场景会走到 :fail_stuck——那是测试写法的问题，不是产品的 bug。
#   Retry：Main 全链（第一遍期望回滚）+ lock/retry-check/reuse/
#     apply2/swap-watch2/restart2/result2（共 22 阶段）。看门狗让
#     占位 node.exe 被杀后 1 秒内重拉，robocopy 重试耗尽后回滚；
#     断言旧版还在、config/data 未动、手动重查仍报新版、缓存包复用
#     （不用重下）、第二遍 apply 直达新版。
#
#   swap-watch（所有场景，apply 与 restart 之间，最高优先级）：
#     lucky 2026-11-20 定性——换版本后应自动关闭当前窗口再重开，
#     两个窗口冲突是 bug：旧面板窗口根本没关、整个旧进程树还活着，
#     换文件必然撞锁。这里每 0.5 秒采样，断言 robocopy 开始之前
#     旧 exe 已死、安装目录下已无进程（孤儿 node.exe 也须死透），
#     而不是等到 restart 阶段才发现还活着。流程以 --no-open 启动、
#     没有独立 --app 窗口，“面板窗口已关”在流程内等价于旧 exe
#     进程已死 + 旧端口不再响应。
#
#   from 版本要求：换文件那一步的 bat 是旧版 exe 写出来的（旧版
#     updater.py），所以 Lock 的杀进程步骤和 Retry 的缓存复用只有
#     from 版本 ≥ 1.1.10（带清场步骤 + 缓存落盘的版本）时才能测到；
#     from 更老时 Lock 会在 swap-watch/lock-assert 按预期失败
#     （旧 bat 杀不掉占位），Retry 的复用断言会明确 SKIP、走完整
#     重下重试（失败重试本身仍验证）。Main + swap-watch 不受此限。
#
# 每个阶段失败都打印 "UPDATEFLOW_FAIL stage=<阶段> <原因>" 并以 exit 1
# 结束；全部通过打印 "UPDATEFLOW_OK scenario=<场景> from=<旧版> to=<新版>" 并 exit 0。
# 每个阶段都打印耗时，方便看出下载、换文件、重开各花了多久。

param(
  [Parameter(Mandatory = $true)][string]$ExeDir,
  [Parameter(Mandatory = $true)][string]$FromVersion,
  [Parameter(Mandatory = $true)][string]$ToVersion,
  [int]$Port = 18686,
  [int]$TimeoutSeconds = 150,
  [string]$ResumeDir = 'tests/fixtures/resume_samples',
  [ValidateSet('Main','Lock','Retry')][string]$Scenario = 'Main'
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
$LockNodePath = Join-Path $ExeDir '_internal\patchright\driver\node.exe'
$holderPidFile = Join-Path $tempDir 'updateflow-holder.pid'
$watchdogPidFile = Join-Path $tempDir 'updateflow-watchdog.pid'
$watchdogStateFile = Join-Path $tempDir 'updateflow-watchdog.json'

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

function Get-InstallDirProcesses {
  # 安装目录下还有哪些进程在跑（与 bat 的 :procsweep 同款判定：
  # ExecutablePath 以安装目录开头）。只查 node.exe / 简历猎手.exe
  # 两个映像——别的进程不可能从安装目录里启动。返回数组（没有时为空）。
  $found = @()
  try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='简历猎手.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
      if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($ExeDir + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $found += $p
      }
    }
  } catch {
    Write-Host "  swap-watch: 查安装目录进程失败：$($_.Exception.Message)"
  }
  return $found
}

function Test-FileExclusiveOpen {
  param([string]$Path)
  # 文件能不能独占打开（FileShare::None）：能打开=没人占着；
  # 打不开（IOException）=还被占着。返回 $true=能独占打开。
  try {
    $fs = [IO.File]::Open($Path, 'Open', 'Read', 'None')
    $fs.Dispose()
    return $true
  } catch [System.IO.IOException] {
    return $false
  } catch {
    return $false
  }
}

function Start-LockHolder {
  # 占位进程：直接跑安装目录里的 node.exe 本身（-e 空转 600 秒）。
  # 跑起来的 exe 自己会把镜像文件锁住——这就是生产上孤儿
  # node.exe 的真实形态（旧版退出时不杀子进程，driver node 变成
  # 孤儿继续跑，robocopy 换 _internal 就报错误 32）。
  # 注意不用 PowerShell [IO.File]::Open 去占：powershell.exe 在
  # System32 里，产品按 ExecutablePath 只杀安装目录里的进程，
  # 杀不到它，场景 A 永远走 :fail_stuck——那是测试写法的问题，
  # 不是产品的 bug。PID 写文件备查。
  param([string]$NodePath)
  if (-not (Test-Path $NodePath)) { throw "stage=lock 占位目标不存在：$NodePath（打包没带 patchright driver node.exe？）" }
  $holder = Start-Process -FilePath $NodePath -ArgumentList '-e', 'setTimeout(()=>{},600000)' -WindowStyle Hidden -PassThru
  if (-not $holder) { throw 'stage=lock 占位进程拉不起来' }
  $script:holderPid = $holder.Id
  $holder.Id | Out-File -FilePath $holderPidFile -Encoding utf8
  # 等锁真的占上（独占打开必须失败），最多等 10 秒。
  $deadline = (Get-Date).AddSeconds(10)
  while ((Get-Date) -lt $deadline) {
    if (-not (Test-FileExclusiveOpen -Path $NodePath)) {
      Write-Host "stage=lock OK：占位 node.exe 已跑起来（PID $($holder.Id)），$NodePath 已被它锁住"
      return
    }
    Start-Sleep -Milliseconds 500
  }
  throw "stage=lock 占位没生效：占位 node.exe PID $($holder.Id) 跑了 10 秒，$NodePath 还能独占打开"
}

function Start-LockWatchdog {
  # 场景 B 的看门狗：占位 node.exe 被杀后 1 秒内重拉并重新锁定；
  # 见到 update.log 出现“复制失败”或 90 秒后停止，停时先杀掉当下
  # 的占位进程（让回滚能顺利复制、旧版能重开），再退出。
  # 看门狗自己是独立的 powershell.exe（不在 exe 子进程树里），
  # 它的 PID 也写文件，收尾兜底能停掉它。
  param([string]$NodePath)
  if (-not (Test-Path $NodePath)) { throw "stage=lock 占位目标不存在：$NodePath（打包没带 patchright driver node.exe？）" }
  $watchdogCode = @'
param([string]$NodePath, [string]$LogPath, [string]$StatePath, [string]$HolderPidFile)
$deadline = (Get-Date).AddSeconds(90)
$relaunches = 0
$holder = Start-Process -FilePath $NodePath -ArgumentList '-e', 'setTimeout(()=>{},600000)' -WindowStyle Hidden -PassThru
if ($holder) { $relaunches++; $holder.Id | Out-File -FilePath $HolderPidFile -Encoding utf8 }
while ((Get-Date) -lt $deadline) {
  if (-not $holder -or $holder.HasExited) {
    $holder = Start-Process -FilePath $NodePath -ArgumentList '-e', 'setTimeout(()=>{},600000)' -WindowStyle Hidden -PassThru
    if ($holder) { $relaunches++; $holder.Id | Out-File -FilePath $HolderPidFile -Encoding utf8 }
  }
  if (Test-Path $LogPath) {
    $tail = Get-Content $LogPath -Tail 5 -ErrorAction SilentlyContinue
    if ($tail -match '复制失败') { break }
  }
  Start-Sleep -Seconds 1
}
if ($holder -and -not $holder.HasExited) {
  Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
}
@{ relaunches = $relaunches; stopped_at = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -Path $StatePath -Encoding utf8
'@
  $watchdogScript = Join-Path $tempDir 'updateflow-watchdog.ps1'
  Set-Content -Path $watchdogScript -Value $watchdogCode -Encoding UTF8
  Remove-Item $watchdogPidFile -ErrorAction SilentlyContinue
  $wd = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile', '-NonInteractive', '-File', $watchdogScript, '-NodePath', $NodePath, '-LogPath', $updateLog, '-StatePath', $watchdogStateFile, '-HolderPidFile', $holderPidFile -WindowStyle Hidden -PassThru
  if (-not $wd) { throw 'stage=lock 看门狗拉不起来' }
  $script:watchdogPid = $wd.Id
  $wd.Id | Out-File -FilePath $watchdogPidFile -Encoding utf8
  # 等第一把锁占上（独占打开必须失败），最多等 10 秒。
  $deadline = (Get-Date).AddSeconds(10)
  while ((Get-Date) -lt $deadline) {
    if (-not (Test-FileExclusiveOpen -Path $NodePath)) {
      Write-Host "stage=lock OK：看门狗已启动（PID $($wd.Id)），占位 node.exe 已锁住 $NodePath"
      return
    }
    Start-Sleep -Milliseconds 500
  }
  throw "stage=lock 看门狗没把锁占上：10 秒后 $NodePath 还能独占打开"
}

function Stop-LockHolder {
  # 收尾兜底：正常流程里占位进程应已被更新步骤杀掉（场景 A）或
  # 被看门狗自己停掉（场景 B）；还活着就说明杀进程那步没生效
  # （场景此时早已按断言失败），这里只负责不把锁留给下一个测试。
  # 看门狗也一起停。只按 PID 文件杀，绝不按映像名扫射。
  foreach ($pidFile in @($holderPidFile, $watchdogPidFile)) {
    if (Test-Path $pidFile) {
      foreach ($id in (Get-Content $pidFile -ErrorAction SilentlyContinue)) {
        try {
          $p = Get-Process -Id $id -ErrorAction SilentlyContinue
          if ($p) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
        } catch { }
      }
      Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
  }
  if ($script:holderPid) {
    try { Stop-Process -Id $script:holderPid -Force -ErrorAction SilentlyContinue } catch { }
  }
  if ($script:watchdogPid) {
    try { Stop-Process -Id $script:watchdogPid -Force -ErrorAction SilentlyContinue } catch { }
  }
}

function Get-PathHash {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return '(missing)' }
  $h = Get-FileHash -Path $Path -Algorithm SHA256 -ErrorAction SilentlyContinue
  if (-not $h) { return '(unreadable)' }
  return $h.Hash
}

function Get-DataTreeHash {
  param([string]$Dir)
  # data/ 的树哈希：排除运行时文件（.db/.log 等，旧版重启、巡检
  # 也会写它们，变化不代表更新动了用户数据）；config.yaml、
  # update-cache（manifest+zip）都在内——失败的更新绝不能动它们。
  $files = Get-ChildItem -Path $Dir -Recurse -File -Exclude '*.db', '*.db-journal', '*.db-wal', '*.db-shm', '*.log' -ErrorAction SilentlyContinue | Sort-Object FullName
  if (-not $files) { return '(empty)' }
  $lines = foreach ($f in $files) {
    "$($f.FullName)|$((Get-FileHash -Path $f.FullName -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash)"
  }
  $bytes = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
  return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes))).Replace('-', '')
}

function Show-LockDiagnostics {
  # 锁场景失败时的定位信息：持锁 PID、锁路径、manifest 内容、
  # zip 的 mtime/sha。只打印，不抛异常。
  Write-Host "  lock-diag: 锁路径 $LockNodePath（存在：$(Test-Path $LockNodePath)）"
  if (Test-Path $holderPidFile) {
    foreach ($id in (Get-Content $holderPidFile -ErrorAction SilentlyContinue)) {
      $p = Get-Process -Id $id -ErrorAction SilentlyContinue
      if ($p) { Write-Host "  lock-diag: 占位进程还活着：PID $id，$($p.ProcessName)，$($p.Path)" }
      else { Write-Host "  lock-diag: 占位 PID $id 已死" }
    }
  } else {
    Write-Host '  lock-diag: 没有占位 PID 文件'
  }
  if (Test-Path $watchdogStateFile) {
    Write-Host "  lock-diag: 看门狗状态：$(Get-Content $watchdogStateFile -Raw -ErrorAction SilentlyContinue)"
  }
  $manifest = Join-Path $ExeDir 'data\update-cache\update-ready.json'
  if (Test-Path $manifest) {
    Write-Host "  lock-diag: manifest 内容：$(Get-Content $manifest -Raw -ErrorAction SilentlyContinue)"
    $zip = Get-ChildItem -Path (Join-Path $ExeDir 'data\update-cache') -Filter 'jianli-lieshou-*.zip' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($zip) {
      Write-Host "  lock-diag: 缓存 zip：$($zip.FullName)，mtime $($zip.LastWriteTimeUtc.ToString('o'))，sha256 $((Get-FileHash -Path $zip.FullName -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash)"
    } else {
      Write-Host '  lock-diag: 缓存目录里没有 zip'
    }
  } else {
    Write-Host '  lock-diag: data\update-cache\update-ready.json 不存在（包复用实现未落地或缓存已被清）'
  }
}

function Assert-SwapOrder {
  # 换文件期间的顺序断言（lucky 2026-11-20 定性：两个窗口冲突——
  # 换版本后应自动关闭当前窗口再重开；旧面板窗口根本没关、整个
  # 旧进程树还活着，换文件必然撞锁）。
  # apply 返回后、restart 完成前调用：每 0.5 秒采样，直到 update.log
  # 出现拷开始（“正在换上新版本”或 robocopy 退出码行）或 120 秒超时。
  # 断言拷开始时旧 exe 已死——不是等到 restart 阶段才发现还活着。
  # -ExpectInstallDirClear 时还断言安装目录下已无进程（孤儿 node.exe
  # 也须在拷前死掉）；Retry 第一遍不传（占位就是要一直占着）。
  # -OldPid：已知旧 exe PID 时传（第一遍是 $proc.Id；第二遍从 8686
  # 监听者取）；不传时只按进程名查。
  param([int]$OldPid = 0, [switch]$ExpectInstallDirClear)
  $deadline = (Get-Date).AddSeconds(120)
  $oldDeadSeen = $false
  $copySeen = $false
  while ((Get-Date) -lt $deadline) {
    $oldAlive = $false
    $oldDesc = ''
    if ($OldPid -ne 0) {
      $p = Get-Process -Id $OldPid -ErrorAction SilentlyContinue
      if ($p) { $oldAlive = $true; $oldDesc = "旧 exe PID $OldPid 还活着（$($p.ProcessName)）" }
    }
    if (-not $oldAlive) {
      $same = Get-Process -Name '简历猎手' -ErrorAction SilentlyContinue
      if ($same) { $oldAlive = $true; $oldDesc = "同名残留进程还活着：$(($same | ForEach-Object { 'PID ' + $_.Id }) -join '、')" }
    }
    if (-not $oldAlive -and -not $oldDeadSeen) {
      $oldDeadSeen = $true
      # 旧进程死了，面板服务也应已停（旧端口不再响应）——这就是
      # “面板窗口已关”的流程内等价断言（流程以 --no-open 启动，
      # 没有独立 --app 窗口；面板由旧 exe 进程提供服务）。
      try {
        $probe = Invoke-WebRequest -Uri "$base/api/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        Write-Host "  swap-watch: 注意：旧 exe 已死，但旧端口 $Port 仍在响应 /api/health（返回 $($probe.StatusCode)）"
      } catch {
        # 连不上就是关干净了，正常。
      }
    }
    $installProcs = @(Get-InstallDirProcesses)
    $copyStarted = $false
    if (Test-Path $updateLog) {
      $tail = Get-Content $updateLog -Tail 10 -ErrorAction SilentlyContinue
      if ($tail -match '正在换上新版本' -or $tail -match 'robocopy 换主文件退出码') { $copyStarted = $true }
    }
    if ($copyStarted) {
      $copySeen = $true
      if ($oldAlive) {
        throw "stage=swap-watch bat 在旧进程没死透时就开始拷文件：$oldDesc，update.log 已出现'正在换上新版本'——旧面板窗口没关就换文件必然撞锁（lucky 2026-11-20 定性）"
      }
      if ($ExpectInstallDirClear -and $installProcs) {
        $names = ($installProcs | ForEach-Object { "$($_.Name) PID $($_.ProcessId)（$($_.ExecutablePath)）" }) -join '；'
        throw "stage=swap-watch 拷文件开始时安装目录下还有进程：$names——孤儿进程还占着要换的文件"
      }
      Write-Host 'stage=swap-watch OK：拷文件开始时旧 exe 已死、安装目录下无残留进程'
      return
    }
    Start-Sleep -Milliseconds 500
  }
  if (-not $copySeen) {
    # 120 秒都没见拷开始：bat 可能卡在等退出或走了失败分支；
    # 这里不判死刑，交给 restart/result 阶段定性（旧进程退不掉
    # 会在 restart 报，换失败会在 result 报）。
    Write-Host '  swap-watch: 120 秒内没见到拷开始（bat 可能卡在等退出或已走失败分支），顺序断言让给 restart/result 阶段'
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

  # ---- 场景 A/B 的锁准备：ready 之后、apply 之前 ----
  if ($Scenario -eq 'Lock') {
    Measure-Stage 'lock' {
      Start-LockHolder -NodePath $LockNodePath
    }
  }
  if ($Scenario -eq 'Retry') {
    Measure-Stage 'lock' {
      # 换文件失败前的现场快照：config/data 哈希、缓存 zip 指纹、
      # 第一份 staging 位置（回滚后核对用）。
      $script:retryConfigHash = Get-PathHash (Join-Path $ExeDir 'config.yaml')
      $script:retryDataHash = Get-DataTreeHash (Join-Path $ExeDir 'data')
      $script:retryZipPath = $null
      $script:retryZipSha = $null
      $script:retryZipMtime = $null
      $script:retryStagingDir = $null
      # 缓存 zip 的指纹以 manifest 里的那份为准（temp zip 和缓存
      # zip 是两个文件，mtime 本来就不一样，不能拿 temp 的比缓存的）。
      $manifest0 = Join-Path $ExeDir 'data\update-cache\update-ready.json'
      if (Test-Path $manifest0) {
        try { $m0 = Get-Content $manifest0 -Raw -ErrorAction Stop | ConvertFrom-Json } catch { $m0 = $null }
        if ($m0 -and $m0.zip_path -and (Test-Path $m0.zip_path)) {
          $script:retryZipPath = $m0.zip_path
          $script:retryZipSha = (Get-FileHash -Path $m0.zip_path -Algorithm SHA256).Hash
          $script:retryZipMtime = (Get-Item $m0.zip_path).LastWriteTimeUtc.ToString('o')
        }
      }
      try {
        $st0 = Invoke-RestMethod -Uri "$base/api/update/status" -TimeoutSec 10
        $script:retryStagingDir = $st0.staging_dir
        if (-not $script:retryZipPath) { $script:retryZipPath = $st0.zip_path }
      } catch {
        # 状态查不到不挡锁准备；复用断言到时再看。
      }
      Write-Host "  lock-snapshot: config=$($script:retryConfigHash)，data=$($script:retryDataHash)，zip=$($script:retryZipPath)，staging=$($script:retryStagingDir)"
      Start-LockWatchdog -NodePath $LockNodePath
    }
  }

  # 第一遍 apply 的期望：正常/Lock 是新版 ok；Retry 第一遍是回滚
  # （旧版 failed/copy），第二遍才是新版 ok（见 apply2）。
  $script:expectRestartVersion = $ToVersion
  $script:expectVerdict = 'ok'
  if ($Scenario -eq 'Retry') {
    $script:expectRestartVersion = $FromVersion
    $script:expectVerdict = 'failed'
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

  # ---- 阶段 swap-watch：换文件期间旧进程必须已死透（最高优先级） ----
  # lucky 2026-11-20 定性：两个窗口冲突是 bug。不断言“最终起来了”
  # 就够了，要断言顺序——拷开始时旧 exe 已死、安装目录下无残留，
  # 而不是等到 restart 阶段才发现还活着。
  Measure-Stage 'swap-watch' {
    $oldPid = 0
    if ($proc) { $oldPid = $proc.Id }
    if ($Scenario -eq 'Retry') {
      # 第一遍：占位 node.exe 就是要一直占着，只断言旧 exe 已死，
      # 不断言安装目录清空（那是场景 B 要证的回滚前提）。
      Assert-SwapOrder -OldPid $oldPid
    } else {
      Assert-SwapOrder -OldPid $oldPid -ExpectInstallDirClear
    }
  }

  # ---- 阶段 restart：等旧进程退出、期望版本在 8686 上起来 ----
  # 正常/Lock：期望 v$ToVersion（新版）；Retry 第一遍：期望
  # v$FromVersion（回滚后的旧版），第二遍见 restart2。
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
        if ($newHealth.version -eq $script:expectRestartVersion) { break }
        $newHealth = $null
      } catch {
        # 还没起来，继续等。
      }
      Start-Sleep -Seconds 5
    }
    if (-not $newHealth -or $newHealth.version -ne $script:expectRestartVersion) {
      Show-UpdateLogTail
      Show-LogTail $logOut
      Show-LogTail $logErr
      throw "stage=restart 期望版本没起来：${RestartTimeoutSeconds} 秒内 http://127.0.0.1:$RestartPort/api/health 一直没报 v$($script:expectRestartVersion)"
    }
    $restartSeconds = ((Get-Date) - $applyAt).TotalSeconds
    Write-Host "stage=restart OK：期望版本 v$($newHealth.version) 已在 $RestartPort 上起来，从 apply 到可用共 $($restartSeconds.ToString('0.0')) 秒"
  }

  # ---- 场景 A 断言：锁被杀掉了、文件换成了 ----
  if ($Scenario -eq 'Lock') {
    Measure-Stage 'lock-assert' {
      # 1) 占位 PID 已死
      if ($script:holderPid) {
        $hp = Get-Process -Id $script:holderPid -ErrorAction SilentlyContinue
        if ($hp) {
          Show-LockDiagnostics
          throw "stage=lock-assert 占位 node.exe 还活着（PID $($script:holderPid)）：换文件步骤没杀掉它"
        }
      }
      # 2) 该文件可独占打开
      if (-not (Test-FileExclusiveOpen -Path $LockNodePath)) {
        Show-LockDiagnostics
        throw "stage=lock-assert $LockNodePath 还被占着（独占打开失败）"
      }
      # 3) update.log 有杀 node 记录（新版 bat 的 :procsweep 会写
      # “正在结束安装目录里的残留进程（浏览器运行时的 node.exe 等）...”；
      # 旧版 bat 没有这行——场景 A 要求 from 版本已带清场步骤，
      # 见文件头注释）。
      $logText = ''
      if (Test-Path $updateLog) { $logText = Get-Content $updateLog -Raw -ErrorAction SilentlyContinue }
      if ($logText -notmatch 'node') {
        Show-UpdateLogTail
        Show-LockDiagnostics
        throw 'stage=lock-assert update.log 里没有杀 node 的记录（期望见到 node.exe 相关行）'
      }
      # 4) robocopy 换 _internal 退出码 <8（主文件也一起查）
      $internalCodes = @(Select-String -Path $updateLog -Pattern 'robocopy 换 _internal 退出码 (\d+)' -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.Matches.Groups[1].Value })
      if (-not $internalCodes) {
        Show-UpdateLogTail
        throw 'stage=lock-assert update.log 里找不到“robocopy 换 _internal 退出码”行'
      }
      foreach ($rc in $internalCodes) {
        if ($rc -ge 8) { throw "stage=lock-assert robocopy 换 _internal 退出码 $rc（≥8=失败）" }
      }
      $mainCodes = @(Select-String -Path $updateLog -Pattern 'robocopy 换主文件退出码 (\d+)' -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.Matches.Groups[1].Value })
      foreach ($rc in $mainCodes) {
        if ($rc -ge 8) { throw "stage=lock-assert robocopy 换主文件退出码 $rc（≥8=失败）" }
      }
      Write-Host "stage=lock-assert OK：占位已死、node.exe 可独占打开、update.log 有杀 node 记录、robocopy _internal 退出码 $($internalCodes -join ',')（均 <8）"
    }
  }

  # ---- 阶段 result：换文件结果文件 verdict 为 ok，pending 已消费 ----
  # 注意顺序：这一步必须在 no-loop 之前，因为 force 检查会清掉
  # update-result.json（clear_update_result），先读结果再查更新。
  # Retry 第一遍期望 failed/copy（回滚），第二遍见 result2。
  Measure-Stage 'result' {
    $resultPath = Join-Path $ExeDir 'update-result.json'
    $pendingPath = Join-Path $ExeDir 'update-pending.json'
    if ($script:expectVerdict -eq 'failed') {
      # 回滚结果：bat 写 failed/copy，重开的旧版核对 pending 后会
      # 把 verdict 重写一遍（带中文 reason、不带 reason_code），
      # 所以只要求两者至少其一在场且对得上：reason 文案须是 copy
      # 对应的“换文件的时候出问题了”，reason_code 在场时须是 copy。
      $verdict = $null
      $deadline = (Get-Date).AddSeconds(60)
      while ((Get-Date) -lt $deadline) {
        if (Test-Path $resultPath) {
          try { $verdict = Get-Content $resultPath -Raw | ConvertFrom-Json } catch { $verdict = $null }
        }
        if ($verdict -and $verdict.status -eq 'failed' -and -not (Test-Path $pendingPath) -and ($null -ne $verdict.reason -or $null -ne $verdict.reason_code)) { break }
        $verdict = $null
        Start-Sleep -Seconds 2
      }
      if (-not $verdict -or $verdict.status -ne 'failed') {
        Show-UpdateLogTail
        Show-UpdateResult
        Show-LockDiagnostics
        $raw = if (Test-Path $resultPath) { Get-Content $resultPath -Raw } else { '(文件不存在)' }
        throw "stage=result 回滚结果不对：update-result.json 内容为 $raw（期望 status=failed）"
      }
      if ($verdict.expected_version -ne $ToVersion -or $verdict.actual_version -ne $FromVersion) {
        throw "stage=result 回滚结果里的版本不对：expected=$($verdict.expected_version)，actual=$($verdict.actual_version)，期望 expected=v$ToVersion、actual=v$FromVersion"
      }
      if ($verdict.reason -and $verdict.reason -notmatch '换文件的时候出问题了') {
        throw "stage=result 回滚原因不对：reason=$($verdict.reason)（期望 copy 对应的'换文件的时候出问题了'）"
      }
      if ($null -ne $verdict.reason_code -and $verdict.reason_code -ne 'copy') {
        throw "stage=result 回滚原因码不对：reason_code=$($verdict.reason_code)，期望 copy"
      }
      Write-Host 'stage=result OK：回滚 verdict 为 failed/copy，update-pending.json 已消费'
    } else {
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
        if ($Scenario -ne 'Main') { Show-LockDiagnostics }
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
  }

  # ---- 场景 B 第二段：失败后可重试，且不用重新下载 ----
  if ($Scenario -eq 'Retry') {
    Measure-Stage 'retry-check' {
      # 1) 旧版还在
      try {
        $health = Invoke-RestMethod -Uri "$restartBase/api/health" -TimeoutSec 10
      } catch {
        throw "stage=retry-check 回滚后旧版 health 查不到：$($_.Exception.Message)"
      }
      if ($health.version -ne $FromVersion) {
        throw "stage=retry-check 回滚后版本不对：面板报 v$($health.version)，期望仍是旧版 v$FromVersion"
      }
      # 2) config.yaml / data 哈希不变（data 排除 .db/.log 等运行时文件，见 Get-DataTreeHash）
      $cfgHash2 = Get-PathHash (Join-Path $ExeDir 'config.yaml')
      if ($cfgHash2 -ne $script:retryConfigHash) {
        throw "stage=retry-check config.yaml 被改动了：换文件前 $($script:retryConfigHash)，回滚后 $cfgHash2"
      }
      $dataHash2 = Get-DataTreeHash (Join-Path $ExeDir 'data')
      if ($dataHash2 -ne $script:retryDataHash) {
        throw 'stage=retry-check data/ 被改动了：换文件失败不应动用户数据'
      }
      # 3) 手动重查仍报有新版（失败记录不得把手动重查也挡住）
      try {
        $check = Invoke-RestMethod -Uri "$restartBase/api/update/check?force=1" -TimeoutSec 30
      } catch {
        throw "stage=retry-check 手动重查失败：$($_.Exception.Message)"
      }
      if ($check.update_available -ne $true -or $check.version -ne $ToVersion) {
        throw "stage=retry-check 手动重查没报新版：update_available=$($check.update_available)，version=$($check.version)，期望 update_available=true、version=v$ToVersion"
      }
      Write-Host 'stage=retry-check OK：旧版还在、config/data 未动、手动重查仍报有新版'
    }

    Measure-Stage 'reuse' {
      # 包复用：以实际落地的缓存格式为准——
      # data/update-cache/update-ready.json（version/sha256/size/
      # zip_path/downloaded_at）+ jianli-lieshou-<version>.zip；
      # 状态接口还多报 download_cached / cached_version 两个字段。
      # 先看跑着的旧版带不带缓存代码（download_cached 字段在不在）：
      # 不带=from 版本 < 1.1.10，复用测不了，明确 SKIP、走完整重下
      # 重试（失败重试本身仍验证）；带但 manifest 不见=真 bug，硬失败。
      $manifestPath = Join-Path $ExeDir 'data\update-cache\update-ready.json'
      $script:reuseAvailable = $false
      try {
        $probe = Invoke-RestMethod -Uri "$restartBase/api/update/status" -TimeoutSec 10
      } catch {
        throw "stage=reuse 回滚后旧版 status 查不到：$($_.Exception.Message)"
      }
      $cacheSupported = ($probe.PSObject.Properties.Name -contains 'download_cached')
      if (-not $cacheSupported) {
        Write-Host 'stage=reuse SKIP：旧版不带 download_cached 字段（from 版本 < 1.1.10），包复用测不了，走完整重下重试'
      } elseif (-not (Test-Path $manifestPath)) {
        throw 'stage=reuse 旧版带缓存代码，但 data\update-cache\update-ready.json 不存在：下载时没落盘？'
      } else {
        try { $manifest = Get-Content $manifestPath -Raw -ErrorAction Stop | ConvertFrom-Json } catch { $manifest = $null }
        if (-not $manifest -or $manifest.version -ne $ToVersion) {
          throw "stage=reuse manifest 版本不对：version=$($manifest.version)，期望 v$ToVersion"
        }
        $zipPath2 = $manifest.zip_path
        if (-not $zipPath2 -or -not (Test-Path $zipPath2)) {
          throw "stage=reuse manifest 里的 zip 找不到：zip_path=$zipPath2"
        }
        $sha2 = (Get-FileHash -Path $zipPath2 -Algorithm SHA256).Hash
        $mtime2 = (Get-Item $zipPath2).LastWriteTimeUtc.ToString('o')
        if ($script:retryZipSha -and $sha2 -ne $script:retryZipSha) {
          throw "stage=reuse 缓存 zip 变了：换文件前 sha256 $($script:retryZipSha)，现在 $sha2"
        }
        if ($script:retryZipMtime -and $mtime2 -ne $script:retryZipMtime) {
          throw "stage=reuse 缓存 zip 被重写了：换文件前 mtime $($script:retryZipMtime)，现在 $mtime2"
        }
        # 第一份 staging 还在（失败那路 bat 不删 staging）：在就要求
        # 内容完整；整目录没了也不判死刑——复用的真正依据是缓存 zip，
        # 临时 staging 只是附带证据，未来 bat 改成失败也清 staging
        # 时这里不应误报。
        if ($script:retryStagingDir -and (Test-Path $script:retryStagingDir)) {
          if (-not (Test-Path (Join-Path $script:retryStagingDir '简历猎手.exe')) -or -not (Test-Path (Join-Path $script:retryStagingDir '_internal'))) {
            throw "stage=reuse 第一份 staging 不完整：$($script:retryStagingDir) 里缺 exe 或 _internal"
          }
        } else {
          Write-Host "  reuse: 第一份 staging 目录已不在（$($script:retryStagingDir)），以缓存 zip 为准，不挡重试"
        }
        if ($probe.status -ne 'ready') {
          throw "stage=reuse 重开后状态不是 ready：status=$($probe.status)，error=$($probe.error)（期望缓存恢复出 ready）"
        }
        if ($probe.download_cached -ne $true -or $probe.cached_version -ne $ToVersion) {
          throw "stage=reuse status 没报缓存：download_cached=$($probe.download_cached)，cached_version=$($probe.cached_version)，期望 true/v$ToVersion"
        }
        $t0 = Get-Date
        $started = Invoke-RestMethod -Uri "$restartBase/api/update/start" -Method Post -TimeoutSec 30
        $elapsed = ((Get-Date) - $t0).TotalSeconds
        if ($started.status -ne 'ready') {
          throw "stage=reuse start 后状态不是 ready：status=$($started.status)，error=$($started.error)"
        }
        if ($elapsed -ge 5) {
          throw "stage=reuse start 秒回失败：用了 $($elapsed.ToString('0.0')) 秒（67MB 重下要几十秒，≥5 秒说明走了重下）"
        }
        $script:reuseAvailable = $true
        Write-Host "stage=reuse OK：缓存 zip 指纹未变、status 直接 ready、start $($elapsed.ToString('0.0')) 秒回 ready"
      }
      if (-not $script:reuseAvailable) {
        # 完整重下重试：先 check 再 start，等 ready（与主流程同款等待）。
        try {
          $check = Invoke-RestMethod -Uri "$restartBase/api/update/check?force=1" -TimeoutSec 30
        } catch {
          throw "stage=reuse 重下前检查失败：$($_.Exception.Message)"
        }
        if ($check.update_available -ne $true) {
          throw "stage=reuse 重下前检查没报新版：update_available=$($check.update_available)"
        }
        try {
          $started = Invoke-RestMethod -Uri "$restartBase/api/update/start" -Method Post -TimeoutSec 30
        } catch {
          throw "stage=reuse 重下开始失败：$($_.Exception.Message)"
        }
        if ($started.status -notin @('downloading', 'verifying', 'ready')) {
          throw "stage=reuse 重下开始后状态不对：status=$($started.status)，error=$($started.error)"
        }
        $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
        $lastStatus = $null
        while ((Get-Date) -lt $deadline) {
          try {
            $lastStatus = Invoke-RestMethod -Uri "$restartBase/api/update/status" -TimeoutSec 10
          } catch {
            throw "stage=reuse 重下状态查不到：$($_.Exception.Message)"
          }
          if ($lastStatus.status -eq 'ready') { break }
          if ($lastStatus.status -eq 'error') {
            throw "stage=reuse 重下/校验失败：$($lastStatus.error)"
          }
          Start-Sleep -Seconds 10
        }
        if (-not $lastStatus -or $lastStatus.status -ne 'ready') {
          throw "stage=reuse 重下超时：${ReadyTimeoutSeconds} 秒内状态一直没到 ready（最后 status=$($lastStatus.status)）"
        }
        Write-Host 'stage=reuse OK（fallback）：完整重下后 ready，可重试'
      }
    }

    Measure-Stage 'apply2' {
      # 跳过 start-download/ready 直调 apply（reuse 已保证 ready；
      # fallback 也已等到 ready）。
      try {
        $applied2 = Invoke-RestMethod -Uri "$restartBase/api/update/apply" -Method Post -TimeoutSec 30
      } catch {
        throw "stage=apply2 第二次换文件失败：/api/update/apply 请求失败：$($_.Exception.Message)"
      }
      if ($applied2.applying -ne $true) {
        throw "stage=apply2 第二次换文件没开始：返回 applying=$($applied2.applying)，error=$($applied2.error)"
      }
      $script:applyAt = Get-Date
      Write-Host 'stage=apply2 OK：已开始第二次换文件'
    }

    Measure-Stage 'swap-watch2' {
      # 第二遍也要断言顺序：看门狗早已停掉占位，拷开始时安装目录
      # 下应已无进程。旧 exe 是 bat 重拉的那一个，从 8686 监听者取 PID。
      $oldPid2 = 0
      $conn = Get-NetTCPConnection -LocalPort $RestartPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($conn) { $oldPid2 = $conn.OwningProcess }
      Assert-SwapOrder -OldPid $oldPid2 -ExpectInstallDirClear
    }

    Measure-Stage 'restart2' {
      $applyAt = $script:applyAt
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
        Show-UpdateResult
        Show-LockDiagnostics
        throw "stage=restart2 第二次换文件后新版没起来：${RestartTimeoutSeconds} 秒内 http://127.0.0.1:$RestartPort/api/health 一直没报 v$ToVersion"
      }
      Write-Host "stage=restart2 OK：重试后新版已在 $RestartPort 上起来，版本 v$($newHealth.version)"
    }

    Measure-Stage 'result2' {
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
        Show-LockDiagnostics
        $raw = if (Test-Path $resultPath) { Get-Content $resultPath -Raw } else { '(文件不存在)' }
        throw "stage=result2 第二次换文件结果不对：update-result.json 内容为 $raw"
      }
      if ($verdict.expected_version -ne $ToVersion -or $verdict.actual_version -ne $ToVersion) {
        throw "stage=result2 第二次换文件结果里的版本不对：expected=$($verdict.expected_version)，actual=$($verdict.actual_version)，期望 v$ToVersion"
      }
      if (Test-Path $pendingPath) {
        throw 'stage=result2 pending 标记没被消费：update-pending.json 还在'
      }
      Write-Host 'stage=result2 OK：第二次换文件 update-result.json 为 ok，update-pending.json 已消费'
    }
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
  if ($Scenario -ne 'Main') {
    Show-LockDiagnostics
  }
  Stop-LockHolder
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
  Stop-LockHolder
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
Write-Host "UPDATEFLOW_OK scenario=$Scenario from=$FromVersion to=$ToVersion"
exit 0
