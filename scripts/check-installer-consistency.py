#!/usr/bin/env python3
r"""核对 Inno 安装脚本与 installer.py 口径一致（v41）。

用法：python3 scripts/check-installer-consistency.py
退出码 0 = 一致；1 = 有不一致（打印哪一项对不上）。

核对项（两处必须同一份口径）：
- 默认安装目录：iss DefaultDirName 必须是 {src}\<INSTALL_SUBDIR>；
- 普通权限安装：PrivilegesRequired=lowest（卸载项进 HKCU）；
- 卸载注册表项：AppId 与 installer.py 的 UNINSTALL_REGISTRY_KEY 一致；
- 桌面快捷方式名与 updater._DESKTOP_SHORTCUT_NAME 同名；
- 目录说明标签：AutoSize + 48px 高（v41：定高 36px 在高 DPI 下裁切）；
- 开始菜单文件夹选择页已删（DisableProgramGroupPage=yes），
  但 [Icons] 里 {group} 快捷方式还在；
- 准备安装页两个勾选框（默认都勾选），[Icons] 的 Check 条件认它们，
  安装后按勾选写 desktop_shortcut_created / _declined marker；
- [UninstallDelete] 覆盖 installer.py 的全部运行产物、用户数据
  （带 Check: DeleteUserData）、浏览器配置目录、%TEMP% 三个前缀；
- 空了才删的目录（dirifempty）与 installer.py REMOVE_IF_EMPTY_RELATIVE
  一致，且在 data 文件条目之后、安装目录外条目之前；
- 停进程脚本：参数化（v41）——POST /api/exit → 3 轮杀进程 +
  Wait-PidsGone 轮询等 PID 真正消失 → 句柄探针（独占打开 exe 与
  db）→ 最后一招按产品自己的 exe 名枚举 PID 杀；ExecutablePath
  前缀规则 + 排除自己 PID + 工作浏览器只按命令行里的配置目录杀，
  不按映像名杀；结果写进卸载日志；
- 脚本默认版本号与 pyproject / bosshunter.__version__ 一致。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bosshunter import __version__, installer  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("PASS: " if ok else "FAIL: ") + label)
    if not ok:
        FAILURES.append(label)


def eval_pascal_stop_script(code: str) -> str | None:
    """把 .iss 里 Script := '...' + #13#10 + ... 的拼接求值成 PowerShell 文本。

    只处理 StopProcessesBeforeUninstall 里的 Script 赋值：单引号字面量
    （'' 是转义的单引号）、#13#10（换行）。v41 的脚本是参数化的，
    安装目录等都不写进脚本文件，所以求值结果不含任何占位符；
    Q / InstallDir / ProfileDir / IntToStr(GetCurrentProcessId()) 的
    占位分支是 v40 遗留，留着防回归（脚本里再出现这些记号时不静默吞掉）。
    结构对不上返回 None（检查跳过，不误报）。
    """
    m = re.search(r"Script :=\s*(.*?);\s*\n\s*ScriptPath :=", code, re.S)
    if not m:
        return None
    rhs = m.group(1)
    out: list[str] = []
    i, n = 0, len(rhs)
    while i < n:
        c = rhs[i]
        if c == "'":
            j = i + 1
            buf: list[str] = []
            closed = False
            while j < n:
                if rhs[j] == "'":
                    if j + 1 < n and rhs[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    closed = True
                    break
                buf.append(rhs[j])
                j += 1
            if not closed:
                return None
            out.append("".join(buf))
            i = j + 1
        elif rhs.startswith("Q", i) and (
            i == 0 or not (rhs[i - 1].isalnum() or rhs[i - 1] == "_")
        ):
            out.append("'")
            i += 1
        elif rhs.startswith("#13#10", i):
            out.append("\n")
            i += 6
        elif rhs.startswith("IntToStr(GetCurrentProcessId())", i):
            out.append("<PID>")
            i += len("IntToStr(GetCurrentProcessId())")
        elif rhs.startswith("InstallDir", i):
            out.append("<INSTALLDIR>")
            i += len("InstallDir")
        elif rhs.startswith("ProfileDir", i):
            out.append("<PROFILEDIR>")
            i += len("ProfileDir")
        else:
            i += 1
    return "".join(out)


def strip_pascal_comments(code: str) -> str:
    """去掉 [Code] 段里的注释（// 行注释、{ } 块注释、(* *) 注释）。

    只去掉注释：字符串里的花括号/斜杠不算（PowerShell 脚本、
    ExpandConstant('{app}') 都是字符串）。块注释不支持嵌套，
    第一个 } 即闭合（与 find_nested_brace_comments 同款判定）。
    禁令类检查（某函数名不得出现）要对去掉注释后的文本做，
    否则注释里提到名字也会误报。
    """
    out: list[str] = []
    i, n = 0, len(code)
    in_str = False
    while i < n:
        c = code[i]
        if in_str:
            out.append(c)
            if c == "'":
                if i + 1 < n and code[i + 1] == "'":
                    out.append(code[i + 1])
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == "'":
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            while i < n and code[i] != "\n":
                i += 1
            continue
        if c == "(" and i + 1 < n and code[i + 1] == "*":
            j = code.find("*)", i + 2)
            if j == -1:
                break
            i = j + 2
            continue
        if c == "{":
            j = code.find("}", i)
            if j == -1:
                break
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def find_nested_brace_comments(code: str) -> list[str]:
    """[Code] 段里 { ... } 注释不支持嵌套：注释内再出现 { 会提前闭合。

    流水线 #5 教训：GetUserProfile 的注释里写了 {userprofile}，注释在
    那个 } 处提前闭合，同一行剩下的文字被当成代码，iscc 在第 122 行
    第 27 列报 Syntax error。返回问题描述列表（空 = 干净）。
    字符串里的花括号（如 PowerShell 脚本、ExpandConstant('{app}')）
    与 // 行注释里的花括号不算。
    """
    problems: list[str] = []
    i = 0
    n = len(code)
    line = 1
    in_str = False
    in_line_comment = False
    in_star_comment = False
    brace_start_line = 0
    while i < n:
        c = code[i]
        if c == "\n":
            line += 1
            in_line_comment = False
            i += 1
            continue
        if in_line_comment:
            i += 1
            continue
        if in_star_comment:
            if c == "*" and i + 1 < n and code[i + 1] == ")":
                in_star_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_str:
            if c == "'":
                if i + 1 < n and code[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if brace_start_line:
            if c == "{":
                problems.append(
                    f"第 {line} 行：花括号注释里又出现 {{（注释在第一个 }} 处提前闭合）"
                )
            elif c == "}":
                brace_start_line = 0
            i += 1
            continue
        if c == "'":
            in_str = True
        elif c == "/" and i + 1 < n and code[i + 1] == "/":
            in_line_comment = True
            i += 2
            continue
        elif c == "(" and i + 1 < n and code[i + 1] == "*":
            in_star_comment = True
            i += 2
            continue
        elif c == "{":
            brace_start_line = line
        i += 1
    if brace_start_line:
        problems.append(f"第 {brace_start_line} 行起的花括号注释没有闭合")
    return problems


def main() -> int:
    iss_path = ROOT / "installer" / "jianli-lieshou.iss"
    if not iss_path.is_file():
        print("FAIL: 找不到 installer/jianli-lieshou.iss")
        return 1
    # .iss 必须带 UTF-8 BOM：iscc 无 BOM 时按系统 ANSI 读，中文向导文案
    # 会乱码（v40 教训：lucky 真机上向导说明文案乱码）。utf-8-sig 读
    # （有 BOM 去掉、没 BOM 与 utf-8 一样），下面的 BOM 检查单独断言。
    check(
        iss_path.read_bytes()[:3] == b"\xef\xbb\xbf",
        ".iss 带 UTF-8 BOM（中文文案不乱码）",
    )
    iss = iss_path.read_text(encoding="utf-8-sig")

    check(
        f"DefaultDirName={{src}}\\{installer.INSTALL_SUBDIR}" in iss,
        f"默认安装目录是 {{src}}\\{installer.INSTALL_SUBDIR}",
    )
    check(
        f"默认安装到安装包所在目录下的{installer.INSTALL_SUBDIR}文件夹；"
        "若修改为其他目录，则按所选路径安装" in iss,
        "向导说明文案是用户指定的句子（默认安装到安装包所在目录下…）",
    )
    check(
        "PrivilegesRequired=lowest" in iss,
        "PrivilegesRequired=lowest（普通权限安装，卸载项进 HKCU）",
    )
    check(
        f"AppId={installer.APP_ID}" in iss,
        f"AppId 与 installer.py 一致（{installer.APP_ID}）",
    )
    check(
        installer.UNINSTALL_REGISTRY_KEY.endswith("\\" + installer.APP_ID + "_is1"),
        "卸载注册表项名是 AppId + _is1",
    )
    check(
        f'#define MyAppVersion "{__version__}"' in iss,
        f"iss 默认版本号是 {__version__}",
    )
    check(
        installer.APP_VERSION == __version__,
        f"installer.APP_VERSION 与 __version__ 一致（{__version__}）",
    )
    check(
        '{userdesktop}\\' + installer.DESKTOP_SHORTCUT_NAME.rsplit(".", 1)[0] in iss
        or '{userdesktop}\\' + installer.DESKTOP_SHORTCUT_NAME in iss,
        f"桌面快捷方式名是 {installer.DESKTOP_SHORTCUT_NAME}",
    )
    check(
        "OutputBaseFilename=" in iss and "MyAppVersion" in iss,
        "安装程序文件名带版本号（OutputBaseFilename 用 MyAppVersion）",
    )
    # v41 项 2：开始菜单文件夹选择页已删，但 {group} 快捷方式还在。
    check(
        "DisableProgramGroupPage=yes" in iss,
        "开始菜单文件夹选择页已删（DisableProgramGroupPage=yes）",
    )
    icons = re.split(r"(?m)^\[Icons\]\s*$", iss, maxsplit=1)[1]
    icons = re.split(r"(?m)^\[[^\]]+\]\s*$", icons, maxsplit=1)[0]
    check(
        "{group}\\简历猎手" in icons and "{group}\\卸载简历猎手" in icons,
        "[Icons] 里开始菜单快捷方式还在（{group}，默认组名即应用名）",
    )
    check(
        "Check: ShouldCreateStartMenuShortcut" in icons,
        "开始菜单快捷方式认 ShouldCreateStartMenuShortcut（勾选框决定）",
    )
    check(
        "Check: ShouldCreateDesktopShortcut" in icons,
        "桌面快捷方式认 ShouldCreateDesktopShortcut（勾选框决定）",
    )
    # v41 项 1：目录说明标签不再定高裁切。
    check(
        "DirNote.AutoSize := True" in iss
        and "DirNote.Height := 48" in iss
        and "DirNote.Top := WizardForm.DirEdit.Top + WizardForm.DirEdit.Height + 8"
        in iss,
        "目录说明标签 AutoSize + 高 48px + 距输入框 8px（高 DPI 下不裁切）",
    )
    # v41 项 3：准备安装页两个勾选框，默认都勾选；状态经 Flag 进 [Icons]。
    check(
        "DesktopShortcutCheckBox" in iss
        and "StartMenuShortcutCheckBox" in iss
        and iss.count("TNewCheckBox.Create(WizardForm)") == 2
        and "WizardForm.ReadyMemo.Height := WizardForm.ReadyMemo.Height - 48"
        in iss,
        "准备安装页有两个 TNewCheckBox（ReadyMemo 先缩短 48px 腾出空位）",
    )
    check(
        iss.count("Checked := True") >= 2,
        "两个快捷方式勾选框默认都勾选（Checked := True）",
    )
    check(
        "CreateDesktopShortcutFlag := True" in iss
        and "CreateStartMenuShortcutFlag := True" in iss,
        "两个 Flag 默认 True（静默安装不走向导，快捷方式照常创建）",
    )
    check(
        "function NextButtonClick(CurPageID: Integer): Boolean;" in iss
        and "CurPageID = wpReady" in iss,
        "离开准备安装页时 NextButtonClick 把勾选框状态同步进 Flag",
    )
    check(
        "function ShouldCreateDesktopShortcut(): Boolean;" in iss
        and "function ShouldCreateStartMenuShortcut(): Boolean;" in iss,
        "[Icons] 的 Check 条件是 ShouldCreate*Shortcut 函数",
    )
    check(
        "procedure CurStepChanged(CurStep: TSetupStep);" in iss,
        "CurStepChanged 原型正确（procedure + TSetupStep）",
    )
    check(
        "desktop_shortcut_declined" in iss
        and "desktop_shortcut_created" in iss
        and "SaveStringToFile(DataDir + '\\desktop_shortcut_declined'" in iss
        and "SaveStringToFile(DataDir + '\\desktop_shortcut_created'" in iss,
        "安装后按勾选写 marker：勾选写 created、不勾选写 declined",
    )
    check(
        "DeleteFile(DataDir + '\\desktop_shortcut_created')" in iss
        and "DeleteFile(DataDir + '\\desktop_shortcut_declined')" in iss,
        "两个 marker 互斥：写一个就删另一个（覆盖安装时旧 marker 不残留）",
    )
    check(
        installer.DESKTOP_SHORTCUT_DECLINED_MARKER_FILENAME
        == "desktop_shortcut_declined",
        "installer.py 的 declined marker 名与 .iss 一致",
    )

    # [UninstallDelete] 段：运行产物与用户数据必须都在。
    # 只认行首的段头：注释里提到段名（如 [Code]）不算，否则 split 会提前截断。
    section = re.split(r"(?m)^\[UninstallDelete\]\s*$", iss, maxsplit=1)[1]
    section = re.split(r"(?m)^\[Code\]\s*$", section, maxsplit=1)[0]
    # 只看非注释行：段尾的注释会提到 %AppData%（说明不碰），不算。
    section_code = "\n".join(
        ln for ln in section.splitlines()
        if not ln.strip().startswith(";")
    )
    runtime_ok = True
    for rel in installer._RUNTIME_RELATIVE:
        win = str(rel).replace("/", "\\")
        if win not in section and not (
            rel.suffix == "" and win in section
        ):
            # .star_hint_* 这类带通配符的在下面单独核对。
            runtime_ok = False
            print(f"  缺运行产物：{win}")
    check(runtime_ok, "运行产物清单都在 [UninstallDelete] 里")
    check(
        "update-backup-*" in section and ".star_hint_*" in section,
        "update-backup-* 与 .star_hint_* 在 [UninstallDelete] 里",
    )
    user_data_ok = True
    for rel in installer.USER_DATA_RELATIVE:
        win = str(rel).replace("/", "\\")
        line = next(
            (ln for ln in section.splitlines() if win in ln),
            "",
        )
        if "Check: DeleteUserData" not in line:
            user_data_ok = False
            print(f"  用户数据缺 Check: DeleteUserData：{win}")
    check(user_data_ok, "用户数据只在打勾时删（Check: DeleteUserData）")
    # data/ 空了才删：安装程序 ssPostInstall 写 marker 时建 data/，
    # 卸载删完里面文件后它就空了；Inno 不自动清变空的子目录，不加
    # dirifempty，空 data/ 会让安装目录删不干净（流水线 #22 冒烟教训：
    # 冒烟从不运行 exe，data/ 里没有任何用户数据，只剩空目录）。
    # 位置在 data 文件条目之后、安装目录外条目之前；只删空目录，
    # 保留用户数据时 data/ 非空就不动（绝不是 filesandordirs 删整个
    # data/——那会连保留的 db 与 resumes 一起删掉）。
    expected_dirifempty = [
        'Type: dirifempty; Name: "{app}\\' + str(rel).replace("/", "\\") + '"'
        for rel in installer.REMOVE_IF_EMPTY_RELATIVE
    ]
    dirifempty_lines = [
        ln.strip()
        for ln in section.splitlines()
        if ln.strip().startswith("Type: dirifempty;")
    ]
    check(
        dirifempty_lines == expected_dirifempty,
        "空时删的目录与 installer.py REMOVE_IF_EMPTY_RELATIVE 一致（dirifempty 条目）",
    )
    code_lines = section_code.splitlines()
    dirifempty_idx = next(
        (i for i, ln in enumerate(code_lines) if "dirifempty" in ln), -1
    )
    last_data_idx = max(
        i for i, ln in enumerate(code_lines) if "data\\resumes" in ln
    )
    outside_idx = next(
        (i for i, ln in enumerate(code_lines) if "{code:GetUserProfile}" in ln),
        -1,
    )
    check(
        dirifempty_idx > last_data_idx
        and 0 <= outside_idx
        and dirifempty_idx < outside_idx,
        "dirifempty 条目在 data 文件条目之后、安装目录外条目之前",
    )
    check(
        'Type: filesandordirs; Name: "{app}\\data"' not in section_code,
        "不 filesandordirs 删整个 data/（保留用户数据时 db 与 resumes 会被一起删掉）",
    )
    check(
        "{code:GetUserProfile}\\.jianli-lieshou" in section,
        "浏览器配置目录 {code:GetUserProfile}\\.jianli-lieshou 在清理清单里",
    )
    check(
        "{userprofile}" not in section_code,
        "没有用 Inno 不存在的 {userprofile} 常量",
    )
    check(
        "{%TEMP}" in section
        and all(p in section for p in installer.TEMP_PREFIXES),
        "%TEMP% 三个前缀都在清理清单里（用 {%TEMP}，不是 {tmp}）",
    )
    check(
        "{tmp}\\bosshunter" not in section and "{tmp}\\jianli-update" not in section,
        "没把 Inno 自己的 {tmp} 当成系统 %TEMP% 用",
    )
    check(
        "AppData" not in section_code and "LocalAppData" not in section_code,
        "清理清单不碰 %AppData% / %LocalAppData%",
    )

    # 停进程脚本：与 installer.uninstall_stop_processes_script 同款判定。
    # 同样只认行首的 [Code] 段头。
    code = re.split(r"(?m)^\[Code\]\s*$", iss, maxsplit=1)[1]
    check("ExecutablePath" in code and "StartsWith($prefix" in code,
          "停进程用 ExecutablePath 前缀规则")
    # v41 参数化脚本：安装目录等由 Exec 命令行传参，脚本文件全文 ASCII。
    check("param([string]$installDir" in code
          and "-InstallDir " in code and "-ProfileDir " in code
          and "-LogPath " in code and "-ExcludePid " in code
          and '-ExeName "简历猎手.exe"' in code,
          "停进程脚本是参数化的（Exec 命令行传安装目录/配置目录/日志/PID/exe 名）")
    # v41 流程：先 POST /api/exit → 最多 3 轮杀进程，每轮杀完轮询
    # Wait-PidsGone 等 PID 真正消失（最多 10 秒）→ 句柄探针。
    check("for ($round = 1; $round -le 3; $round++)" in code
          and "Wait-PidsGone $targets 10" in code,
          "停进程最多 3 轮，每轮杀完轮询等 PID 真正消失（Wait-PidsGone 10 秒）")
    check("Test-FileLocked" in code and "Wait-FilesFree" in code
          and "'ReadWrite'" in code and "Wait-FilesFree 5" in code
          and "Wait-FilesFree 60" in code,
          "停进程等句柄释放：独占打开 exe 与 db 探针（ReadWrite），5 秒不行再等 60 秒")
    check("stop-sweep: start" in code,
          "扫尾开始记一行（脚本没跑与跑了没扫到可区分）")
    check("api-exit: ok" in code and "api-exit: failed" in code,
          "两次 /api/exit POST 的结果都记进卸载日志")
    check("byname:" in code,
          "最后一招枚举到的 PID 记进卸载日志")
    check("failed-procs:" in code,
          "杀失败的 PID 记进卸载日志（提权 Access Denied 可见）")
    check("Win32_Process -Filter " in code and "Name = " in code
          and "Stop-Process -Id $id" in code,
          "最后一招按产品自己的 exe 名枚举 PID 再杀（按 PID 杀，不 broad 杀映像名）")
    check("Write-StopLog" in code and "stop-sweep: killed=" in code
          and "probe-ok=" in code,
          "停进程结果写进卸载日志（stop-sweep / probe-ok，不再只打 stdout）")
    check("Start-Sleep -Seconds 4" not in code
          and "Start-Sleep -Seconds 2" not in code,
          "v40 的固定 4 秒 / 每遍 2 秒睡眠已删（改成有界轮询，不再睡死数）")
    check("IndexOf($installDir" in code,
          "停进程第二轮按命令行里的安装目录杀（系统 node / cmd / robocopy）")
    # .iss 里四个名字是 Q 拼接的，原文里不连续；逐个查，精确的整串
    # 由下面“拼出的脚本与 installer.py 逐字一致”那项兜底。
    check(all(n in code for n in ("node.exe", "cmd.exe", "robocopy.exe",
                                   "powershell.exe")),
          "命令行轮只认 node / cmd / robocopy / powershell 四个名字")
    check("ProcessId -ne $PID" in code,
          "停进程第二轮排除扫尾脚本自己的 powershell.exe（$PID，不自杀）")
    check("stuck=$($stuck.Count)" in code and "stuck-procs:" in code,
          "停进程最后核实残留并输出 stuck 计数（不拦卸载、只记日志）")
    check("GetCurrentProcessId()" in code,
          "停进程排除卸载程序自己的 PID（不自杀）")
    # 卸载程序是两阶段的：第一阶段 unins000.exe 在安装目录里，第二阶段
    # _unins.tmp 在 %TEMP% 里；GetCurrentProcessId() 只排除当前阶段的
    # PID，第二阶段跑脚本时第一阶段的 PID 不在排除里，只靠 PID 排除
    # 会把第一阶段杀掉——被杀的第一阶段退出码是 -1（TerminateProcess
    # -1），等它退出的调用方就看到卸载失败（流水线 #9 重跑教训）。
    # 所以第一循环还要按名排除两个阶段的卸载程序。
    check("$p.Name -ne " in code and "unins000.exe" in code
          and "_unins.tmp" in code,
          "停进程按名排除卸载程序两阶段（unins000.exe / _unins.tmp），不杀第一阶段")
    check("CommandLine" in code and "Contains($profile)" in code,
          "工作浏览器只按命令行里的配置目录杀")
    check("Stop-Process -Name" not in code,
          "不按映像名杀进程（Stop-Process -Name 不出现）")
    check("/api/exit" in code, "卸载前先试应用内 /api/exit 完整退出清理")
    check("DeleteUserData" in code and "DeleteUserDataFlag" in code,
          "删用户数据只认 DeleteUserDataFlag（默认保留）")
    check("SuppressibleMsgBox" in code and "IDNO" in code,
          "删用户数据先问用户，静默默认保留（SuppressibleMsgBox 默认 IDNO）")
    check("UserDataExists" in code,
          "没用户数据时不问用户（UserDataExists 守门）")
    check("SuppressibleMsgBox(Notice, mbInformation, MB_OK, IDOK)" in code,
          "残留告知用 SuppressibleMsgBox（静默不弹窗、不挂住）")
    # Inno 的 Create*Page 系列与 WizardSilent 在卸载程序里被禁
    # （sfNoUninstall）：调了卸载直接退 1（流水线 #8 教训：退出码 1
    # = Setup failed to initialize）。CreateInputOptionPage 在
    # InitializeUninstall 里建卸载选项页就是这么死的。
    # 对去掉注释后的文本查：注释里提到名字不算（InitializeUninstall
    # 的注释就写明了这个禁令）。
    code_nocomments = strip_pascal_comments(code)
    for _fname in ("CreateCustomPage", "CreateInputQueryPage",
                   "CreateInputOptionPage", "CreateInputDirPage",
                   "CreateInputFilePage", "CreateOutputMsgPage",
                   "CreateOutputMsgMemoPage", "CreateOutputProgressPage",
                   "CreateOutputMarqueeProgressPage"):
        check(_fname not in code_nocomments, f"卸载程序禁用的 {_fname} 不出现")
    check("WizardSilent" not in code_nocomments,
          "卸载程序禁用的 WizardSilent 不出现")
    check("DeleteDataCheckBox" not in code_nocomments,
          "卸载选项页的 DeleteDataCheckBox 已移除")
    check("GetUserProfile" in code and "GetEnv('USERPROFILE')" in code,
          "GetUserProfile 脚本常量取 %USERPROFILE%（Inno 没有 {userprofile} 常量）")
    # 事件函数原型：对照 Inno 官方文档核对（流水线 #6 教训：
    # InitializeUninstall 写成 procedure 会报 Invalid prototype）。
    check("function InitializeUninstall(): Boolean;" in code,
          "InitializeUninstall 是 function Boolean（不是 procedure）")
    check("procedure InitializeUninstall" not in code,
          "InitializeUninstall 没有写成 procedure")
    m_uninst = re.search(
        r"function InitializeUninstall\(\): Boolean;(.*?)end;", code, re.S
    )
    check(m_uninst is not None and "Result := True" in m_uninst.group(1),
          "InitializeUninstall 返回 True（允许继续卸载）")
    check("procedure InitializeWizard();" in code,
          "InitializeWizard 是 procedure")
    check(
        "procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);"
        in code,
        "CurUninstallStepChanged 原型正确（procedure + TUninstallStep）",
    )
    nested = find_nested_brace_comments(code)
    for p in nested:
        print(f"  注释花括号嵌套：{p}")
    check(not nested, "[Code] 段花括号注释里没有内层花括号（注释不支持嵌套）")
    check("MsgBox" in code and "卸载日志" in code,
          "删不掉的残留记日志并在完成页告知用户")
    check("LogUninstall('进入 usDone。')" in code,
          "usDone 入口记一行日志（下次失败直接证明走到哪）")

    # Python 侧的停进程脚本与 iss 里的文本判定一致（关键子串都在）。
    py_script = installer.uninstall_stop_processes_script()
    for needle in ("param([string]$installDir", "StartsWith($prefix",
                   "Contains($profile)", "ProcessId -ne $excludePid",
                   "Name -ne 'unins000.exe'", "Name -ne '_unins.tmp'",
                   "for ($round = 1", "IndexOf($installDir",
                   "ProcessId -ne $PID", "stuck=$($stuck.Count)",
                   "@('node.exe','cmd.exe','robocopy.exe','powershell.exe')",
                   "Wait-PidsGone", "Test-FileLocked", "Wait-FilesFree",
                   "Write-StopLog", "probe-ok="):
        check(needle in py_script, f"installer.py 停进程脚本含 {needle}")
    # .iss 里 Pascal 拼出来的脚本必须与 installer.py 生成的逐字一致：
    # v41 的脚本是参数化的，不含任何运行时值，不再需要第一行剥离或
    # 占位替换（v40 的那套归一化已删——留着反而会把真正的差异盖住）。
    # Linux 上编不了 Inno，拼错了只能靠这一项发现——v40 之前只核对
    # 子串，拼错引号也发现不了。
    assembled = eval_pascal_stop_script(code)
    check(assembled is not None, "能从 .iss 里求值出停进程脚本")
    if assembled is not None:
        check(
            assembled == py_script,
            ".iss 拼出的停进程脚本与 installer.py 生成的逐字一致",
        )
        if assembled != py_script:
            a_lines = assembled.split("\n")
            p_lines = py_script.split("\n")
            for i, (a, p) in enumerate(zip(a_lines, p_lines)):
                if a != p:
                    print(f"  首个差异在第 {i + 1} 行：")
                    print(f"    .iss: {a!r}")
                    print(f"    .py : {p!r}")
                    break
            if len(a_lines) != len(p_lines):
                print(f"  行数不同：.iss {len(a_lines)} 行，.py {len(p_lines)} 行")
        check(
            all(
                assembled.count(a) == assembled.count(b)
                for a, b in (("{", "}"), ("(", ")"), ("[", "]"))
            ),
            "拼出的停进程脚本括号平衡",
        )

    print()
    if FAILURES:
        print(f"CONSISTENCY: FAILED（{len(FAILURES)} 项不一致）")
        return 1
    print("CONSISTENCY: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
