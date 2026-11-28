; 简历猎手 · Inno Setup 6 安装 / 卸载脚本（v47，1.1.33）。
;
; 口径唯一来源是 src/bosshunter/installer.py：默认路径、卸载清理清单、
; 安全谓词、停进程判定都必须与那个模块逐项一致，对应关系由
; scripts/check-installer-consistency.py 核对（verify.sh 会跑它）。
;
; 编译（Windows，CI 里由 build-exe.yml 跑）：
;   choco install innosetup -y
;   iscc /DMyAppVersion=1.1.33 installer\jianli-lieshou.iss
; 不传 /DMyAppVersion 时用下面 #define 的默认值（发版时记得与
; pyproject.toml 的 version 保持一致，verify.sh 不校验这一项，
; CI 里读 pyproject.toml 传值才是准的）。
; 产物：dist-installer\简历猎手-安装程序-1.1.33.exe。
; 编译前必须先跑 pyinstaller（dist\简历猎手\ 要有内容），[Files]
; 直接把 dist 产物原样打进安装包。
;
; 关键约定：
; - DefaultDirName={src}\简历猎手：默认装到安装包所在目录下的
;   简历猎手子文件夹；用户在向导里改目录后按所选路径安装，
;   不再强行追加一层子文件夹（installer.py resolve_target_dir 同款规则）。
; - PrivilegesRequired=lowest：不强制管理员权限，普通权限就能装到
;   自己选的目录；卸载注册表项因此写在 HKCU 下
;   （Software\Microsoft\Windows\CurrentVersion\Uninstall\简历猎手_is1，
;   Inno 按 AppId 自动写 DisplayName / DisplayVersion /
;   InstallLocation / UninstallString / Publisher / DisplayIcon）。
; - 桌面快捷方式名「简历猎手.lnk」与 updater._DESKTOP_SHORTCUT_NAME
;   同名；应用第一次启动时 ensure_desktop_shortcut 也会建同一个，
;   但安装时用户不勾选桌面快捷方式时，安装程序写
;   data\desktop_shortcut_declined marker，程序启动时见到它就不再
;   补建（v41，否则向导里的勾选框等于没生效）；开始菜单快捷方式
;   程序从不自建，只有安装程序建。
; - 默认 config.yaml 不是安装包里直接带的文件：dist 里只有
;   _internal\config.example.yaml，launcher 首次启动时复制为
;   安装目录下的 config.yaml（原有行为不变）。
; - 卸载不静默删用户数据：默认保留 config.yaml、
;   data\bosshunter.db、data\resumes\，卸载时问一次（用户选“是”才删）。
;   注意：Inno 的卸载程序里建不了自定义向导页（CreateInputOptionPage
;   等 Create*Page 函数在卸载程序里被禁，调了卸载直接退 1，见
;   InitializeUninstall 的注释），所以改问 SuppressibleMsgBox 对话框。

#ifndef MyAppVersion
  #define MyAppVersion "1.1.40"
#endif

[Setup]
AppName=简历猎手
AppVersion={#MyAppVersion}
AppVerName=简历猎手 {#MyAppVersion}
AppId=简历猎手
AppPublisher=聚梦Muse AI
AppPublisherURL=https://www.16688.com.cn/shop/S542647
DefaultDirName={src}\简历猎手
DefaultGroupName=简历猎手
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=简历猎手-安装程序-{#MyAppVersion}
SetupIconFile=..\logo.ico
UninstallDisplayIcon={app}\简历猎手.exe
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; 安装包本身不写注册表启动项、不装服务；卸载注册表项由 Inno 自动写。

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
; dist\简历猎手\ 是 PyInstaller onedir 产物：简历猎手.exe + _internal。
Source: "..\dist\简历猎手\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 快捷方式是否创建由“准备安装”页的两个勾选框决定（默认都勾选）：
; 桌面入口认 ShouldCreateDesktopShortcut，开始菜单两个入口认
; ShouldCreateStartMenuShortcut。开始菜单文件夹选择页已用
; DisableProgramGroupPage=yes 删掉，组名固定用 DefaultGroupName。
Name: "{group}\简历猎手"; Filename: "{app}\简历猎手.exe"; WorkingDir: "{app}"; Check: ShouldCreateStartMenuShortcut
Name: "{group}\卸载简历猎手"; Filename: "{uninstallexe}"; Check: ShouldCreateStartMenuShortcut
Name: "{userdesktop}\简历猎手"; Filename: "{app}\简历猎手.exe"; WorkingDir: "{app}"; Check: ShouldCreateDesktopShortcut

[Run]
Filename: "{app}\简历猎手.exe"; Description: "启动简历猎手"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 安装目录内的运行产物：一律删（与用户数据保留选项无关）。
; 对应 installer.py _RUNTIME_RELATIVE + update-backup-* + .star_hint_*。
Type: filesandordirs; Name: "{app}\data\logs"
Type: filesandordirs; Name: "{app}\data\update-cache"
Type: files; Name: "{app}\data\.instance.json"
Type: files; Name: "{app}\data\launched-browser.json"
Type: files; Name: "{app}\data\window-geometry.json"
Type: files; Name: "{app}\data\.startup-failed.json"
Type: files; Name: "{app}\data\desktop_shortcut_created"
Type: files; Name: "{app}\data\desktop_shortcut_declined"
Type: files; Name: "{app}\data\auth_cache.json"
Type: files; Name: "{app}\data\cities.cache.json"
Type: files; Name: "{app}\data\.star_hint_*"
Type: files; Name: "{app}\update.bat"
Type: files; Name: "{app}\update.log"
Type: files; Name: "{app}\update-pending.json"
Type: files; Name: "{app}\update-result.json"
Type: filesandordirs; Name: "{app}\update-backup-*"
; 用户数据：只在卸载时用户确认“同时删除用户数据”时删，默认保留。
; 对应 installer.py USER_DATA_RELATIVE。
Type: files; Name: "{app}\config.yaml"; Check: DeleteUserData
Type: files; Name: "{app}\data\bosshunter.db"; Check: DeleteUserData
Type: files; Name: "{app}\data\bosshunter.db-wal"; Check: DeleteUserData
Type: files; Name: "{app}\data\bosshunter.db-shm"; Check: DeleteUserData
Type: filesandordirs; Name: "{app}\data\resumes"; Check: DeleteUserData
; 空了才删的目录：data/ 只在删完里面文件后是空的才删——用户数据被
; 删掉或从未产生时（冒烟测试从不运行 exe，data/ 里只有安装时写的
; 快捷方式 marker）；保留用户数据时 data/ 里还有 db 与 resumes，
; 非空就不删，目录按原设计留下来。Inno 不会自动清掉变空的子目录
; （只自动移除空的 {app} 本身），不加这一行，空 data/ 会让安装
; 目录删不干净（流水线 #22 冒烟教训）。对应 installer.py
; REMOVE_IF_EMPTY_RELATIVE；绝不要改成 filesandordirs 删整个 data/，
; 那会在用户选“保留用户数据”时也删掉 db 与 resumes。
Type: dirifempty; Name: "{app}\data"
; 安装目录外：工作浏览器配置目录整个删（登录态在里面，卸载即清）。
; 对应 installer.py browser_profile_dir。注意：Inno 没有 {userprofile}
; 常量（写了会在编译时报 Unknown constant），这里用 [Code] 段的
; GetUserProfile 脚本常量取 %USERPROFILE%。
Type: filesandordirs; Name: "{code:GetUserProfile}\.jianli-lieshou"
; %TEMP% 里属于本程序的残留（只认这三个前缀，别家文件不动）。
; 对应 installer.py TEMP_PREFIXES。注意：Inno 的 {tmp} 是安装程序自己
; 的临时子目录，不是系统 %TEMP%，这里必须用环境变量常量 {%TEMP}。
Type: files; Name: "{%TEMP}\bosshunter_resume.html"
Type: files; Name: "{%TEMP}\bosshunter_resume_*.html"
Type: files; Name: "{%TEMP}\bosshunter-browser-*.lock"
Type: filesandordirs; Name: "{%TEMP}\jianli-update-*"
; 注意：{app} 本身由 Inno 在文件删完、目录为空时自动移除（data/ 这
; 类子目录不会自动清，上面那行 dirifempty 负责）；保留用户数据时
; {app}\config.yaml 与 data\ 会留下来，这是故意的。
; %AppData% / %LocalAppData% 下没有我方写的东西，卸载清单里不碰。

[Code]
var
  DeleteUserDataFlag: Boolean;
  UninstallLogPath: String;
  DesktopShortcutCheckBox: TNewCheckBox;
  StartMenuShortcutCheckBox: TNewCheckBox;
  CreateDesktopShortcutFlag: Boolean;
  CreateStartMenuShortcutFlag: Boolean;

function DeleteUserData(): Boolean;
begin
  { 删用户数据只认这一面旗：默认 False（保留）；卸载时用户在对话框
    里选“是”才置 True。静默卸载不弹窗，旗保持 False，即保留。 }
  Result := DeleteUserDataFlag;
end;

function UserDataExists(): Boolean;
var
  AppDir: String;
begin
  { 有任一项用户数据才值得问用户；一项都没有就不问，直接保留。 }
  AppDir := ExpandConstant('{app}');
  Result := FileExists(AppDir + '\config.yaml') or
    FileExists(AppDir + '\data\bosshunter.db') or
    DirExists(AppDir + '\data\resumes');
end;

function GetUserProfile(Param: String): String;
begin
  // Inno 没有 userprofile 常量（v39 曾误用，iscc 报 Unknown constant，
  // 流水线 #4 因此失败）：用户目录只能从环境变量取，与
  // StopProcessesBeforeUninstall 里的 GetEnv('USERPROFILE') 同款写法。
  // 供 [UninstallDelete] 的 code:GetUserProfile 用。
  // 注意：Pascal Script 的花括号注释不支持嵌套，注释里不能出现花括号
  // （流水线 #5 教训：注释里的 userprofile 后那个右花括号提前闭合了
  // 注释，iscc 在第 122 行报 Syntax error）。
  Result := GetEnv('USERPROFILE');
end;

function GetCurrentProcessId(): DWORD;
  external 'GetCurrentProcessId@kernel32.dll stdcall';

procedure LogUninstall(const Text: String);
begin
  if UninstallLogPath <> '' then
    SaveStringToFile(UninstallLogPath, Text + #13#10, True);
end;

procedure InitializeWizard();
var
  DirNote: TNewStaticText;
begin
  { 目录选择页下写用户指定的说明：默认装到安装包所在目录下的
    简历猎手文件夹；改成别的目录就按所选路径安装。
    v41 修裁切（lucky 真机：第二行“路径安装。”上半截被截断）：
    旧版 DirNote 高度写死 36px、AutoSize 关着——说明 60 个字在
    DirEdit 宽度（约 332px）下要折两行，行高随系统 DPI 缩放
    （125% 约 19px、150% 约 22px，两行 38–45px），36px 装不下，
    第二行就被控件底边裁掉。改成 AutoSize=True + WordWrap=True
    （Inno 自家标签就是这么自适应高度的），初始高度也给到 48px，
    Top 往上收 4px，整段文字在各 DPI 下都完整显示；目录页下面只
    有底部的 DiskSpaceLabel（Top 216），说明再长也碰不到它。 }
  DirNote := TNewStaticText.Create(WizardForm);
  DirNote.Parent := WizardForm.SelectDirPage;
  DirNote.Left := WizardForm.DirEdit.Left;
  DirNote.Top := WizardForm.DirEdit.Top + WizardForm.DirEdit.Height + 8;
  DirNote.Width := WizardForm.DirEdit.Width;
  DirNote.Height := 48;
  DirNote.AutoSize := True;
  DirNote.WordWrap := True;
  DirNote.Caption := '默认安装到安装包所在目录下的简历猎手文件夹；若修改为其他目录，则按所选路径安装';

  { 准备安装页下方放两个快捷方式勾选框（默认都勾选）：ReadyMemo
    几乎占满整页（Top 24 + Height 205，页高 237），下面只剩 8px
    空白，不够放两行勾选框——先把 ReadyMemo 缩短 48px，再在腾出
    的空位放两框（第一框 Top = Memo 底 + 8，第二框再往下 24px，
    两框底边 230px，页高 237，刚好放下；Memo 有滚动条，内容长了
    可以滚）。状态先存进两个 Flag（默认 True），用户在准备安装页
    点“下一步”时 NextButtonClick 再从勾选框同步一次；静默安装不
    走向导，Flag 保持默认 True，两个快捷方式照常创建（冒烟测试
    断言的就是这一点）。 }
  WizardForm.ReadyMemo.Height := WizardForm.ReadyMemo.Height - 48;
  DesktopShortcutCheckBox := TNewCheckBox.Create(WizardForm);
  DesktopShortcutCheckBox.Parent := WizardForm.ReadyPage;
  DesktopShortcutCheckBox.Left := WizardForm.ReadyMemo.Left;
  DesktopShortcutCheckBox.Top := WizardForm.ReadyMemo.Top + WizardForm.ReadyMemo.Height + 8;
  DesktopShortcutCheckBox.Width := WizardForm.ReadyMemo.Width;
  DesktopShortcutCheckBox.Height := 17;
  DesktopShortcutCheckBox.Caption := '创建桌面快捷方式';
  DesktopShortcutCheckBox.Checked := True;
  StartMenuShortcutCheckBox := TNewCheckBox.Create(WizardForm);
  StartMenuShortcutCheckBox.Parent := WizardForm.ReadyPage;
  StartMenuShortcutCheckBox.Left := WizardForm.ReadyMemo.Left;
  StartMenuShortcutCheckBox.Top := DesktopShortcutCheckBox.Top + DesktopShortcutCheckBox.Height + 7;
  StartMenuShortcutCheckBox.Width := WizardForm.ReadyMemo.Width;
  StartMenuShortcutCheckBox.Height := 17;
  StartMenuShortcutCheckBox.Caption := '创建开始菜单快捷方式';
  StartMenuShortcutCheckBox.Checked := True;
  CreateDesktopShortcutFlag := True;
  CreateStartMenuShortcutFlag := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { 离开准备安装页时把勾选框状态同步进 Flag，[Icons] 的 Check
    函数安装时读 Flag。静默安装不经过这里，Flag 保持默认 True。 }
  if CurPageID = wpReady then
  begin
    CreateDesktopShortcutFlag := DesktopShortcutCheckBox.Checked;
    CreateStartMenuShortcutFlag := StartMenuShortcutCheckBox.Checked;
  end;
end;

function ShouldCreateDesktopShortcut(): Boolean;
begin
  Result := CreateDesktopShortcutFlag;
end;

function ShouldCreateStartMenuShortcut(): Boolean;
begin
  Result := CreateStartMenuShortcutFlag;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    { 按勾选状态写快捷方式 marker：程序启动时
      ensure_desktop_shortcut 认 marker——勾选桌面时写
      desktop_shortcut_created（程序不再重复建），不勾选时写
      desktop_shortcut_declined（程序启动时不再补建，否则勾选框
      等于没生效）。两个 marker 互斥：写一个就删另一个（覆盖安装
      时旧 marker 可能还在）。写失败不挡安装（最坏是程序启动时
      按老行为补建一次桌面快捷方式）；开始菜单快捷方式程序从不
      自建，不用 marker。 }
    DataDir := ExpandConstant('{app}\data');
    try
      ForceDirectories(DataDir);
      if CreateDesktopShortcutFlag then
      begin
        DeleteFile(DataDir + '\desktop_shortcut_declined');
        SaveStringToFile(DataDir + '\desktop_shortcut_created', '{#MyAppVersion}' + #13#10, False);
      end
      else
      begin
        DeleteFile(DataDir + '\desktop_shortcut_created');
        SaveStringToFile(DataDir + '\desktop_shortcut_declined', '{#MyAppVersion}' + #13#10, False);
      end;
    except
    end;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  UninstallLogPath := GetEnv('TEMP') + '\简历猎手-卸载日志.txt';
  { InitializeUninstall 是事件函数，必须返回 Boolean：True 才继续卸载。
    本函数没有“某条件不允许卸载”的设计，一律返回 True。
    注意：这里不能建卸载选项页——Inno 的 CreateInputOptionPage 等
    Create*Page 函数在卸载程序里被禁（sfNoUninstall），调了就报脚本错、
    卸载以退出码 1（Setup failed to initialize）失败（流水线 #8 教训）。
    删用户数据的选择改在 CurUninstallStepChanged (usAppMutexCheck)
    里问 SuppressibleMsgBox 对话框。 }
  Result := True;
end;

function StopProcessesBeforeUninstall(): Boolean;
var
  Script, ScriptPath: String;
  ResultCode: Integer;
  InstallDir, ProfileDir: String;
begin
  Result := True;
  InstallDir := ExpandConstant('{app}');
  ProfileDir := GetEnv('USERPROFILE') + '\.jianli-lieshou\browser-profile';
  // 卸载前停进程（v41 设计，脚本全文见 installer.py
  // uninstall_stop_processes_script，.iss 里这段 Pascal 拼出来的
  // 文本必须与它逐字一致，由 check-installer-consistency.py 核对）：
  // 参数化脚本——安装目录、浏览器配置目录、卸载日志路径、要排除
  // 的 PID、exe 名都不写进脚本文件，由下面 Exec 的命令行传参，
  // 所以 .ps1 全文只用 ASCII（SaveStringToFile 写临时文件时非
  // ASCII 可能走系统代码页）；命令行是 Unicode，中文路径没问题。
  // 流程：先 POST /api/exit 走应用内优雅退出 → 最多 3 轮按路径证据
  // 杀进程，每轮杀完轮询等 PID 真正消失（最多 10 秒）→ 探句柄：
  // 独占打开 exe 与 bosshunter.db，5 秒没释放就再 POST 一次等
  // 60 秒（v42：30 秒不够，优雅退出最长约 66 秒）→ 还占着就按
  // 产品自己的 exe 名枚举 PID 再杀一轮 → 全程写进卸载日志（以前
  // 扫尾的输出只打到 powershell stdout，Exec 不收，日志里只剩一个
  // 退出码，根本看不出扫尾干了什么；v42 再补上关键节点：脚本开始、
  // 两次 api-exit 结果、最后一招枚举到的 PID、杀失败的 PID）。
  // 判定红线不变：只认 ExecutablePath 前缀 / 命令行里的安装目录 /
  // 命令行里的浏览器配置目录；绝不按映像名 broad 杀 node / chrome；
  // 按名排除卸载程序两阶段 unins000.exe 与 _unins.tmp；循环 2 还
  // 排除 $PID（扫尾脚本自己的 powershell.exe）。最后一招按名枚举
  // 只认产品自己的 exe 名，且只在句柄探针已证明我方文件被占着、
  // 优雅退出已试过两轮的前提下才动手，按 PID 杀，不是 broad 杀
  // 映像名。
  Script :=
    'param([string]$installDir, [string]$profile, [string]$logPath, [int]$excludePid, [string]$exeName)' + #13#10 +
    'function Write-StopLog([string]$text) { try { Add-Content -Path $logPath -Value $text -Encoding Ascii -ErrorAction Stop } catch { } }' + #13#10 +
    '$prefix = $installDir + ''\''' + #13#10 +
    '$exePath = Join-Path $installDir $exeName' + #13#10 +
    '$dbPath = Join-Path $installDir ''data\bosshunter.db''' + #13#10 +
    'function Get-TargetPids {' + #13#10 +
    '  $procs = @(Get-CimInstance Win32_Process)' + #13#10 +
    '  $ids = @()' + #13#10 +
    '  foreach ($p in $procs) { if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -and $p.ProcessId -ne $excludePid -and $p.Name -ne ''unins000.exe'' -and $p.Name -ne ''_unins.tmp'') { $ids += $p.ProcessId } }' + #13#10 +
    '  foreach ($p in $procs) { if (@(''node.exe'',''cmd.exe'',''robocopy.exe'',''powershell.exe'') -contains $p.Name -and $p.CommandLine -and $p.CommandLine.IndexOf($installDir, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and $p.ProcessId -ne $excludePid -and $p.ProcessId -ne $PID -and $p.Name -ne ''unins000.exe'' -and $p.Name -ne ''_unins.tmp'') { $ids += $p.ProcessId } }' + #13#10 +
    '  foreach ($p in $procs) { if (($p.Name -eq ''chrome.exe'' -or $p.Name -eq ''msedge.exe'') -and $p.CommandLine -and $p.CommandLine.Contains($profile)) { $ids += $p.ProcessId } }' + #13#10 +
    '  return ($ids | Select-Object -Unique)' + #13#10 +
    '}' + #13#10 +
    'function Wait-PidsGone($pids, [int]$seconds) {' + #13#10 +
    '  $deadline = (Get-Date).AddSeconds($seconds)' + #13#10 +
    '  while ((Get-Date) -lt $deadline) {' + #13#10 +
    '    $alive = @()' + #13#10 +
    '    foreach ($id in $pids) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $alive += $id } }' + #13#10 +
    '    if ($alive.Count -eq 0) { return @() }' + #13#10 +
    '    Start-Sleep -Milliseconds 500' + #13#10 +
    '  }' + #13#10 +
    '  $alive = @()' + #13#10 +
    '  foreach ($id in $pids) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $alive += $id } }' + #13#10 +
    '  return $alive' + #13#10 +
    '}' + #13#10 +
    'function Test-FileLocked([string]$path) {' + #13#10 +
    '  if (-not (Test-Path $path)) { return $false }' + #13#10 +
    '  try { $s = [IO.File]::Open($path, ''Open'', ''ReadWrite'', ''None''); $s.Close(); return $false } catch { return $true }' + #13#10 +
    '}' + #13#10 +
    'function Wait-FilesFree([int]$seconds) {' + #13#10 +
    '  $deadline = (Get-Date).AddSeconds($seconds)' + #13#10 +
    '  while ((Get-Date) -lt $deadline) {' + #13#10 +
    '    if (-not (Test-FileLocked $exePath) -and -not (Test-FileLocked $dbPath)) { return $true }' + #13#10 +
    '    Start-Sleep -Milliseconds 1000' + #13#10 +
    '  }' + #13#10 +
    '  return ((-not (Test-FileLocked $exePath)) -and (-not (Test-FileLocked $dbPath)))' + #13#10 +
    '}' + #13#10 +
    'Write-StopLog "stop-sweep: start"' + #13#10 +
    'try { Invoke-WebRequest -Uri ''http://127.0.0.1:8686/api/exit'' -Method Post -TimeoutSec 3 | Out-Null; Write-StopLog "api-exit: ok" } catch { Write-StopLog "api-exit: failed" }' + #13#10 +
    '$killed = 0; $failed = 0; $failedIds = @()' + #13#10 +
    'for ($round = 1; $round -le 3; $round++) {' + #13#10 +
    '  $targets = @(Get-TargetPids)' + #13#10 +
    '  if ($targets.Count -eq 0) { break }' + #13#10 +
    '  foreach ($id in $targets) { try { Stop-Process -Id $id -Force -ErrorAction Stop; $killed += 1 } catch { $failed += 1; $failedIds += $id } }' + #13#10 +
    '  $still = @(Wait-PidsGone $targets 10)' + #13#10 +
    '  if ($still.Count -eq 0) { break }' + #13#10 +
    '}' + #13#10 +
    '$probeOk = Wait-FilesFree 5' + #13#10 +
    'if (-not $probeOk) {' + #13#10 +
    '  try { Invoke-WebRequest -Uri ''http://127.0.0.1:8686/api/exit'' -Method Post -TimeoutSec 3 | Out-Null; Write-StopLog "api-exit: ok" } catch { Write-StopLog "api-exit: failed" }' + #13#10 +
    '  $probeOk = Wait-FilesFree 60' + #13#10 +
    '}' + #13#10 +
    'if (-not $probeOk) {' + #13#10 +
    '  $byName = @(Get-CimInstance Win32_Process -Filter "Name = ''$exeName''" | ForEach-Object { $_.ProcessId })' + #13#10 +
    '  $byName = @($byName | Where-Object { $_ -ne $excludePid })' + #13#10 +
    '  if ($byName.Count -gt 0) { Write-StopLog ("byname: " + ($byName -join ''; '')) }' + #13#10 +
    '  foreach ($id in $byName) { try { Stop-Process -Id $id -Force -ErrorAction Stop; $killed += 1 } catch { $failed += 1; $failedIds += $id } }' + #13#10 +
    '  if ($byName.Count -gt 0) { $null = Wait-PidsGone $byName 10; $probeOk = Wait-FilesFree 5 }' + #13#10 +
    '}' + #13#10 +
    '$stuck = @(Get-TargetPids)' + #13#10 +
    'Write-StopLog ("stop-sweep: killed=$killed failed=$failed stuck=$($stuck.Count) probe-ok=$probeOk")' + #13#10 +
    'if ($stuck.Count -gt 0) { Write-StopLog ("stuck-procs: " + ($stuck -join ''; '')) }' + #13#10 +
    'if ($failedIds.Count -gt 0) { Write-StopLog ("failed-procs: " + ($failedIds -join ''; '')) }' + #13#10 +
    'Write-Output "killed=$killed failed=$failed stuck=$($stuck.Count) probe-ok=$probeOk"';
  ScriptPath := ExpandConstant('{tmp}\jl-stop-processes.ps1');
  if not SaveStringToFile(ScriptPath, Script, False) then
  begin
    LogUninstall('卸载前停进程：写临时脚本失败，跳过扫尾（安装目录下可能仍有运行中的进程）。');
    Exit;
  end;
  if Exec('powershell.exe',
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + ScriptPath + '"' +
    ' -InstallDir "' + InstallDir + '" -ProfileDir "' + ProfileDir + '" -LogPath "' + UninstallLogPath + '" -ExcludePid ' + IntToStr(GetCurrentProcessId()) + ' -ExeName "简历猎手.exe"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    LogUninstall('卸载前停进程：powershell 退出码 ' + IntToStr(ResultCode))
  else
    LogUninstall('卸载前停进程：powershell 没能启动（安装目录下可能仍有运行中的进程）。');
  DeleteFile(ScriptPath);
end;

procedure CollectLeftovers(const Dir: String; var List: TStringList; var Count: Integer);
var
  FindRec: TFindRec;
  Path: String;
begin
  if FindFirst(Dir + '\*', FindRec) then
  try
    repeat
      if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
      begin
        Path := Dir + '\' + FindRec.Name;
        Inc(Count);
        if Count <= 20 then
          List.Add(Path);
        if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
          CollectLeftovers(Path, List, Count);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Leftovers: TStringList;
  Count, i: Integer;
  Notice: String;
  InstallDir: String;
begin
  if CurUninstallStep = usAppMutexCheck then
  begin
    { 删用户数据先问用户，默认保留。问在这里（确认卸载之后、真正删
      文件之前），不用 InitializeUninstall 里问（那时还在确认卸载前）。
      SuppressibleMsgBox：静默卸载（/SUPPRESSMSGBOXES）不弹窗，直接
      按默认值 IDNO（保留）走，不会挂住；交互卸载才弹是/否对话框，
      默认按钮是否（MB_DEFBUTTON2）。没用户数据时不问。 }
    if UserDataExists() then
    begin
      if SuppressibleMsgBox('卸载将删除程序文件与缓存、临时数据。' + #13#10 +
          '是否同时删除用户数据（config.yaml、简历、数据库）？删了找不回来。',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DeleteUserDataFlag := True;
    end;
  end;
  if CurUninstallStep = usUninstall then
  begin
    { 先停进程再删文件：删的是 [UninstallDelete] 清单与 Inno 自动
      移除的程序文件，停进程失败也不静默跳过——记进卸载日志。 }
    StopProcessesBeforeUninstall();
    if DeleteUserData() then
      LogUninstall('卸载选项：用户选择同时删除用户数据。')
    else
      LogUninstall('卸载选项：保留用户数据（config.yaml、简历、数据库）。');
  end;
  if CurUninstallStep = usDone then
  begin
    LogUninstall('进入 usDone。');
    { 删不掉的残留不静默跳过：如实记日志、完成页告知用户手动处理。 }
    InstallDir := ExpandConstant('{app}');
    if DirExists(InstallDir) then
    begin
      Leftovers := TStringList.Create();
      try
        Count := 0;
        CollectLeftovers(InstallDir, Leftovers, Count);
        if Count > 0 then
        begin
          LogUninstall('卸载后残留（被占用，未能删除）：');
          for i := 0 to Leftovers.Count - 1 do
            LogUninstall('  - ' + Leftovers[i]);
          if Count > Leftovers.Count then
            LogUninstall('  ……共 ' + IntToStr(Count) + ' 项，上面只列出前 20 项。');
          Notice := '以下文件或文件夹正在被占用，未能删除（已记入卸载日志）：' + #13#10;
          for i := 0 to Leftovers.Count - 1 do
            Notice := Notice + '  - ' + Leftovers[i] + #13#10;
          if Count > Leftovers.Count then
            Notice := Notice + '  ……共 ' + IntToStr(Count) + ' 项。' + #13#10;
          Notice := Notice + '请关闭占用它们的程序后手动删除，或重启电脑后再删除。' + #13#10 +
            '卸载日志：' + UninstallLogPath;
          { 用 SuppressibleMsgBox：静默卸载不弹窗（按 IDOK 直接返回），
            否则普通 MsgBox 在静默模式下也会弹窗、没人点就挂住。 }
          SuppressibleMsgBox(Notice, mbInformation, MB_OK, IDOK);
        end;
      finally
        Leftovers.Free();
      end;
    end;
  end;
end;
