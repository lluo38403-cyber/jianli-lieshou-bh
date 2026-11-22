# 简历猎手安装程序冒烟测试（GitHub Actions 专用，v39）
#
# 用法：
#   pwsh scripts/ci/smoke-installer.ps1 -InstallerExe <安装程序 exe> -ExpectedVersion <如 1.1.25> [-WorkDir <工作目录>]
#
# 流程：静默安装到临时目录 → 核对 exe 能跑 → 核对注册表卸载项与
# 快捷方式 → 静默卸载 → 核对安装目录与注册表项都干净。
# 不启动面板、不登录任何真实平台、不写用户数据（装完不运行 exe，
# 所以 config.yaml / data/ 都不会产生；默认保留用户数据的卸载也必须
# 把安装目录整个清掉，这一点本脚本直接断言）。
#
# 每个阶段失败都打印 "SMOKE_FAIL stage=<阶段> <原因>" 并以 exit 1 结束；
# 全部通过打印 "SMOKE_OK" 并以 exit 0 结束。

param(
  [Parameter(Mandatory = $true)][string]$InstallerExe,
  [Parameter(Mandatory = $true)][string]$ExpectedVersion,
  [string]$WorkDir = ""
)
$ErrorActionPreference = 'Stop'

$tempDir = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$workDir = if ($WorkDir) { $WorkDir } else { Join-Path $tempDir 'smoke-installer' }
$installDir = Join-Path $workDir 'zhaopinglieshou'
$exe = Join-Path $installDir '简历猎手.exe'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\简历猎手_is1'
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '简历猎手.lnk'
$startMenuLnk = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'Microsoft\Windows\Start Menu\Programs\简历猎手\简历猎手.lnk'
$installed = $false

try {
  if (-not (Test-Path $InstallerExe)) { throw "stage=package 找不到安装程序：$InstallerExe" }
  Remove-Item $workDir -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $workDir | Out-Null

  # ---- 阶段 install：静默安装 ----
  $proc = Start-Process -FilePath $InstallerExe -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$installDir") -Wait -PassThru
  if ($proc.ExitCode -ne 0) { throw "stage=install 静默安装失败，退出码 $($proc.ExitCode)" }
  if (-not (Test-Path $exe)) { throw 'stage=install 装完了但找不到 简历猎手.exe' }
  $installed = $true
  Write-Host 'stage=install OK：静默安装完成'

  # ---- 阶段 version：exe 能跑 --version 且退出码为 0 ----
  # 注意：exe 是无窗口程序（console=False），--version 的输出不会回到
  # 控制台，这里只断言“能跑、退出码 0”，不拿输出文本比对版本号；
  # 版本号本身由注册表 DisplayVersion（iss AppVersion）与 build job 里
  # 对打包前 exe 的 --version 步骤共同保证，面板版本号由 smoke-windows
  # 对同一份产物跑 /api/version 核对。
  $versionOut = & $exe --version 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0) { throw "stage=version exe --version 退出码 $LASTEXITCODE" }
  Write-Host 'stage=version OK：exe 能跑 --version，退出码 0'

  # ---- 阶段 registry：HKCU 卸载项齐不齐 ----
  if (-not (Test-Path $uninstallKey)) { throw 'stage=registry 注册表里没有 HKCU 卸载项（简历猎手_is1）' }
  $reg = Get-ItemProperty $uninstallKey
  foreach ($field in @('DisplayName', 'DisplayVersion', 'InstallLocation', 'UninstallString', 'Publisher', 'DisplayIcon')) {
    if (-not $reg.$field) { throw "stage=registry 卸载项缺 $field" }
  }
  if ($reg.DisplayVersion -ne $ExpectedVersion) { throw "stage=registry DisplayVersion 是 $($reg.DisplayVersion)，不是 $ExpectedVersion" }
  if ($reg.InstallLocation -ne $installDir) { throw "stage=registry InstallLocation 是 $($reg.InstallLocation)，不是 $installDir" }
  Write-Host 'stage=registry OK：卸载项六个值齐全，版本与路径都对'

  # ---- 阶段 shortcuts：开始菜单与桌面快捷方式 ----
  if (-not (Test-Path $desktopLnk)) { throw 'stage=shortcuts 桌面上没有 简历猎手.lnk' }
  if (-not (Test-Path $startMenuLnk)) { throw 'stage=shortcuts 开始菜单里没有 简历猎手.lnk' }
  Write-Host 'stage=shortcuts OK：桌面与开始菜单快捷方式都在'

  # ---- 阶段 uninstall：静默卸载后干不干净 ----
  $uninstaller = Join-Path $installDir 'unins000.exe'
  if (-not (Test-Path $uninstaller)) { throw 'stage=uninstall 找不到 unins000.exe' }
  $proc = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
  if ($proc.ExitCode -ne 0) { throw "stage=uninstall 静默卸载失败，退出码 $($proc.ExitCode)" }
  if (Test-Path $installDir) { throw "stage=uninstall 卸载后安装目录还在：$installDir" }
  if (Test-Path $uninstallKey) { throw 'stage=uninstall 卸载后注册表卸载项还在' }
  if (Test-Path $desktopLnk) { throw 'stage=uninstall 卸载后桌面快捷方式还在' }
  Write-Host 'stage=uninstall OK：安装目录、注册表项、桌面快捷方式都干净了'

  Write-Host 'SMOKE_OK'
  exit 0
} catch {
  Write-Host "SMOKE_FAIL $($_.Exception.Message)"
  if ($installed -and (Test-Path (Join-Path $installDir 'unins000.exe'))) {
    Write-Host '收尾：尝试静默卸载残留的安装…'
    Start-Process -FilePath (Join-Path $installDir 'unins000.exe') -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -ErrorAction SilentlyContinue | Out-Null
  }
  exit 1
}
