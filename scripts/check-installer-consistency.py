#!/usr/bin/env python3
r"""核对 Inno 安装脚本与 installer.py 口径一致（v39）。

用法：python3 scripts/check-installer-consistency.py
退出码 0 = 一致；1 = 有不一致（打印哪一项对不上）。

核对项（两处必须同一份口径）：
- 默认安装目录：iss DefaultDirName 必须是 {src}\<INSTALL_SUBDIR>；
- 普通权限安装：PrivilegesRequired=lowest（卸载项进 HKCU）；
- 卸载注册表项：AppId 与 installer.py 的 UNINSTALL_REGISTRY_KEY 一致；
- 桌面快捷方式名与 updater._DESKTOP_SHORTCUT_NAME 同名；
- [UninstallDelete] 覆盖 installer.py 的全部运行产物、用户数据
  （带 Check: DeleteUserData）、浏览器配置目录、%TEMP% 三个前缀；
- 停进程脚本：ExecutablePath 前缀规则 + 排除自己 PID + 工作浏览器
  只按命令行里的配置目录杀，不按映像名杀；
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
    iss = iss_path.read_text(encoding="utf-8")

    check(
        f"DefaultDirName={{src}}\\{installer.INSTALL_SUBDIR}" in iss,
        f"默认安装目录是 {{src}}\\{installer.INSTALL_SUBDIR}",
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
    py_script = installer.uninstall_stop_processes_script(
        "C:\\app", "C:\\Users\\x\\.jianli-lieshou\\browser-profile", 1234
    )
    for needle in ("StartsWith($prefix", "Contains($profile)", "ProcessId -ne 1234",
                   "Name -ne 'unins000.exe'", "Name -ne '_unins.tmp'"):
        check(needle in py_script, f"installer.py 停进程脚本含 {needle}")

    print()
    if FAILURES:
        print(f"CONSISTENCY: FAILED（{len(FAILURES)} 项不一致）")
        return 1
    print("CONSISTENCY: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
