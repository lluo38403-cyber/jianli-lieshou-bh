"""简历猎手 · 安装 / 卸载口径（v40，1.1.26）。

这一模块是安装程序与卸载程序的**唯一口径来源**：Inno Setup 脚本
（installer/jianli-lieshou.iss）里的规则必须与这里逐项一致，对应关系
见 scripts/check-installer-consistency.py（verify.sh 会跑它）。

只放纯函数：不读注册表、不杀进程、不删文件——调用方（Inno 脚本、
测试）按返回的清单行事。Windows 真机行为（安装向导、注册表写入、
进程停止、实际删除）在 Linux CI 里测不了，见本文件末尾的界限说明。

已核实的写盘位置（删之前逐项对过源码，不许凭想象扩清单）：
- 安装目录内：config.yaml；data/bosshunter.db（server.py:229 DATA_DIR）、
  data/resumes/（config.py:155 默认 resume_output_dir）、data/logs/
  （launcher.py 日志轮换）、data/update-cache/ + update-ready.json
  （updater.py:1489 UPDATE_CACHE_DIRNAME/MANIFEST）、data/.instance.json
  与 data/launched-browser.json（browser/runtime/__init__.py:42-43）、
  data/window-geometry.json（server.py:5111）、data/.startup-failed.json
  （server.py:4747）、data/desktop_shortcut_created（updater.py:1797）、
  data/.star_hint_<版本>（main.py:40）、data/auth_cache.json
  （server.py:1772 传给 auth/client.py 的 cache_path，24 小时离线宽限
  缓存，应用进程内持有句柄）与 data/cities.cache.json
  （server.py:4327 调 refresh_city_cache 写的城市列表缓存，
  cities.py:218 原子替换写）——后两个是缓存文件，不属于用户数据，
  卸载时一律删；安装目录根：update.bat、
  update.log、update-pending.json、update-result.json、
  update-backup-<版本>/（updater.py:76-78、460、510）。
- 安装目录外：%USERPROFILE%\\.jianli-lieshou\\browser-profile（工作浏览器
  专用配置目录，browser/runtime/__init__.py:388 get_browser_profile_dir，
  登录态存在里面）；%TEMP% 下 bosshunter_resume.html 与
  bosshunter_resume_*.html（ai/resume.py:916/963，用完即删，残留才清）、
  bosshunter-browser-*.lock（browser/ownership.py:35）、jianli-update-*
  临时目录（updater.py:1456/1645、server.py:1476）。
- 只读不写、卸载时不碰：%AppData% / %LocalAppData% 下找 Chrome/Edge
  可执行文件只是发现逻辑（browser/runtime/__init__.py:202、
  diagnostics.py:318），我方代码没有往那里写东西——v40 又全仓库
  grep 核实过一遍（APPDATA、LOCALAPPDATA、ExpandEnvironmentStrings、
  getenv('APPDATA')、os.environ 里的相关键）：除上述两处只读发现外
  无任何引用；工作浏览器用 --user-data-dir 指到
  %USERPROFILE%\\.jianli-lieshou\\browser-profile（runtime/__init__.py:520），
  配置数据写在那里，不写 Chrome/Edge 自身的 %LocalAppData% 目录。
  所以卸载清单里仍然不碰 %AppData% / %LocalAppData%，不要凭空编造
  删除路径。
- config.py:703 与 cities.py:201 的 NamedTemporaryFile 都带
  dir=path.parent（原子写同目录临时文件，写完 os.replace，失败才残留），
  不在 %TEMP% 里——残留形态是安装目录内的 .<文件名>.*.tmp，卸载时随
  安装目录整体删除，不单列。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "简历猎手"
APP_EXE_NAME = "简历猎手.exe"
APP_VERSION = "1.1.26"
INSTALL_SUBDIR = "jianpinglieshou"
DESKTOP_SHORTCUT_NAME = "简历猎手.lnk"  # 与 updater._DESKTOP_SHORTCUT_NAME 同名
START_MENU_GROUP = "简历猎手"

# Inno AppId：卸载注册表项就是这个名字 + "_is1"。
APP_ID = "简历猎手"
UNINSTALL_REGISTRY_KEY = (
	"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\简历猎手_is1"
)
UNINSTALL_REGISTRY_VALUES = (
	"DisplayName",
	"DisplayVersion",
	"InstallLocation",
	"UninstallString",
	"Publisher",
	"DisplayIcon",
)

# 工作浏览器配置目录在用户主目录下的相对位置。
BROWSER_PROFILE_RELATIVE = Path(".jianli-lieshou") / "browser-profile"

# %TEMP% 里属于本程序的前缀（只清这三个，绝不碰别的程序的临时文件）。
TEMP_PREFIXES = ("bosshunter_resume", "bosshunter-browser-", "jianli-update-")

# 安装目录内“用户数据”：默认保留，卸载向导打勾才删。
USER_DATA_RELATIVE = (
	Path("config.yaml"),
	Path("data") / "bosshunter.db",
	Path("data") / "resumes",
)

# 安装目录内“程序运行产物”：卸载时一律删（与 keep_user_data 无关）。
_RUNTIME_RELATIVE = (
	Path("data") / "logs",
	Path("data") / "update-cache",
	Path("data") / ".instance.json",
	Path("data") / "launched-browser.json",
	Path("data") / "window-geometry.json",
	Path("data") / ".startup-failed.json",
	Path("data") / "desktop_shortcut_created",
	Path("data") / "auth_cache.json",
	Path("data") / "cities.cache.json",
	Path("update.bat"),
	Path("update.log"),
	Path("update-pending.json"),
	Path("update-result.json"),
)

_UPDATE_BACKUP_PREFIX = "update-backup-"
_STAR_HINT_PREFIX = ".star_hint_"


# ─── 路径计算 ────────────────────────────────────────────────────────────

def default_install_dir(installer_dir: str | Path) -> Path:
	"""默认安装目录 = 安装包所在目录 + jianpinglieshou。

	installer_dir 是安装程序（安装包）所在目录，对应 Inno 的 {src}。
	"""
	if installer_dir is None or not str(installer_dir).strip():
		raise ValueError("安装包所在目录为空，算不出默认安装目录")
	return Path(installer_dir) / INSTALL_SUBDIR


def _split_parts(text: str) -> list[str]:
	"""按两种分隔符切路径，供跨平台的安全判定用（Linux 上也要能判 Windows 路径）。"""
	return [p for p in re.split(r"[\\/]+", text.strip()) if p not in ("", ".")]


def unsafe_reason(path: str | Path, *, home: str | Path | None = None,
		tempdir: str | Path | None = None) -> str | None:
	"""路径不适合做安装/卸载目标时返回原因，适合返回 None。

	红线（任一命中即拒绝）：
	- 空路径 / 只有空白 / "."；
	- 文件系统根（C:\\、/）——删它等于删盘；
	- Windows 目录及其子目录（C:\\Windows、System32、SysWOW64…）；
	- 用户主目录本身、桌面本身、系统临时目录本身或其子目录——
	  装进去会散落文件、卸起来会误伤别家东西；
	- 路径里含 NUL（传不进 Windows API，必是坏输入）。
	注意：Program Files 这类标准安装位置是允许的；用户亲手选的普通
	目录（哪怕是安装包所在目录本身）也允许，只是不再强行追加一层
	子文件夹。
	"""
	text = "" if path is None else str(path)
	if not text.strip() or "\x00" in text:
		return "路径为空或含非法字符"
	parts = _split_parts(text)
	if not parts:
		return "路径为空"
	# 盘符根（C:）或 POSIX 根（/）。
	if re.fullmatch(r"[A-Za-z]:", parts[0]) and len(parts) == 1:
		return "不能装到盘符根目录"
	if text.strip().startswith(("/", "\\")) and len(parts) == 0:
		return "不能装到文件系统根目录"
	if text.strip() in ("/", "\\"):
		return "不能装到文件系统根目录"
	# Windows 目录：盘符后的第一段是 Windows，或任何一段是 System32/SysWOW64。
	lowered = [p.lower() for p in parts]
	if len(parts) >= 2 and re.fullmatch(r"[A-Za-z]:", parts[0]) and lowered[1] == "windows":
		return "不能装到 Windows 系统目录里"
	if "system32" in lowered or "syswow64" in lowered:
		return "不能装到 Windows 系统目录里"
	if lowered[0] == "windows":
		return "不能装到 Windows 系统目录里"
	for base, label in ((home, "用户主目录"), (tempdir, "系统临时目录")):
		if base is None:
			continue
		base_parts = _split_parts(str(base))
		if not base_parts:
			continue
		if lowered == [p.lower() for p in base_parts]:
			return f"不能装到{label}本身"
		if len(lowered) > len(base_parts) and lowered[: len(base_parts)] == [
			p.lower() for p in base_parts
		]:
			# 在临时目录下任何位置都不行（临时目录随时可能被系统清掉，
			# 装进去不可靠）；主目录下建子目录是允许的，只拦主目录本身。
			if label == "系统临时目录":
				return "不能装到系统临时目录里"
	return None


def is_safe_install_dir(path: str | Path, *, home: str | Path | None = None,
		tempdir: str | Path | None = None) -> bool:
	"""unsafe_reason 的布尔版。"""
	return unsafe_reason(path, home=home, tempdir=tempdir) is None


def resolve_target_dir(user_choice: str | Path | None,
		installer_dir: str | Path) -> Path:
	"""算出最终安装目录。

	- 用户没选（空/None）→ 默认安装目录（安装包所在目录 + jianpinglieshou）。
	- 用户选了 → 以用户所选为准，不再强行追加一层 jianpinglieshou；
	  相对路径按安装包所在目录解释；不安全的路径直接报 ValueError，
	  安装向导必须拦住、不让继续。
	"""
	if user_choice is None or not str(user_choice).strip():
		return default_install_dir(installer_dir)
	chosen = Path(str(user_choice).strip()).expanduser()
	if not chosen.is_absolute():
		chosen = Path(installer_dir) / chosen
	# 先按用户原样输的路径判一次（Linux 上跑单测时 Windows 风格的
	# "C:\\Windows" 会被当成相对路径，先判 raw 才能拦住它）；
	# 再按解析后的最终路径判一次。
	for candidate in (str(user_choice).strip(), chosen):
		reason = unsafe_reason(candidate)
		if reason is not None:
			raise ValueError(f"安装目录不安全：{reason}：{chosen}")
	return chosen


def browser_profile_dir(home: str | Path) -> Path:
	"""工作浏览器配置目录：%USERPROFILE%\\.jianli-lieshou\\browser-profile。"""
	return Path(home) / BROWSER_PROFILE_RELATIVE


# ─── 卸载清理清单 ────────────────────────────────────────────────────────

@dataclass
class UninstallPlan:
	"""卸载时要删 / 要留的清单（只含本程序的路径）。

	remove_paths：无条件删除（文件直接删，目录递归删）。
	remove_if_empty：删完上面那些后、如果空了才删（留给保留下来的
	  用户数据让位：keep_user_data=True 时安装目录与 data/ 只要还
	  留着用户数据就不删）。
	keep_paths：keep_user_data=True 时保留的用户数据路径。
	install_dir_removable：keep_user_data=False 时整个安装目录可删，
	  为 True；保留用户数据时只有在没有任何用户数据文件时才为 True。
	temp_matches：%TEMP% 里实际扫到的、属于本程序的文件/目录
	  （remove_paths 的子集，单列出来方便写进卸载日志）。
	"""
	install_dir: Path
	remove_paths: list[Path] = field(default_factory=list)
	remove_if_empty: list[Path] = field(default_factory=list)
	keep_paths: list[Path] = field(default_factory=list)
	install_dir_removable: bool = False
	temp_matches: list[Path] = field(default_factory=list)


def _scan_tempdir(tempdir: str | Path) -> list[Path]:
	"""扫 %TEMP% 顶层，只认属于本程序的三个前缀；别的原样不动。

	tempdir 不是目录、是盘符根或扫描失败时返回空清单——宁可漏清，
	不许误删。只扫顶层：三个来源（resume html、lock、jianli-update-）
	都在 %TEMP% 顶层建，不递归进子目录。
	"""
	base = Path(tempdir)
	try:
		if not base.is_dir():
			return []
		if str(base) in ("/", "\\") or re.fullmatch(r"[A-Za-z]:[\\/]?", str(base)):
			return []
		entries = list(base.iterdir())
	except OSError:
		return []
	matched: list[Path] = []
	for entry in entries:
		name = entry.name
		if name.startswith("bosshunter_resume"):
			matched.append(entry)
		elif name.startswith("bosshunter-browser-") and name.endswith(".lock"):
			matched.append(entry)
		elif name.startswith("jianli-update-"):
			matched.append(entry)
	return matched


def uninstall_cleanup_paths(install_dir: str | Path, home: str | Path,
		tempdir: str | Path, keep_user_data: bool = True) -> UninstallPlan:
	"""算出卸载清理清单。

	install_dir 必须通过安全判定——万一记录的安装目录是系统目录
	（或空路径），直接报 ValueError，卸载程序不许往下删。
	keep_user_data=True（默认）：保留 config.yaml、data/bosshunter.db、
	data/resumes/，其余程序文件与运行产物全删；
	keep_user_data=False（向导里打勾）：安装目录整体删。
	"""
	install = Path(install_dir)
	reason = unsafe_reason(install)
	if reason is not None:
		raise ValueError(f"卸载目标不安全：{reason}：{install}")
	plan = UninstallPlan(install_dir=install)
	# 1. 安装目录内的运行产物：一律删。
	for rel in _RUNTIME_RELATIVE:
		plan.remove_paths.append(install / rel)
	# update-backup-* 目录与 .star_hint_* 标记文件：程序生成的，按前缀扫。
	try:
		for entry in install.iterdir():
			if entry.name.startswith(_UPDATE_BACKUP_PREFIX):
				plan.remove_paths.append(entry)
		data_dir = install / "data"
		if data_dir.is_dir():
			for entry in data_dir.iterdir():
				if entry.name.startswith(_STAR_HINT_PREFIX):
					plan.remove_paths.append(entry)
	except OSError:
		pass
	# 2. 用户数据：保留或删。
	user_data = [install / rel for rel in USER_DATA_RELATIVE]
	if keep_user_data:
		plan.keep_paths = user_data
		plan.install_dir_removable = not any(p.exists() for p in user_data)
		plan.remove_if_empty = [install / "data", install]
	else:
		plan.remove_paths.extend(user_data)
		plan.install_dir_removable = True
		plan.remove_if_empty = []
	# 3. 安装目录外：工作浏览器配置目录整个删（登录态在里面，卸载即清）。
	profile = browser_profile_dir(home)
	plan.remove_paths.append(profile)
	plan.remove_if_empty.append(profile.parent)  # .jianli-lieshou：空了才删
	# 4. %TEMP% 里属于本程序的残留。
	plan.temp_matches = _scan_tempdir(tempdir)
	plan.remove_paths.extend(plan.temp_matches)
	return plan


# ─── 卸载前停进程（复用已有退出清理的判定） ─────────────────────────────

def _ps_quote(value: str) -> str:
	"""包进 PowerShell 单引号字符串：单引号本身翻倍。"""
	return "'" + value.replace("'", "''") + "'"


def uninstall_stop_processes_script(install_dir: str | Path,
		browser_profile_dir_path: str | Path,
		exclude_pid: int | None = None) -> str:
	"""卸载前停进程的 PowerShell 脚本（文本，供 Inno 脚本原样执行）。

	红线（与 updater._install_dir_kill_script 同款，不许改）：
	只认 ExecutablePath 前缀（安装目录 + '\\'，目录边界在内，兄弟目录
	不误杀），exclude_pid 跳过卸载程序自己（它自己也在安装目录里，
	不排除会自杀）；绝不按映像名杀 node.exe / chrome.exe（v28 教训：
	会误杀用户浏览器和其他 node）——下面所有杀操作都必须同时有
	“路径证据”（可执行路径在安装目录下，或命令行里带安装目录 /
	本程序浏览器配置目录），光凭进程名绝不动手。
	另外必须按名排除卸载程序本身的两个阶段：Inno 卸载是两阶段的，
	第一阶段 unins000.exe 在安装目录里、第二阶段 _unins.tmp 在 %TEMP%
	里；exclude_pid 只排除当前阶段的 PID，第二阶段跑脚本时第一阶段
	的 PID 不在排除里，只靠 PID 排除会把第一阶段杀掉——被杀的第一
	阶段退出码是 -1（TerminateProcess -1），等它退出的调用方就看到
	卸载失败（流水线 #9 重跑教训）。所以循环 1、2 还要加
	$p.Name -ne 'unins000.exe' 与 $p.Name -ne '_unins.tmp'。

	三轮循环（v40，1.1.26；v39 只有单遍、无复核，进程残留就直接删，
	撞上占用就报残留）：
	- 循环 1：可执行路径在安装目录下——主程序 简历猎手.exe、
	  patchright 驱动 node.exe（_internal\\patchright\\driver\\node.exe）
	  等打包进来的进程。
	- 循环 2：命令行里带安装目录——可执行路径在安装目录外、循环 1
	  够不着的进程，只认 node.exe / cmd.exe / robocopy.exe /
	  powershell.exe 这四个名字：系统 node 跑的 cdp-proxy
	  （runtime/__init__.py：ensure_runtime 用 check_node_available
	  找到的第一个可用 node，常是 C:\\Program Files\\nodejs\\node.exe，
	  命令行是 node <安装目录>\\_internal\\...\\cdp-proxy.mjs）；
	  跑 update.bat 的 cmd.exe（可执行路径在 System32 里）；更新
	  时的 robocopy.exe；bat 拉起的 powershell.exe。ExecutablePath
	  读不到的进程（例如以管理员身份跑的应用、卸载程序是普通权限）
	  也靠这一轮兜底——但注意非管理员的卸载程序本来就杀不掉管理员
	  进程，那种情况靠前面的 POST /api/exit 让应用自己退出。
	  循环 2 还排除 $PID（扫尾脚本自己的 powershell.exe；万一安装
	  目录是 %TEMP% 的祖先目录，脚本自己的命令行就会带上安装目录，
	  不排除会自杀）。
	- 循环 3：工作浏览器——它拉的是系统 Chrome/Edge，可执行路径
	  不在安装目录里，只杀命令行里带本程序浏览器配置目录
	  （%USERPROFILE%\\.jianli-lieshou\\browser-profile）的 chrome.exe /
	  msedge.exe 进程，用户的日常浏览器命令行里没有这个目录，不会误杀。
	三轮循环最多跑 3 遍，每遍之间睡 2 秒；一遍扫不到目标就提前停。
	最后再扫一遍核实：还活着的按名 + PID 记进 stuck-procs（不拦
	卸载，只写日志供诊断）。
	另外，卸载程序应先试 POST http://127.0.0.1:8686/api/exit（走应用内
	完整的 _perform_exit_cleanup：停任务 → 停 runtime/Chrome → 扫尾），
	调不通或等不到退出再跑这段脚本扫尾——脚本本身只是第二道防线。
	输出 killed=<数> failed=<数> browser=<数> stuck=<数>，调用方写进
	卸载日志；stuck>0 时再出一行 stuck-procs: <名 PID; …>。
	新脚本只用 ASCII 字面量（进程名、路径变量全是 ASCII）：Inno 的
	SaveStringToFile 写临时 .ps1 时非 ASCII 可能走系统代码页，脚本里
	不写中文就没这个风险。
	"""
	install = str(Path(install_dir))
	profile = str(Path(browser_profile_dir_path))
	exclude = f" -and $p.ProcessId -ne {int(exclude_pid)}" if exclude_pid is not None else ""
	# 循环 1 的条件（前缀规则 + 排除自己与卸载程序两阶段）。
	loop1 = (
		"$p.ExecutablePath -and $p.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)"
		+ exclude
		+ " -and $p.Name -ne 'unins000.exe' -and $p.Name -ne '_unins.tmp'"
	)
	# 循环 2 的条件（命令行里带安装目录，只认四个名字 + 排除自己）。
	loop2 = (
		"@('node.exe','cmd.exe','robocopy.exe','powershell.exe') -contains $p.Name"
		" -and $p.CommandLine -and $p.CommandLine.IndexOf($installDir, [System.StringComparison]::OrdinalIgnoreCase) -ge 0"
		+ exclude
		+ " -and $p.ProcessId -ne $PID -and $p.Name -ne 'unins000.exe' -and $p.Name -ne '_unins.tmp'"
	)
	# 循环 3 的条件（工作浏览器：命令行里带本程序配置目录）。
	loop3 = (
		"($p.Name -eq 'chrome.exe' -or $p.Name -eq 'msedge.exe')"
		" -and $p.CommandLine -and $p.CommandLine.Contains($profile)"
	)
	return (
		f"$installDir = {_ps_quote(install)}; "
		"$prefix = $installDir + '\\'; "
		f"$profile = {_ps_quote(profile)}; "
		"$killed = 0; $failed = 0; $browser = 0; "
		"for ($pass = 1; $pass -le 3; $pass++) { "
		"$found = 0; "
		"foreach ($p in Get-CimInstance Win32_Process) { "
		"if (" + loop1 + ") "
		"{ $found += 1; try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $killed += 1 } catch { $failed += 1 } } }; "
		"foreach ($p in Get-CimInstance Win32_Process) { "
		"if (" + loop2 + ") "
		"{ $found += 1; try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $killed += 1 } catch { $failed += 1 } } }; "
		"foreach ($p in Get-CimInstance Win32_Process) { "
		"if (" + loop3 + ") "
		"{ $found += 1; try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $browser += 1 } catch { $failed += 1 } } }; "
		"if ($found -eq 0) { break }; "
		"Start-Sleep -Seconds 2 }; "
		"$stuck = @(); "
		"foreach ($p in Get-CimInstance Win32_Process) { "
		"if ((" + loop1 + ") -or (" + loop2 + ") -or (" + loop3 + ")) "
		"{ $stuck += \"$($p.Name) PID $($p.ProcessId)\" } }; "
		"Write-Output \"killed=$killed failed=$failed browser=$browser stuck=$($stuck.Count)\"; "
		"if ($stuck.Count -gt 0) { Write-Output (\"stuck-procs: \" + ($stuck -join '; ')) }"
	)

# ─── 卸载残留上报 ────────────────────────────────────────────────────────

def format_leftover_notice(leftovers: list[str], log_path: str | Path) -> str:
	"""卸载完成页的残留告知文案：删不掉的残留不静默跳过，如实告诉用户。

	leftovers 是删不掉的残留路径（字符串）；为空返回 ""，调用方不弹提示。
	"""
	if not leftovers:
		return ""
	lines = [
		"以下文件或文件夹正在被占用，未能删除（已记入卸载日志）：",
		*[f"  - {item}" for item in leftovers],
		"请关闭占用它们的程序后手动删除，或重启电脑后由系统删除。",
		f"卸载日志：{log_path}",
	]
	return "\n".join(lines)


# ─── Windows 真机界限（Linux CI 测不了的部分） ──────────────────────────
# 以下行为只在 Windows 真机上发生，本模块与单元测试覆盖不到，
# 留给 lucky 在无影云电脑上实测（见改动清单 XLIII「未验证」）：
# - Inno 安装向导：默认路径是不是 {src}\\jianpinglieshou、向导说明
#   文案是否清晰无乱码、改目录后不再追加子文件夹、注册表卸载项与
#   开始菜单/桌面快捷方式、装完启动；
# - 卸载向导：先停进程再删文件、用户数据保留选项默认保留、残留上报；
# - 注册表项与快捷方式在卸载后是否干净、缓存目录是否真的清空。
