; 简历猎手 · Inno Setup 6 安装 / 卸载脚本（v39，1.1.25）。
;
; 口径唯一来源是 src/bosshunter/installer.py：默认路径、卸载清理清单、
; 安全谓词、停进程判定都必须与那个模块逐项一致，对应关系由
; scripts/check-installer-consistency.py 核对（verify.sh 会跑它）。
;
; 编译（Windows，CI 里由 build-exe.yml 跑）：
;   choco install innosetup -y
;   iscc /DMyAppVersion=1.1.25 installer\jianli-lieshou.iss
; 不传 /DMyAppVersion 时用下面 #define 的默认值（发版时记得与
; pyproject.toml 的 version 保持一致，verify.sh 不校验这一项，
; CI 里读 pyproject.toml 传值才是准的）。
; 产物：dist-installer\简历猎手-安装程序-1.1.25.exe。
; 编译前必须先跑 pyinstaller（dist\简历猎手\ 要有内容），[Files]
; 直接把 dist 产物原样打进安装包。
;
; 关键约定：
; - DefaultDirName={src}\zhaopinglieshou：默认装到安装包所在目录下的
;   zhaopinglieshou 子文件夹；用户在向导里改目录后按所选路径安装，
;   不再强行追加一层子文件夹（installer.py resolve_target_dir 同款规则）。
; - PrivilegesRequired=lowest：不强制管理员权限，普通权限就能装到
;   自己选的目录；卸载注册表项因此写在 HKCU 下
;   （Software\Microsoft\Windows\CurrentVersion\Uninstall\简历猎手_is1，
;   Inno 按 AppId 自动写 DisplayName / DisplayVersion /
;   InstallLocation / UninstallString / Publisher / DisplayIcon）。
; - 桌面快捷方式名「简历猎手.lnk」与 updater._DESKTOP_SHORTCUT_NAME
;   同名；应用第一次启动时 ensure_desktop_shortcut 也会建同一个，
;   两边不打架。
; - 默认 config.yaml 不是安装包里直接带的文件：dist 里只有
;   _internal\config.example.yaml，launcher 首次启动时复制为
;   安装目录下的 config.yaml（原有行为不变）。
; - 卸载不静默删用户数据：默认保留 config.yaml、
;   data\bosshunter.db、data\resumes\，卸载时问一次（用户选“是”才删）。
;   注意：Inno 的卸载程序里建不了自定义向导页（CreateInputOptionPage
;   等 Create*Page 函数在卸载程序里被禁，调了卸载直接退 1，见
;   InitializeUninstall 的注释），所以改问 SuppressibleMsgBox 对话框。

#ifndef MyAppVersion
  #define MyAppVersion "1.1.25"
#endif

[Setup]
AppName=简历猎手
AppVersion={#MyAppVersion}
AppVerName=简历猎手 {#MyAppVersion}
AppId=简历猎手
AppPublisher=聚梦Muse AI
AppPublisherURL=https://www.16688.com.cn/shop/S542647
DefaultDirName={src}\zhaopinglieshou
DefaultGroupName=简历猎手
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
Name: "{group}\简历猎手"; Filename: "{app}\简历猎手.exe"; WorkingDir: "{app}"
Name: "{group}\卸载简历猎手"; Filename: "{uninstallexe}"
Name: "{userdesktop}\简历猎手"; Filename: "{app}\简历猎手.exe"; WorkingDir: "{app}"

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
Type: filesandordirs; Name: "{app}\data\resumes"; Check: DeleteUserData
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
; 注意：{app} 本身由 Inno 在文件删完、目录为空时自动移除；保留用户
; 数据时 {app}\config.yaml 与 data\ 会留下来，这是故意的。
; %AppData% / %LocalAppData% 下没有我方写的东西，卸载清单里不碰。

[Code]
var
  DeleteUserDataFlag: Boolean;
  UninstallLogPath: String;

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
  { 目录选择页上写清楚最终安装路径的规则：默认是安装包所在目录下的
    zhaopinglieshou；改成别的目录就按所选路径安装，不再追加子文件夹。 }
  DirNote := TNewStaticText.Create(WizardForm);
  DirNote.Parent := WizardForm.SelectDirPage;
  DirNote.Left := WizardForm.DirEdit.Left;
  DirNote.Top := WizardForm.DirEdit.Top + WizardForm.DirEdit.Height + 12;
  DirNote.Width := WizardForm.DirEdit.Width;
  DirNote.Height := 36;
  DirNote.AutoSize := False;
  DirNote.WordWrap := True;
  DirNote.Caption := '默认装到安装包所在目录下的 zhaopinglieshou 文件夹。' +
    '改成别的目录就按所选路径安装，不再追加子文件夹；最终路径以这里为准。';
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
  Q: Char;
  Script, ScriptPath: String;
  ResultCode: Integer;
  InstallDir, ProfileDir: String;
begin
  Result := True;
  Q := Chr(39);
  InstallDir := ExpandConstant('{app}');
  ProfileDir := GetEnv('USERPROFILE') + '\.jianli-lieshou\browser-profile';
  { 先走应用内完整退出清理（POST /api/exit：停任务 → 停 runtime /
    拉起的 Chrome → 安装目录扫尾），调不通就跳过，不挡卸载；
    再跑同一份判定（installer.uninstall_stop_processes_script）的
    PowerShell 扫尾：只认 ExecutablePath 在安装目录下的进程（排除
    卸载程序自己的 PID，不自杀），工作浏览器只杀命令行里带本程序
    配置目录的 chrome.exe / msedge.exe。绝不按映像名杀 node /
    chrome（v28 教训：会误杀用户浏览器和其他 node）。
    注意 PID 只排除当前阶段：Inno 卸载是两阶段的，第一阶段
    unins000.exe 在安装目录里、第二阶段 _unins.tmp 在 %TEMP% 里；
    第二阶段跑脚本时 GetCurrentProcessId 是第二阶段的 PID，第一阶段
    的 PID 不在排除里，只靠 PID 排除会把第一阶段杀掉——被杀的第一
    阶段退出码是 -1（TerminateProcess -1），等它退出的调用方就看到
    卸载失败（流水线 #9 重跑教训）。所以第一循环还要按名排除
    unins000.exe 与 _unins.tmp 两个阶段的卸载程序。 }
  Script :=
    'try { Invoke-WebRequest -Uri ' + Q + 'http://127.0.0.1:8686/api/exit' + Q +
    ' -Method Post -TimeoutSec 3 | Out-Null; Start-Sleep -Seconds 4 } catch { }' + #13#10 +
    '$installDir = ' + Q + InstallDir + Q + '; ' +
    '$prefix = $installDir + ' + Q + '\' + Q + '; ' +
    '$killed = 0; $failed = 0; $browser = 0; ' +
    'foreach ($p in Get-CimInstance Win32_Process) { ' +
    'if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)' +
    ' -and $p.ProcessId -ne ' + IntToStr(GetCurrentProcessId()) +
    ' -and $p.Name -ne ' + Q + 'unins000.exe' + Q +
    ' -and $p.Name -ne ' + Q + '_unins.tmp' + Q + ') ' +
    '{ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $killed += 1 } catch { $failed += 1 } } }; ' +
    '$profile = ' + Q + ProfileDir + Q + '; ' +
    'foreach ($p in Get-CimInstance Win32_Process) { ' +
    'if (($p.Name -eq ' + Q + 'chrome.exe' + Q + ' -or $p.Name -eq ' + Q + 'msedge.exe' + Q +
    ') -and $p.CommandLine -and $p.CommandLine.Contains($profile)) ' +
    '{ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $browser += 1 } catch { $failed += 1 } } }; ' +
    'if (($killed + $browser) -gt 0) { Start-Sleep -Seconds 1 }; ' +
    'Write-Output "killed=$killed failed=$failed browser=$browser"';
  ScriptPath := ExpandConstant('{tmp}\jl-stop-processes.ps1');
  if not SaveStringToFile(ScriptPath, Script, False) then
  begin
    LogUninstall('卸载前停进程：写临时脚本失败，跳过扫尾（安装目录下可能仍有运行中的进程）。');
    Exit;
  end;
  if Exec('powershell.exe',
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + ScriptPath + '"',
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
