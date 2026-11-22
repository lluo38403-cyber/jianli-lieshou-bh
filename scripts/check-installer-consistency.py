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
    section = iss.split("[UninstallDelete]", 1)[1].split("[Code]", 1)[0]
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
        "{userprofile}\\.jianli-lieshou" in section,
        "浏览器配置目录 {userprofile}\\.jianli-lieshou 在清理清单里",
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
    code = iss.split("[Code]", 1)[1]
    check("ExecutablePath" in code and "StartsWith($prefix" in code,
          "停进程用 ExecutablePath 前缀规则")
    check("GetCurrentProcessId()" in code,
          "停进程排除卸载程序自己的 PID（不自杀）")
    check("CommandLine" in code and "Contains($profile)" in code,
          "工作浏览器只按命令行里的配置目录杀")
    check("Stop-Process -Name" not in code,
          "不按映像名杀进程（Stop-Process -Name 不出现）")
    check("/api/exit" in code, "卸载前先试应用内 /api/exit 完整退出清理")
    check("DeleteUserData" in code and "Checked := False" in code,
          "卸载选项默认保留用户数据（复选框默认不勾选）")
    check("MsgBox" in code and "卸载日志" in code,
          "删不掉的残留记日志并在完成页告知用户")

    # Python 侧的停进程脚本与 iss 里的文本判定一致（关键子串都在）。
    py_script = installer.uninstall_stop_processes_script(
        "C:\\app", "C:\\Users\\x\\.jianli-lieshou\\browser-profile", 1234
    )
    for needle in ("StartsWith($prefix", "Contains($profile)", "ProcessId -ne 1234"):
        check(needle in py_script, f"installer.py 停进程脚本含 {needle}")

    print()
    if FAILURES:
        print(f"CONSISTENCY: FAILED（{len(FAILURES)} 项不一致）")
        return 1
    print("CONSISTENCY: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
