# 简历猎手 Windows 冒烟测试（GitHub Actions 专用）
#
# 用法：
#   pwsh scripts/ci/smoke-windows.ps1 -ExeDir <解压后的产物目录> -ExpectedVersion <如 1.0.6> [-Port 18686] [-TimeoutSeconds 150]
#
# 只做浅测：不登录任何真实招聘平台、不投递真实简历、不填 AI Key、
# 不用真机器码薅试用（启动时设 JIANLI_NO_AUTH_SYNC=1，跳过中转授权同步）、
# 不触发自动更新换文件。试用倒计时只看界面上有没有正常显示，不等它走完。
#
# 每个阶段失败都打印 "SMOKE_FAIL stage=<阶段> <原因>" 并以 exit 1 结束，
# 让整个 CI 失败；阶段名能直接看出是打包坏了、起不来还是面板打不开。
# 全部通过打印 "SMOKE_OK" 并以 exit 0 结束。

param(
  [Parameter(Mandatory = $true)][string]$ExeDir,
  [Parameter(Mandatory = $true)][string]$ExpectedVersion,
  [int]$Port = 18686,
  [int]$TimeoutSeconds = 150
)
$ErrorActionPreference = 'Stop'

$tempDir = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$logOut = Join-Path $tempDir 'smoke-exe-out.log'
$logErr = Join-Path $tempDir 'smoke-exe-err.log'

function Show-LogTail {
  param([string]$Path)
  if (Test-Path $Path) {
    Get-Content $Path -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  exe-log: $_" }
  }
}

$proc = $null
try {
  # ---- 阶段 package：产物齐不齐（打包坏了） ----
  $exe = Join-Path $ExeDir '简历猎手.exe'
  if (-not (Test-Path $exe)) { throw 'stage=package 打包坏了：找不到 简历猎手.exe' }
  $indexHtml = Get-ChildItem -Path $ExeDir -Recurse -Filter 'index.html' -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $indexHtml) { throw 'stage=package 打包坏了：产物里没有面板首页 index.html' }
  Write-Host "stage=package OK：exe 与面板首页都在"

  # ---- 阶段 port：端口有没有被占 ----
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $connect = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
    $connected = $connect.AsyncWaitHandle.WaitOne(2000)
    if ($connected -and $client.Connected) { throw "stage=port 端口 $Port 已被占用，换台干净机器或等一会儿再跑" }
  } catch {
    if ($_.Exception.Message -like 'stage=port*') { throw $_ }
    # 连不上就是空闲，继续。
  } finally {
    $client.Close()
  }
  Write-Host "stage=port OK：$Port 空闲"

  # ---- 阶段 start：exe 能不能起来 ----
  # JIANLI_NO_AUTH_SYNC=1：跳过启动时的中转授权同步，不拿这台临时机器的机器码去薅试用。
  $env:JIANLI_NO_AUTH_SYNC = '1'
  Remove-Item $logOut, $logErr -ErrorAction SilentlyContinue
  $proc = Start-Process -FilePath $exe -ArgumentList 'web', '--port', "$Port", '--no-open' `
    -WorkingDirectory $ExeDir -RedirectStandardOutput $logOut -RedirectStandardError $logErr `
    -WindowStyle Hidden -PassThru
  Start-Sleep -Seconds 5
  if ($proc.HasExited) {
    Show-LogTail $logOut
    Show-LogTail $logErr
    throw "stage=start exe 起不来：启动 5 秒就退出了（exit $($proc.ExitCode)）"
  }
  Write-Host "stage=start OK：exe 已启动（PID $($proc.Id)）"

  # ---- 阶段 panel：面板能不能打开 ----
  $base = "http://127.0.0.1:$Port"
  $homePage = $null
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
      Show-LogTail $logOut
      Show-LogTail $logErr
      throw "stage=start exe 起不来：等面板时进程退出了（exit $($proc.ExitCode)）"
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
    throw "stage=panel 面板打不开：${TimeoutSeconds} 秒内 http://127.0.0.1:$Port/ 一直没返回 200"
  }
  Write-Host 'stage=panel OK：面板返回 200'

  # ---- 阶段 home：首页标题对不对 ----
  if ($homePage.Content -notmatch '简历猎手') { throw 'stage=home 首页不对：返回的页面里没有“简历猎手”' }
  Write-Host 'stage=home OK：首页标题正常'

  # ---- 阶段 version：面板报的版本号和当前版本一致 ----
  try {
    $health = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 10
  } catch {
    throw "stage=version 版本查不到：/api/health 请求失败：$($_.Exception.Message)"
  }
  if ($health.version -ne $ExpectedVersion) {
    throw "stage=version 版本号对不上：面板报 v$($health.version)，期望 v$ExpectedVersion"
  }
  Write-Host "stage=version OK：面板版本 v$($health.version) 与期望一致"

  # ---- 阶段 update：更新状态接口正常，且刚启动不会误报更新失败 ----
  # 只做只读检查：/api/update/status 要报对当前版本；
  # /api/update/check?force=1 再怎么样也不能 500（中转连不上时也要
  # 静默返回“没有新版”，绝不能把面板拖垮）。不真下载、不真换文件。
  try {
    $updateStatus = Invoke-RestMethod -Uri "$base/api/update/status" -TimeoutSec 10
  } catch {
    throw "stage=update 更新状态查不到：/api/update/status 请求失败：$($_.Exception.Message)"
  }
  if ($updateStatus.current_version -ne $ExpectedVersion) {
    throw "stage=update 更新状态里的版本号对不上：报 v$($updateStatus.current_version)，期望 v$ExpectedVersion"
  }
  try {
    $check = Invoke-WebRequest -Uri "$base/api/update/check?force=1" -UseBasicParsing -TimeoutSec 15
    if ($check.StatusCode -ne 200) { throw "stage=update 更新检查异常：/api/update/check 返回 $($check.StatusCode)" }
  } catch {
    if ($_.Exception.Message -like 'stage=update*') { throw $_ }
    throw "stage=update 更新检查异常：/api/update/check 请求失败：$($_.Exception.Message)"
  }
  Write-Host 'stage=update OK：更新状态正常，检查接口不报错'

  # ---- 阶段 authbar：顶部授权栏打进包里没有 ----
  # 授权栏是前端组件，面板本身不跑浏览器时看不到渲染；
  # 这里查打包好的 JS 里有没有授权栏的文案，确认组件确实在包里。
  $jsFiles = Get-ChildItem -Path $ExeDir -Recurse -Filter '*.js' -ErrorAction SilentlyContinue
  $found = $false
  foreach ($f in $jsFiles) {
    if (Select-String -Path $f.FullName -Pattern '输入卡密' -SimpleMatch -Quiet) { $found = $true; break }
  }
  if (-not $found) { throw 'stage=authbar 授权栏没打进包里：打包好的 JS 里找不到授权栏文案' }
  Write-Host 'stage=authbar OK：授权栏组件在包里'

  # ---- 阶段 pages：主要页面数据接口通不通 ----
  foreach ($path in @('/api/workbench', '/api/config', '/api/activity')) {
    try {
      $r = Invoke-WebRequest -Uri "$base$path" -UseBasicParsing -TimeoutSec 10
      if ($r.StatusCode -ne 200) { throw "stage=pages 主要页面打不开：$path 返回 $($r.StatusCode)" }
    } catch {
      if ($_.Exception.Message -like 'stage=pages*') { throw $_ }
      throw "stage=pages 主要页面打不开：$path 请求失败：$($_.Exception.Message)"
    }
  }
  Write-Host 'stage=pages OK：首页/设置/动态接口都通'

  # ---- 阶段 pdf：PDF 简历上传（v28） ----
  # 只用 Python 标准库现场生成两份测试 PDF，不引新依赖：
  # 一份带文字（应上传成功），一份空白页无文字层（应报扫描件错）。
  # 生成器写进临时 .py 再跑，跑完就删；CI 上的 python 就是装包的那个。
  $pdfGen = Join-Path $tempDir 'smoke-make-pdf.py'
  $pdfText = Join-Path $tempDir 'smoke-resume.pdf'
  $pdfBlank = Join-Path $tempDir 'smoke-blank.pdf'
  @'
def build_pdf(text):
    esc = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    stream = 'BT /F1 24 Tf 72 720 Td (' + esc + ') Tj ET' if text else ''
    sb = stream.encode('latin-1')
    objs = []
    objs.append(b'<< /Type /Catalog /Pages 2 0 R >>')
    objs.append(b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>')
    objs.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>')
    objs.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    objs.append(b'<< /Length ' + str(len(sb)).encode() + b' >>\nstream\n' + sb + b'\nendstream')
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += ('%d 0 obj\n' % i).encode() + obj + b'\nendobj\n'
    xref_pos = len(out)
    out += ('xref\n0 %d\n' % (len(objs) + 1)).encode()
    out += b'0000000000 65535 f \n'
    for off in offsets:
        out += ('%010d 00000 n \n' % off).encode()
    out += ('trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, xref_pos)).encode()
    return bytes(out)

import sys
open(sys.argv[1], 'wb').write(build_pdf('Jane Doe - Senior Engineer'))
open(sys.argv[2], 'wb').write(build_pdf(''))
'@ | Set-Content -Path $pdfGen -Encoding utf8
  python $pdfGen $pdfText $pdfBlank
  if (-not (Test-Path $pdfText) -or -not (Test-Path $pdfBlank)) { throw 'stage=pdf 测试 PDF 没生成出来' }
  try {
    $upload = Invoke-WebRequest -Uri "$base/api/resume/upload" -Method Post -Form @{ file = Get-Item $pdfText } -TimeoutSec 15
    $uploadJson = $upload.Content | ConvertFrom-Json
    if (-not $uploadJson.success) { throw 'stage=pdf 带文字的 PDF 上传没成功' }
  } catch {
    if ($_.Exception.Message -like 'stage=pdf*') { throw $_ }
    throw "stage=pdf 带文字的 PDF 上传失败：$($_.Exception.Message)"
  }
  Write-Host 'stage=pdf OK：带文字的 PDF 上传成功'
  try {
    $blankUpload = Invoke-WebRequest -Uri "$base/api/resume/upload" -Method Post -Form @{ file = Get-Item $pdfBlank } -TimeoutSec 15
    throw 'stage=pdf 空白 PDF 应该报扫描件错，却上传成功了'
  } catch {
    if ($_.Exception.Message -like 'stage=pdf*') { throw $_ }
    $body = $_.ErrorDetails.Message
    if (-not $body -or $body -notmatch '扫描版') { throw "stage=pdf 空白 PDF 没报扫描件错：$body" }
  }
  Write-Host 'stage=pdf OK：空白 PDF 报了扫描件错'
  Remove-Item $pdfGen, $pdfText, $pdfBlank -ErrorAction SilentlyContinue
} catch {
  Write-Host "SMOKE_FAIL $_"
  Show-LogTail $logOut
  Show-LogTail $logErr
  if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  }
  exit 1
}

# 正常收尾：关掉 exe，确认它退干净了。
if ($proc -and -not $proc.HasExited) {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  if (-not $proc.WaitForExit(10000)) {
    Write-Host 'SMOKE_FAIL stage=stop exe 关不掉：Stop-Process 后 10 秒还在跑'
    exit 1
  }
}
Write-Host "SMOKE_OK version=$ExpectedVersion port=$Port"
exit 0
