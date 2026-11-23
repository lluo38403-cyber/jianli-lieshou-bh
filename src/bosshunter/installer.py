"""简历猎手 · 安装 / 卸载口径（v46，1.1.32）。

这一模块是安装程序与卸载程序的**唯一口径来源**：Inno Setup 脚本
（installer/jianli-lieshou.iss）里的规则必须与这里逐项一致，对应关系
见 scripts/check-installer-consistency.py（verify.sh 会跑它）。

只放纯函数：不读注册表、不杀进程、不删文件——调用方（Inno 脚本、
测试）按返回的清单行事。Windows 真机行为（安装向导、注册表写入、
进程停止、实际删除）在 Linux CI 里测不了，见本文件末尾的界限说明。

已核实的写盘位置（删之前逐项对过源码，不许凭想象扩清单）：
- 安装目录内：config.yaml；data/bosshunter.db（server.py:229 DATA_DIR）
  与它的 WAL 伴生文件 data/bosshunter.db-wal / data/bosshunter.db-shm
  （db.py:103 PRAGMA journal_mode=WAL；伴生文件与主库同进退：保留用户
  数据时一起留、删用户数据时一起删，单删 -wal 会丢掉未落盘的事务）；
  data/resumes/（config.py:155 默认 resume_output_dir）、data/logs/
  （launcher.py 日志轮换）、data/update-cache/ + update-ready.json
  （updater.py:1489 UPDATE_CACHE_DIRNAME/MANIFEST）、data/.instance.json
  与 data/launched-browser.json（browser/runtime/__init__.py:42-43）、
  data/window-geometry.json（server.py:5111）、data/.startup-failed.json
  （server.py:4747）、data/desktop_shortcut_created（updater.py:1797）与
  data/desktop_shortcut_declined（v41：安装时用户不勾选桌面快捷方式，
  安装程序写这个 marker，程序启动时 ensure_desktop_shortcut 见到它
  就不再补建——否则勾选框等于没生效）、data/auth_cache.json
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
APP_VERSION = "1.1.32"
INSTALL_SUBDIR = "简历猎手"
DESKTOP_SHORTCUT_NAME = "简历猎手.lnk"  # 与 updater._DESKTOP_SHORTCUT_NAME 同名
# 安装时用户不勾选“创建桌面快捷方式”，安装程序写这个 marker；
# 与 updater.DESKTOP_SHORTCUT_DECLINED_MARKER_FILENAME 同名。
DESKTOP_SHORTCUT_DECLINED_MARKER_FILENAME = "desktop_shortcut_declined"
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
# bosshunter.db-wal / .db-shm 是 WAL 模式的伴生文件，与主库同进退。
USER_DATA_RELATIVE = (
	Path("config.yaml"),
	Path("data") / "bosshunter.db",
	Path("data") / "bosshunter.db-wal",
	Path("data") / "bosshunter.db-shm",
	Path("data") / "resumes",
)

# 安装目录内“空了才删的目录”：卸载删完文件后目录空了才删，里面还
# 留着用户数据时不动。对应 Inno [UninstallDelete] 里的
# Type: dirifempty 条目。data/ 是唯一一个：安装程序在 ssPostInstall
# 写桌面快捷方式 marker 时建 data/（.iss CurStepChanged），卸载删
# 完里面的文件后它就空了；Inno 不会自动清掉变空的子目录（只自动
# 移除空的 {app} 本身），不加这条空 data/ 会留下、安装目录删不
# 干净（流水线 #22 冒烟教训：冒烟从不运行 exe，data/ 里没有任何
# 用户数据，只剩空目录）。绝不要改成 filesandordirs 删整个 data/——
# 那会在用户选“保留用户数据”时也删掉 db 与 resumes。
REMOVE_IF_EMPTY_RELATIVE = (Path("data"),)

# 安装目录内“程序运行产物”：卸载时一律删（与 keep_user_data 无关）。
_RUNTIME_RELATIVE = (
	Path("data") / "logs",
	Path("data") / "update-cache",
	Path("data") / ".instance.json",
	Path("data") / "launched-browser.json",
	Path("data") / "window-geometry.json",
	Path("data") / ".startup-failed.json",
	Path("data") / "desktop_shortcut_created",
	Path("data") / "desktop_shortcut_declined",
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
	"""默认安装目录 = 安装包所在目录 + 简历猎手。

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

	- 用户没选（空/None）→ 默认安装目录（安装包所在目录 + 简历猎手）。
	- 用户选了 → 以用户所选为准，不再强行追加一层简历猎手；
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
		plan.remove_if_empty = [
			install / rel for rel in REMOVE_IF_EMPTY_RELATIVE
		] + [install]
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

def uninstall_stop_processes_script() -> str:
	"""卸载前停进程的 PowerShell 脚本（文本，供 Inno 脚本原样执行）。

	参数化脚本：安装目录、浏览器配置目录、卸载日志路径、要排除的
	PID、exe 名都不写进脚本文件，由 Inno 在命令行上传参
	（-InstallDir / -ProfileDir / -LogPath / -ExcludePid / -ExeName），
	所以脚本全文只用 ASCII——Inno 的 SaveStringToFile 写临时 .ps1
	时非 ASCII 可能走系统代码页，脚本里不写中文就没这个风险；
	命令行是 Unicode，中文路径/文件名传参没问题。

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

	三轮判定（与 v40 相同，v41 只改“杀完之后怎么办”）：
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

	v41 为什么改（v40 卸载仍报 data / bosshunter.db 被占用，根因已核实）：
	1. /api/exit 是异步的（server.py:1731：先回包，后台线程 1.5 秒后
	   才开始清理；_stop_active_tasks 最多 3 轮 × wait 5 秒 +
	   shutdown_browser_resources 最多 10+10 秒，优雅退出要 2 秒到
	   35 秒以上），v40 只在 POST 返回后固定睡 4 秒——应用还在
	   关 Chrome、停任务，扫尾就已经跑完了。
	2. v40 杀完只固定睡 2 秒、扫不到就提前停，从不等被杀的进程真正
	   退出；Stop-Process -Force 只发终止请求就返回，杀失败
	   （例如目标是管理员进程、卸载程序没权限）只记 failed，不重试、
	   不等待、不拦删除——删除直接撞上还没死的进程。
	3. Get-CimInstance 对管理员进程返回空的 ExecutablePath /
	   CommandLine（卸载程序是普通权限，PrivilegesRequired=lowest）：
	   三个循环全都看不见提权跑的主程序，$found=0 就提前停——
	   “没扫到”被当成“没在跑”，而应用其实还活着、还拿着数据库。
	4. v40 扫尾的输出（killed/stuck-procs）只打到 powershell 标准
	   输出，Inno 的 Exec 不收 stdout——卸载日志里只剩一个退出码，
	   扫尾到底跑没跑、杀没杀掉、谁还活着，全都不可见。
	所以 v41 改成“杀 + 等真退出 + 探句柄 + 写日志”：
	- 每轮杀完轮询等 PID 真正消失（500ms 一轮，最多 10 秒），
	  有残留就再扫一轮（最多 3 轮，覆盖扫尾后又被拉起来的进程，
	  例如更新中的 update.bat 重拉 exe）；
	- 杀完探句柄：独占打开 {app}\\简历猎手.exe 与
	  {app}\\data\\bosshunter.db（[IO.File]::Open ReadWrite + None，
	  文件不存在算不占用），5 秒内没释放就再 POST 一次 /api/exit
	  等 60 秒（v42：30 秒不够，见下）；
	- 还占着时走最后一招：按产品自己的 exe 名（简历猎手.exe，只
	  有我方产品用这个名字）枚举 PID、按 PID 杀、再等 10 秒、再探
	  5 秒——只在句柄探针已证明“我方文件被占着”、且优雅退出已试
	  过两轮的前提下才动手，不是 broad 按映像名杀；
	- 全程写进卸载日志（Write-StopLog，ASCII 行）：
	  stop-sweep: killed=N failed=N stuck=N probe-ok=True/False，
	  有残留再加一行 stuck-procs: <PID; …>。
	正常情况（应用没跑或很快退出）探针第一下就通过，整个扫尾
	1–2 秒结束；只有真卡住时才走到后面的有界等待，最长约 110 秒。

	v42 为什么又改（v41 上线后真机仍报 data / bosshunter.db 被占用，
	截图无法区分卸的是 v40（1.1.26）还是 v41（1.1.27），两版都要
	考虑到；卸载日志用户还没发来，先做静态核查）：
	- 调用链已核对，没问题：扫尾在 usUninstall 里、删文件之前跑，
	  powershell 退出码记进卸载日志；/api/exit 是应用内优雅退出，
	  先回包、后台线程 1.5 秒后才开始清理（web/server.py api_exit）。
	- 优雅退出最长路径已核实：停任务最多 3 轮 × 5 秒 + 关 runtime /
	  Chrome 各 10 秒 + 应用自己的安装目录扫尾（subprocess 超时
	  30 秒），约 66 秒；v41 第二次 POST 后只等 30 秒（第一次
	  POST 起总共约 35 秒），提权跑的主程序在 WMI 里看不见、只能
	  靠优雅退出 + 探针时，探针可能比应用退出早到——改成等 60 秒
	  （第一次 POST 起约 65 秒，最后一招后再探 5 秒，整条链最晚
	  约 75 秒才下结论），覆盖常见最坏（约 40 秒）并留有余量。
	- 提权进程的最后一招也可能 Access Denied：普通权限的卸载程序
	  杀不掉管理员进程（Stop-Process 抛错只记 failed），v41 没记
	  是哪些 PID 失败、两次 /api/exit 的结果也没记——下次再报占用
	  时日志里看不出原因。v42 把关键节点都写进卸载日志：脚本开始
	  （stop-sweep: start）、两次 api-exit 的 ok/failed、最后一招
	  枚举到的 PID（byname:）、杀失败的 PID（failed-procs:）。
	  以管理员身份重跑卸载程序这个后手先不做：要不要弹 UAC、提权
	  后重跑的流程，等用户那份卸载日志来了、确认真是提权问题再定，
	  不猜。另：XLIV 里“提权启动再卸载同样不再报被占用（WMI 盲区
	  由最后一招兜住）”说过了头——最后一招只能兜住 Stop-Process
	  能杀掉的情况，提权进程它杀不掉，真正兜住提权的是 /api/exit
	  + 探针等待，这里一并纠正。
	"""
	lines = [
		"param([string]$installDir, [string]$profile, [string]$logPath, [int]$excludePid, [string]$exeName)",
		"function Write-StopLog([string]$text) { try { Add-Content -Path $logPath -Value $text -Encoding Ascii -ErrorAction Stop } catch { } }",
		"$prefix = $installDir + '\\'",
		"$exePath = Join-Path $installDir $exeName",
		"$dbPath = Join-Path $installDir 'data\\bosshunter.db'",
		"function Get-TargetPids {",
		"  $procs = @(Get-CimInstance Win32_Process)",
		"  $ids = @()",
		"  foreach ($p in $procs) { if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -and $p.ProcessId -ne $excludePid -and $p.Name -ne 'unins000.exe' -and $p.Name -ne '_unins.tmp') { $ids += $p.ProcessId } }",
		"  foreach ($p in $procs) { if (@('node.exe','cmd.exe','robocopy.exe','powershell.exe') -contains $p.Name -and $p.CommandLine -and $p.CommandLine.IndexOf($installDir, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and $p.ProcessId -ne $excludePid -and $p.ProcessId -ne $PID -and $p.Name -ne 'unins000.exe' -and $p.Name -ne '_unins.tmp') { $ids += $p.ProcessId } }",
		"  foreach ($p in $procs) { if (($p.Name -eq 'chrome.exe' -or $p.Name -eq 'msedge.exe') -and $p.CommandLine -and $p.CommandLine.Contains($profile)) { $ids += $p.ProcessId } }",
		"  return ($ids | Select-Object -Unique)",
		"}",
		"function Wait-PidsGone($pids, [int]$seconds) {",
		"  $deadline = (Get-Date).AddSeconds($seconds)",
		"  while ((Get-Date) -lt $deadline) {",
		"    $alive = @()",
		"    foreach ($id in $pids) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $alive += $id } }",
		"    if ($alive.Count -eq 0) { return @() }",
		"    Start-Sleep -Milliseconds 500",
		"  }",
		"  $alive = @()",
		"  foreach ($id in $pids) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $alive += $id } }",
		"  return $alive",
		"}",
		"function Test-FileLocked([string]$path) {",
		"  if (-not (Test-Path $path)) { return $false }",
		"  try { $s = [IO.File]::Open($path, 'Open', 'ReadWrite', 'None'); $s.Close(); return $false } catch { return $true }",
		"}",
		"function Wait-FilesFree([int]$seconds) {",
		"  $deadline = (Get-Date).AddSeconds($seconds)",
		"  while ((Get-Date) -lt $deadline) {",
		"    if (-not (Test-FileLocked $exePath) -and -not (Test-FileLocked $dbPath)) { return $true }",
		"    Start-Sleep -Milliseconds 1000",
		"  }",
		"  return ((-not (Test-FileLocked $exePath)) -and (-not (Test-FileLocked $dbPath)))",
		"}",
		"Write-StopLog \"stop-sweep: start\"",
		"try { Invoke-WebRequest -Uri 'http://127.0.0.1:8686/api/exit' -Method Post -TimeoutSec 3 | Out-Null; Write-StopLog \"api-exit: ok\" } catch { Write-StopLog \"api-exit: failed\" }",
		"$killed = 0; $failed = 0; $failedIds = @()",
		"for ($round = 1; $round -le 3; $round++) {",
		"  $targets = @(Get-TargetPids)",
		"  if ($targets.Count -eq 0) { break }",
		"  foreach ($id in $targets) { try { Stop-Process -Id $id -Force -ErrorAction Stop; $killed += 1 } catch { $failed += 1; $failedIds += $id } }",
		"  $still = @(Wait-PidsGone $targets 10)",
		"  if ($still.Count -eq 0) { break }",
		"}",
		"$probeOk = Wait-FilesFree 5",
		"if (-not $probeOk) {",
		"  try { Invoke-WebRequest -Uri 'http://127.0.0.1:8686/api/exit' -Method Post -TimeoutSec 3 | Out-Null; Write-StopLog \"api-exit: ok\" } catch { Write-StopLog \"api-exit: failed\" }",
		"  $probeOk = Wait-FilesFree 60",
		"}",
		"if (-not $probeOk) {",
		"  $byName = @(Get-CimInstance Win32_Process -Filter \"Name = '$exeName'\" | ForEach-Object { $_.ProcessId })",
		"  $byName = @($byName | Where-Object { $_ -ne $excludePid })",
		"  if ($byName.Count -gt 0) { Write-StopLog (\"byname: \" + ($byName -join '; ')) }",
		"  foreach ($id in $byName) { try { Stop-Process -Id $id -Force -ErrorAction Stop; $killed += 1 } catch { $failed += 1; $failedIds += $id } }",
		"  if ($byName.Count -gt 0) { $null = Wait-PidsGone $byName 10; $probeOk = Wait-FilesFree 5 }",
		"}",
		"$stuck = @(Get-TargetPids)",
		"Write-StopLog (\"stop-sweep: killed=$killed failed=$failed stuck=$($stuck.Count) probe-ok=$probeOk\")",
		"if ($stuck.Count -gt 0) { Write-StopLog (\"stuck-procs: \" + ($stuck -join '; ')) }",
		"if ($failedIds.Count -gt 0) { Write-StopLog (\"failed-procs: \" + ($failedIds -join '; ')) }",
		"Write-Output \"killed=$killed failed=$failed stuck=$($stuck.Count) probe-ok=$probeOk\"",
	]
	return "\n".join(lines)

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
# 留给 lucky 在无影云电脑上实测（见改动清单 XLIV「未验证」）：
# - Inno 安装向导：默认路径是不是 {src}\\简历猎手、向导说明
#   文案是否清晰无乱码、改目录后不再追加子文件夹、注册表卸载项与
#   开始菜单/桌面快捷方式、装完启动；
# - 卸载向导：先停进程再删文件、用户数据保留选项默认保留、残留上报；
# - 注册表项与快捷方式在卸载后是否干净、缓存目录是否真的清空。
