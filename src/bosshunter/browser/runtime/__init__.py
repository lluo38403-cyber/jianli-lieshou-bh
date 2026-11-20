"""Built-in BossHunter Browser Runtime management."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

from bosshunter.config import DEFAULTS

_logger = logging.getLogger(__name__)

_browser_config: dict[str, Any] | None = None
_runtime_process: subprocess.Popen | None = None
_MIN_NODE_MAJOR = 22

# v25 孤儿进程清理：我们自己拉起的自动化 Chrome 的记录。
# 模块级 _launched_browser 是本进程内拉起的记录；落盘 state 文件
# （data/launched-browser.json）是给“任务管理器强杀 exe 后下次启动”
# 的清理用的——进程内记录随强杀一起丢了，state 文件还在。
# 只认我们拉起的 Chrome：杀之前必须核对命令行含专用配置目录或
# 调试端口（见 _matches_our_browser_markers），绝不按映像名全局杀。
_launched_browser: dict[str, Any] | None = None
BROWSER_STATE_FILENAME = "launched-browser.json"
INSTANCE_FILENAME = ".instance.json"

# data/ 目录显式指定（run_server 起服务时调 set_data_dir 设为
# 服务的 DATA_DIR）。没显式指定时只在 frozen 的 exe 里落盘 state
# 文件；开发环境直接调 launch_debuggable_browser 时不落盘，免得
# 测试/调试把 state 文件写进仓库的 data/。
_DATA_DIR_OVERRIDE: Path | None = None


def set_data_dir(path: str | Path | None) -> None:
    """Set the data dir used for browser/instance state files.

    run_server 在起新服务时调一次；updater/启动清理与本模块共用
    这一处取路径逻辑，不各写各的。
    """
    global _DATA_DIR_OVERRIDE
    _DATA_DIR_OVERRIDE = Path(path) if path is not None else None


def get_data_dir(data_dir: str | Path | None = None) -> Path:
    """Return the data dir for state files (shared by runtime + updater).

    优先级：显式参数 > set_data_dir > BOSSHUNTER_DATA_DIR 环境变量 >
    frozen exe 所在目录下的 data > 含 config.yaml 的仓库根目录下的
    data > 当前工作目录下的 data。
    """
    if data_dir is not None:
        return Path(data_dir)
    if _DATA_DIR_OVERRIDE is not None:
        return _DATA_DIR_OVERRIDE
    env = os.environ.get("BOSSHUNTER_DATA_DIR")
    if env:
        return Path(os.path.expandvars(env))
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config.yaml").is_file():
            return parent / "data"
    return Path.cwd() / "data"


def _state_file_enabled(data_dir: str | Path | None = None) -> bool:
    """Whether state files may be written to disk.

    只有显式指定了 data 目录（参数/set_data_dir/环境变量）或跑在
    frozen 的 exe 里才落盘；其余情况只留内存记录，不往仓库里写文件。
    """
    if data_dir is not None:
        return True
    if _DATA_DIR_OVERRIDE is not None:
        return True
    if os.environ.get("BOSSHUNTER_DATA_DIR"):
        return True
    return bool(getattr(sys, "frozen", False))


def _browser_state_path(data_dir: str | Path | None = None) -> Path:
    return get_data_dir(data_dir) / BROWSER_STATE_FILENAME


def _instance_path(data_dir: str | Path | None = None) -> Path:
    return get_data_dir(data_dir) / INSTANCE_FILENAME

# 自动化浏览器拉起后的起始页：一句中文说明这个窗口是干什么的，
# 别让它空着一张 about:blank 白屏晾在那里（用户会以为出问题了）。
# 不用面板地址：拉浏览器时不一定知道面板端口，data: 页不依赖服务。
_AUTOMATION_START_PAGE = "data:text/html," + urllib.parse.quote(
    "<meta charset=utf-8><title>简历猎手浏览器</title>"
    "<body style=\"font:16px/1.6 sans-serif;color:#555;padding:40px\">"
    "这是简历猎手的浏览器窗口：登录招聘网站、过验证码、投递都在这里进行，"
    "平时不用管它，也不用关。</body>",
    safe="",
)


def _default_browser_config() -> dict[str, Any]:
    defaults = DEFAULTS["browser"]
    result: dict[str, Any] = {}
    for key, value in defaults.items():
        result[key] = value[:] if isinstance(value, list) else value
    return result


def set_browser_config(config: dict[str, Any] | None) -> None:
    """Set process-wide browser config used by facade calls."""
    global _browser_config
    _browser_config = get_browser_config(config)


def get_browser_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return browser config merged with defaults."""
    merged = _default_browser_config()
    source = config if config is not None else (_browser_config or {})
    browser = source.get("browser", source) if isinstance(source, dict) else {}
    if isinstance(browser, dict):
        merged.update(browser)
    return merged


def get_runtime_url(config: dict[str, Any] | None = None) -> str:
    """Return configured local runtime URL."""
    browser = get_browser_config(config)
    host = browser.get("proxy_host", "127.0.0.1")
    port = browser.get("proxy_port", 3456)
    return f"http://{host}:{port}"


def get_runtime_script_path() -> Path:
    """Return bundled CDP proxy script path."""
    try:
        script = files(__package__).joinpath("cdp-proxy.mjs")
        path = Path(os.fspath(script))
        if path.exists():
            return path
    except Exception:
        pass
    return Path(__file__).with_name("cdp-proxy.mjs")


def _candidate_node_executables() -> list[str]:
    """Return Node.js candidates without depending only on the caller's PATH."""
    candidates: list[str] = []

    def add(candidate: str | Path | None) -> None:
        if not candidate:
            return
        value = os.fspath(candidate)
        if value not in candidates:
            candidates.append(value)

    # Explicit override stays first for managed and packaged installations.
    add(os.environ.get("BOSSHUNTER_NODE_PATH"))
    add(shutil.which("node"))
    # Keep the command name as a fallback so shell-managed installations and
    # existing tests continue to work even when `which` is unavailable.
    add("node")

    # Some AI desktop runtimes ship a private Node.js but do not export it to
    # child-process PATH. Reuse that executable instead of asking the user to
    # install a duplicate copy.
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        add(parent / "node" / "bin" / ("node.exe" if os.name == "nt" else "node"))

    home = Path.home()
    patterns = [
        ".cache/codex-runtimes/*/dependencies/node/bin/node",
        ".nvm/versions/node/*/bin/node",
        ".volta/bin/node",
        ".local/share/fnm/node-versions/*/installation/bin/node",
        ".local/share/mise/installs/node/*/bin/node",
        ".asdf/installs/nodejs/*/bin/node",
    ]
    for pattern in patterns:
        for candidate in sorted(home.glob(pattern), reverse=True):
            add(candidate)

    if os.name == "nt":
        for root_name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(root_name)
            if root:
                add(Path(root) / "nodejs" / "node.exe")
                add(Path(root) / "Programs" / "nodejs" / "node.exe")
    else:
        add("/opt/homebrew/bin/node")
        add("/usr/local/bin/node")
        add("/opt/local/bin/node")

    return candidates


def _node_major(version: str) -> int | None:
    match = re.match(r"^v?(\d+)", version.strip())
    return int(match.group(1)) if match else None


def check_node_available() -> dict[str, Any]:
    """Find a Node.js runtime new enough for the bundled browser component."""
    errors: list[str] = []
    for executable in _candidate_node_executables():
        if executable != "node" and not Path(executable).is_file():
            continue
        try:
            # creationflags 只在 Windows 上传：CPython 在非 Windows 上
            # 收到非零 creationflags 直接抛 ValueError（之前没加这个
            # 判断，verify 全量跑出 16 个失败，全是这一句）。
            run_kwargs: dict[str, Any] = {}
            if os.name == "nt":
                # Windows 上不加这个，node --version 会闪一下黑框。
                # fallback 写死真实值 0x08000000：Linux 上 subprocess
                # 没有这个属性，传了也会被忽略，测试还能断言到。
                run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            result = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                **run_kwargs,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            continue

        version = (result.stdout or result.stderr or "").strip()
        major = _node_major(version)
        if result.returncode == 0 and major is not None and major >= _MIN_NODE_MAJOR:
            return {
                "available": True,
                "version": version or None,
                "executable": executable,
                "error": None,
            }
        if version:
            errors.append(f"{executable}: {version}（需要 Node.js {_MIN_NODE_MAJOR}+）")

    return {
        "available": False,
        "version": None,
        "executable": None,
        "error": errors[-1] if errors else f"未找到 Node.js {_MIN_NODE_MAJOR}+",
    }


def runtime_health(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return `/health` JSON when the runtime responds."""
    try:
        response = httpx.get(f"{get_runtime_url(config)}/health", timeout=3, trust_env=False)
        if response.status_code == 200:
            return response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return None


def is_bosshunter_runtime(config: dict[str, Any] | None = None) -> bool:
    """Return True only when the local service identifies as BossHunter Runtime."""
    health = runtime_health(config)
    return bool(health and health.get("runtime") == "bosshunter")


def runtime_targets(config: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
    """Return runtime page targets when Chrome and BossHunter Runtime are ready."""
    if not is_bosshunter_runtime(config):
        return None
    try:
        response = httpx.get(f"{get_runtime_url(config)}/targets", timeout=5, trust_env=False)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return data
    except (httpx.HTTPError, ValueError):
        return None
    return None


def _is_port_available(host: str, port: int) -> bool:
    # No SO_REUSEADDR here: on Windows it allows binding ports already in use,
    # which would make this probe report occupied ports as available.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _find_available_runtime_port(browser: dict[str, Any]) -> int | None:
    host = browser.get("proxy_host", "127.0.0.1")
    start_port = int(browser.get("proxy_port", 3456))
    for port in range(start_port + 1, min(start_port + 100, 65535) + 1):
        if _is_port_available(host, port):
            return port
    return None


def _switch_runtime_port(config: dict[str, Any] | None, browser: dict[str, Any], port: int) -> None:
    global _browser_config
    browser["proxy_port"] = port
    if isinstance(config, dict):
        if isinstance(config.get("browser"), dict):
            config["browser"]["proxy_port"] = port
        else:
            config["proxy_port"] = port
    _browser_config = browser


def _runtime_env(config: dict[str, Any] | None = None) -> dict[str, str]:
    browser = get_browser_config(config)
    env = os.environ.copy()
    env["BOSSHUNTER_BROWSER_PROXY_PORT"] = str(browser.get("proxy_port", 3456))
    env["BOSSHUNTER_CHROME_PORTS"] = ",".join(str(port) for port in browser.get("chrome_ports", [9222, 9229, 9333]))
    env["BOSSHUNTER_ENABLE_PORT_GUARD"] = "true" if browser.get("enable_port_guard", True) else "false"
    return env


def get_browser_profile_dir() -> Path:
    """Return the dedicated profile dir for the browser we launch ourselves.

    专用配置目录（不是用户日常 Chrome 的默认配置）：用户日常 Chrome
    可能已经在运行，单实例机制下再拉一个 chrome.exe 只会在已有实例里
    开窗口，--remote-debugging-port 不生效；专用目录保证每次都能拉起
    一个真正带调试端口的实例。登录态就存在这个目录里，重启仍在。
    """
    override = os.environ.get("BOSSHUNTER_BROWSER_PROFILE_DIR")
    if override:
        return Path(os.path.expandvars(override))
    return Path.home() / ".jianli-lieshou" / "browser-profile"


def pick_chrome_debug_port(config: dict[str, Any] | None = None) -> int | None:
    """Return the debug port our browser instance should use (v27).

    An already-listening configured port wins (reuse the running instance,
    ours or the user's); otherwise the first free configured port; None
    when every configured port is busy without answering. The app window
    and the work browser share this pick, so both land in one Chrome
    instance instead of two taskbar icons.
    """
    browser = get_browser_config(config)
    ports = [int(port) for port in browser.get("chrome_ports", [9222, 9229, 9333])]
    for port in ports:
        if _chrome_debug_version(port) is not None:
            return port
    for port in ports:
        if _is_port_available("127.0.0.1", port):
            return port
    return None


def _chrome_debug_version(port: int) -> dict[str, Any] | None:
    """Return /json/version when a debuggable Chrome answers on this port."""
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=2, trust_env=False)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data.get("webSocketDebuggerUrl"):
                return data
    except (httpx.HTTPError, ValueError):
        return None
    return None


def _launch_kwargs() -> dict[str, Any]:
    """Detached-process kwargs shared by the proxy and the browser launch.

    Windows 上再加 CREATE_NO_WINDOW：detached 的进程自己没有控制台，
    但它拉起的控制台子程序会各自新开黑框；CREATE_NO_WINDOW 让整条
    链都不开窗口（node --version 探测、cdp-proxy、Chrome 拉起都走这里）。
    """
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        # getattr fallback 写死真实值：Linux 上 subprocess 没有这些
        # 属性，测试里把 os.name 扮成 nt 时也不会炸。
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True
    return kwargs


def launch_debuggable_browser(config: dict[str, Any] | None = None, target_url: str | None = None) -> bool:
    """Launch the selected browser with a debug port when nothing debuggable is up.

    先看配置的 chrome_ports 有没有已经在监听的调试端口（用户自己拉的
    也认）；没有才按 browser.browser_choice 解析到的启动文件拉一个：
    专用配置目录 + --remote-debugging-port。手动路径优先于自动检测；
    所选浏览器找不到时按 Chrome→Edge 回落（见 resolve_browser_executable），
    仍找不到返回 False，调用方给面板中文提示，不要静默失败。
    拉起后等调试端口应答才算成功。用户关了自动拉起
    （browser.auto_launch_browser=false）或运行组件本身就不自启时不动。

    target_url：拉起后直接打开的目标页（去登录的登录页、任务页地址）；
    不给时才用一句中文说明的 data: 起始页兜底，不再先停在任何起始页。
    注意：不加 --disable-blink-features=AutomationControlled——CDP 直连
    的真人 Chrome 里 navigator.webdriver 本来就是 false（见 antibot.py），
    这参数除了让 Chrome 顶上挂一条“您使用的是不受支持的命令行标记”
    警告，什么都没多做。起始页也不用 about:blank，改成一句中文说明，
    免得空闲时一张白屏晾在那里。
    """
    browser = get_browser_config(config)
    if browser.get("runtime") != "builtin" or not browser.get("auto_start_proxy", True):
        return False
    if not browser.get("auto_launch_browser", True):
        return False
    # v27：端口选择走 pick_chrome_debug_port，与 --app 窗口共用同一
    # 个 pick（已在监听的端口优先复用，否则第一个空闲端口）。
    port = pick_chrome_debug_port(config)
    if port is None:
        return False
    if _chrome_debug_version(port) is not None:
        return True
    # diagnostics.py 在模块层 import 本模块，模块层反向 import 会循环；
    # 调用时两边都已加载完成，函数内 import 安全。
    from bosshunter.browser.diagnostics import resolve_browser_executable

    resolved = resolve_browser_executable(config)
    executable = resolved.get("executable")
    if not executable:
        return False
    profile_dir = get_browser_profile_dir()
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    start_page = target_url if isinstance(target_url, str) and target_url.startswith(("http://", "https://")) else _AUTOMATION_START_PAGE
    try:
        proc = subprocess.Popen(
            [
                executable,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                start_page,
            ],
            **_launch_kwargs(),
        )
    except OSError:
        return False
    # 记下我们拉起的这个 Chrome（PID + 配置目录 + 调试端口）：正常
    # 退出时 shutdown_browser_resources 按这条记录停它；进程被强杀
    # 后下次启动按落盘的 state 文件清它。端口已在监听时提前 return
    # True 的那个 Chrome 不是我们拉的，不记、不碰。
    _record_launched_browser(proc, profile_dir, port, executable)
    deadline = time.time() + 15.0
    while True:
        if _chrome_debug_version(port) is not None:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _is_windows() -> bool:
    """是不是 Windows。单独成函数是为了测试能扮成 Windows 走一遍：
    直接 patch os.name 会连 pathlib.Path 一起改成 WindowsPath，
    在 Linux 上根本建不出来（与 updater.py 的同名 helper 同理）。"""
    return os.name == "nt"


def start_runtime(config: dict[str, Any] | None = None, node_executable: str = "node") -> subprocess.Popen:
    """Start the bundled Node.js browser runtime."""
    global _runtime_process
    script_path = get_runtime_script_path()
    kwargs: dict[str, Any] = {
        "env": _runtime_env(config),
        **_launch_kwargs(),
    }
    _runtime_process = subprocess.Popen([node_executable, str(script_path)], **kwargs)
    return _runtime_process


def stop_runtime(timeout: float = 10.0) -> bool:
    """Stop the Browser Runtime started by start_runtime().

    只停我们自己拉起的那个 node 子进程（_runtime_process）及其后代：
    用户自己的浏览器绝不是它的子进程——launch_debuggable_browser
    是另外拉起的、用户自己拉的更不是；cdp-proxy.mjs 自己也不拉
    浏览器（只连 CDP），所以按进程树杀不会碰到用户浏览器。
    杀树的原因：更新换文件时，_internal 里被孤儿 node 占住的文件
    （如 patchright driver 的 node.exe）会让 robocopy 换不上去；
    更新前先停掉 runtime，bat 的等待/强制结束循环是后备。

    幂等：没拉起过、已经退了、重复调用都返回 True。失败只记日志、
    不抛异常——apply_update 起 bat 之前调它，停不掉也不能挡更新。
    """
    global _runtime_process
    proc = _runtime_process
    _runtime_process = None
    if proc is None:
        return True
    try:
        if proc.poll() is not None:
            return True
    except Exception as exc:
        _logger.warning("stop_runtime: 检查 runtime 进程状态失败：%s", exc)
        return False
    try:
        if _is_windows():
            # Windows 上按进程树杀：runtime node 下面可能还有子进程；
            # 只认我们拉起的这个 PID 的树，不碰别的 node/浏览器。
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                )
            except Exception as exc:
                _logger.warning("stop_runtime: taskkill 失败：%s", exc)
        else:
            # POSIX：start_runtime 用 start_new_session 拉起，先温柔停。
            try:
                proc.terminate()
            except Exception as exc:
                _logger.warning("stop_runtime: terminate 失败：%s", exc)
        try:
            proc.wait(timeout=max(0.0, float(timeout)))
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception as exc:
                _logger.warning("stop_runtime: kill 失败：%s", exc)
            try:
                proc.wait(timeout=5)
            except Exception as exc:
                _logger.warning("stop_runtime: 等 runtime 进程退出失败：%s", exc)
                return False
        return proc.poll() is not None
    except Exception as exc:
        _logger.warning("stop_runtime: 停 runtime 进程失败：%s", exc)
        return False


def _wait_for_targets(browser: dict[str, Any], wait_seconds: float) -> bool:
    """Poll the runtime until Chrome targets appear (at least one probe)."""
    deadline = time.time() + wait_seconds
    while True:
        if runtime_targets(browser) is not None:
            set_browser_config(browser)
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


# ─── v25：孤儿进程清理 ─────────────────────────────────────────
# 根因：正常退出路径没人停运行组件 node 树和我们拉起的自动化 Chrome，
# os._exit 又让子进程一律变孤儿。统一清理入口 shutdown_browser_resources
# 由正常退出（atexit/信号/关 --app 窗口）与更新重启前显式调用；
# 任务管理器强杀 exe 时进程内清理跑不了，靠下次启动的
# cleanup_stale_browser_resources 兜底。
# 所有“杀”操作只认我们自己的标记，绝不按映像名全局杀 chrome.exe /
# node.exe，绝不碰用户自己的浏览器。


def _process_info(pid: int) -> dict[str, Any] | None:
    """Return {"executable", "cmdline"} for a pid, or None when it is gone.

    Windows 用 PowerShell 查 Win32_Process；POSIX 读 /proc。查不到
    （进程已死、权限不足）返回 None，调用方按“已不在”处理，绝不
    凭一个裸 PID 就动手。
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    if _is_windows():
        script = (
            "$p = Get-CimInstance Win32_Process -Filter "
            f'"ProcessId={pid}"; '
            "if ($p) { $p | Select-Object ExecutablePath,CommandLine "
            "| ConvertTo-Json -Compress }"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
        except Exception:
            return None
        text = (result.stdout or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return {
            "executable": data.get("ExecutablePath") or "",
            "cmdline": data.get("CommandLine") or "",
        }
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        exe = ""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return {"executable": exe, "cmdline": raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()}


def _pid_alive(pid: int) -> bool:
    """Return True when the pid still exists."""
    if _is_windows():
        return _process_info(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _matches_our_browser_markers(cmdline: str, profile_dir: Any, port: Any) -> bool:
    """True only when the cmdline carries our dedicated profile or debug port."""
    text = cmdline or ""
    if profile_dir:
        try:
            if str(profile_dir) and str(profile_dir) in text:
                return True
        except Exception:
            pass
    if port is not None:
        try:
            if f"remote-debugging-port={int(port)}" in text:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _kill_process_tree(pid: int, timeout: float = 10.0) -> bool:
    """Kill a pid and its descendants; True when it is gone afterwards.

    Windows：taskkill /PID /T /F（只认这一个 PID 的树）；POSIX：先
    SIGTERM 整进程组（拉起时 start_new_session），等几秒、超时再
    SIGKILL。失败只记日志、不抛异常。
    """
    if not _pid_alive(pid):
        return True
    try:
        if _is_windows():
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                )
            except Exception as exc:
                _logger.warning("_kill_process_tree: taskkill 失败（pid=%s）：%s", pid, exc)
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError as exc:
                    _logger.warning("_kill_process_tree: terminate 失败（pid=%s）：%s", pid, exc)
            deadline = time.time() + max(0.0, float(timeout))
            while _pid_alive(pid) and time.time() < deadline:
                time.sleep(0.2)
            if _pid_alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError as exc:
                        _logger.warning("_kill_process_tree: kill 失败（pid=%s）：%s", pid, exc)
    except Exception as exc:
        _logger.warning("_kill_process_tree: 停进程失败（pid=%s）：%s", pid, exc)
        return False
    deadline = time.time() + max(0.0, float(timeout))
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.2)
    return not _pid_alive(pid)


def _record_launched_browser(proc: subprocess.Popen, profile_dir: Path, port: int, executable: str) -> None:
    """Remember a Chrome we launched ourselves (memory + state file)."""
    global _launched_browser
    record = {
        "pid": proc.pid,
        "executable": executable,
        "profile_dir": str(profile_dir),
        "port": int(port),
        "proc": proc,
    }
    _launched_browser = record
    if not _state_file_enabled():
        return
    try:
        path = _browser_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: value for key, value in record.items() if key != "proc"}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        _logger.warning("_record_launched_browser: 写 state 文件失败：%s", exc)


def _read_browser_state(data_dir: str | Path | None = None) -> dict[str, Any] | None:
    try:
        path = _browser_state_path(data_dir)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _clear_browser_state(data_dir: str | Path | None = None) -> None:
    try:
        path = _browser_state_path(data_dir)
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def stop_launched_browser(timeout: float = 10.0) -> bool:
    """Stop the automation Chrome we launched ourselves, nothing else.

    候选只来自两处：本进程的 _launched_browser 记录、落盘的 state
    文件。动手前先按 PID 查命令行，含专用配置目录或调试端口才杀；
    命令行对不上（PID 被复用、或根本不是我们的 Chrome）就跳过、
    只记日志。幂等：没拉起过、已经退了、重复调用都返回 True。
    """
    global _launched_browser
    candidates: list[dict[str, Any]] = []
    if _launched_browser is not None:
        candidates.append(_launched_browser)
    state = _read_browser_state()
    if state and all(state.get("pid") != cand.get("pid") for cand in candidates):
        candidates.append(state)
    _launched_browser = None
    if not candidates:
        _clear_browser_state()
        return True
    ok = True
    for cand in candidates:
        pid = cand.get("pid")
        proc = cand.get("proc")
        if proc is not None:
            try:
                if proc.poll() is not None:
                    continue
            except Exception:
                pass
        if not isinstance(pid, int) or pid <= 0:
            ok = False
            continue
        info = _process_info(pid)
        if info is None:
            continue  # 进程已经不在了，不用杀。
        if not _matches_our_browser_markers(info.get("cmdline") or "", cand.get("profile_dir"), cand.get("port")):
            _logger.warning(
                "stop_launched_browser: pid=%s 的命令行不含我们的标记，不杀：%s",
                pid, (info.get("cmdline") or "")[:200],
            )
            ok = False
            continue
        if not _kill_process_tree(pid, timeout):
            _logger.warning("stop_launched_browser: pid=%s 没能停掉", pid)
            ok = False
    _clear_browser_state()
    return ok


def write_instance_file(data_dir: str | Path | None = None, pid: int | None = None, port: int = 8686) -> bool:
    """Record this service instance (pid + exe) in data/.instance.json.

    只有显式给了 data 目录或 state 文件可落盘时才写；写失败不挡启动。
    """
    if data_dir is None and not _state_file_enabled():
        return False
    try:
        path = _instance_path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": int(pid if pid is not None else os.getpid()),
            "executable": str(Path(sys.executable).resolve()),
            "port": int(port),
            "started_at": time.time(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return True
    except (OSError, ValueError) as exc:
        _logger.warning("write_instance_file: 写实例文件失败：%s", exc)
        return False


def _read_instance_file(data_dir: str | Path | None = None) -> dict[str, Any] | None:
    try:
        path = _instance_path(data_dir)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def clear_instance_file(data_dir: str | Path | None = None) -> None:
    """Remove the instance file (normal shutdown); never raises."""
    try:
        path = _instance_path(data_dir)
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def _is_pid_our_exe(pid: int) -> bool:
    """True when pid is alive and belongs to our own exe/process.

    frozen 的 exe：ExecutablePath 必须等于当前 exe；开发环境：
    命令行里必须出现 bosshunter / jianli-lieshou（光是同一个
    python.exe 不算——别的 Python 程序也可能是它）。
    """
    info = _process_info(pid)
    if info is None:
        return False
    if getattr(sys, "frozen", False):
        exe = info.get("executable") or ""
        try:
            return bool(exe) and Path(exe).resolve() == Path(sys.executable).resolve()
        except OSError:
            return False
    cmdline = info.get("cmdline") or ""
    return "bosshunter" in cmdline or "jianli-lieshou" in cmdline


def _kill_processes_with_exe_under(install_dir: Path) -> bool:
    """POSIX fallback: kill processes whose exe lives under install_dir.

    Windows 走 updater._kill_install_dir_processes（与 bat 同款
    ExecutablePath 前缀规则）；这里只处理 /proc 能看到的系统。
    跳过自己。任何一步失败都只记日志、不抛异常。
    """
    if _is_windows():
        return False
    try:
        root = install_dir.resolve()
    except OSError:
        return False
    killed_any = False
    try:
        pids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return False
    for entry in pids:
        pid = int(entry)
        if pid == os.getpid():
            continue
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            continue
        try:
            if Path(exe).resolve().is_relative_to(root):
                if _kill_process_tree(pid):
                    killed_any = True
        except (OSError, ValueError):
            continue
    return killed_any


def cleanup_stale_browser_resources(data_dir: str | Path | None = None, port: int = 8686, install_dir: str | Path | None = None) -> bool:
    """Startup sweep for leftovers of a previous run that was force-killed.

    只在确认没有活着的本程序实例后才动手，判定两者结合（任一成立
    就视为有实例在跑，跳过清理，绝不杀活实例）：
    ① data/.instance.json 里记的 PID 还活着且是我们的 exe；
    ② 我们自己的服务在当前端口上能应答（/api/health）。
    确认无实例后才清：杀安装目录下残留进程（ExecutablePath 在安装
    目录下，与 bat 同款规则）+ 按 state 文件杀我们拉起的 Chrome
    （同样先核对命令行）。必须在拉起任何新 runtime 之前跑、只跑一次；
    清完删掉陈旧 state 文件与实例文件。返回 True 表示清掉了东西。
    """
    resolved = get_data_dir(data_dir)
    try:
        from bosshunter.app_window import is_our_service_alive

        if is_our_service_alive("127.0.0.1", port):
            return False
    except Exception:
        pass
    instance = _read_instance_file(resolved)
    if instance is not None:
        pid = instance.get("pid")
        if isinstance(pid, int) and _is_pid_our_exe(pid):
            return False
    cleaned = False
    target: Path | None = None
    if install_dir is not None:
        target = Path(install_dir)
    elif getattr(sys, "frozen", False):
        target = Path(sys.executable).resolve().parent
    if target is not None:
        try:
            if _is_windows():
                from bosshunter.updater import _kill_install_dir_processes

                # 排除当前 PID：启动清理时当前进程自己就是安装目录里
                # 的新 exe，不排除会自杀。换文件的 bat 是 cmd.exe、
                # 不在安装目录里，不会被这条规则碰到。
                if _kill_install_dir_processes(target, exclude_pid=os.getpid()):
                    cleaned = True
            else:
                if _kill_processes_with_exe_under(target):
                    cleaned = True
        except Exception as exc:
            _logger.warning("cleanup_stale_browser_resources: 清安装目录残留失败：%s", exc)
    had_browser_state = _read_browser_state(resolved) is not None or _launched_browser is not None
    try:
        stop_launched_browser()
    except Exception as exc:
        _logger.warning("cleanup_stale_browser_resources: 清残留 Chrome 失败：%s", exc)
    else:
        cleaned = cleaned or had_browser_state
    if instance is not None:
        clear_instance_file(resolved)
        cleaned = True
    return cleaned


def shutdown_browser_resources(timeout: float = 10.0) -> bool:
    """Unified cleanup for every normal exit path: runtime + our Chrome.

    停运行组件 node 进程树（stop_runtime）+ 停我们拉起的自动化
    Chrome（stop_launched_browser），再清实例文件。幂等、失败只记
    日志不抛异常——atexit/信号处理里抛异常只会把退出本身搞砸。
    """
    ok = True
    try:
        if not stop_runtime(timeout):
            ok = False
    except Exception as exc:
        _logger.warning("shutdown_browser_resources: 停 runtime 失败：%s", exc)
        ok = False
    try:
        if not stop_launched_browser(timeout):
            ok = False
    except Exception as exc:
        _logger.warning("shutdown_browser_resources: 停自动化 Chrome 失败：%s", exc)
        ok = False
    try:
        clear_instance_file()
    except Exception:
        pass
    return ok


def ensure_runtime(config: dict[str, Any] | None = None, wait_seconds: float = 15.0, allow_launch: bool = True, target_url: str | None = None) -> bool:
    """Ensure the local Browser Runtime is ready for page operations.

    启动连接失败时自动重试一次（重试间隔 3 秒），重试仍失败才返回错误；
    端口回退逻辑不变。配置本身就不允许自启（runtime 非 builtin 或
    auto_start_proxy 关闭）时不重试——那不是一次可以恢复的失败。
    allow_launch=False 时只连已有的可调试浏览器，绝不替用户拉新窗口
    （后台巡检、检测用）；重试那次也不再重复拉起，刚拉起的浏览器还在启动中。
    target_url：需要拉起时直接打开的目标页（去登录的登录页）；不给时
    才用 data: 说明起始页兜底。
    """
    if _ensure_runtime_attempt(config, wait_seconds, allow_launch, target_url):
        return True
    browser = get_browser_config(config)
    if browser.get("runtime") != "builtin" or not browser.get("auto_start_proxy", True):
        return False
    time.sleep(3)
    return _ensure_runtime_attempt(config, wait_seconds, False)


def _ensure_runtime_attempt(config: dict[str, Any] | None = None, wait_seconds: float = 15.0, allow_launch: bool = True, target_url: str | None = None) -> bool:
    """One attempt to bring the local Browser Runtime up."""
    browser = get_browser_config(config)
    if runtime_targets(browser) is not None:
        set_browser_config(browser)
        return True
    if browser.get("runtime") != "builtin":
        return False
    if not browser.get("auto_start_proxy", True):
        return False
    if not allow_launch:
        # v25：检测模式绝不为检测拉起任何东西——不调 start_runtime、
        # 不调 launch_debuggable_browser，只等现成的 runtime 在
        # wait_seconds 内出现。ensure_runtime 的重试那次传的就是
        # False：第一次（True）已经把 runtime 拉起来了，重试只等
        # targets 出现，不受影响；第一次在 start_runtime 之前就失败
        # 时，重试也只是等一轮、返回 False，和以前结果一致。
        return _wait_for_targets(browser, wait_seconds)
    node = check_node_available()
    if not node.get("available"):
        return False

    health = runtime_health(browser)
    host = browser.get("proxy_host", "127.0.0.1")
    port = int(browser.get("proxy_port", 3456))
    # Swap ports when the configured one serves a foreign runtime, or when
    # nothing responds and the port cannot be bound at all (e.g. Windows
    # reserved port ranges that shift after every reboot).
    needs_fallback = (health is not None and health.get("runtime") != "bosshunter") or (
        health is None and not _is_port_available(host, port)
    )
    if needs_fallback:
        fallback_port = _find_available_runtime_port(browser)
        if fallback_port is None:
            return False
        _switch_runtime_port(config, browser, fallback_port)

    start_runtime(browser, str(node.get("executable") or "node"))
    # 本来就要拉浏览器时不等满 15 秒：等 5 秒看有没有现成可调试的，
    # 没有就直接拉起；不拉时保持原来的等待时长。
    first_wait = min(wait_seconds, 5.0) if wait_seconds > 0 else wait_seconds
    if _wait_for_targets(browser, first_wait):
        return True
    if launch_debuggable_browser(browser, target_url=target_url):
        return _wait_for_targets(browser, wait_seconds)
    return False


__all__ = [
    "set_browser_config",
    "get_browser_config",
    "get_runtime_url",
    "get_runtime_script_path",
    "get_browser_profile_dir",
    "pick_chrome_debug_port",
    "launch_debuggable_browser",
    "check_node_available",
    "runtime_health",
    "is_bosshunter_runtime",
    "runtime_targets",
    "start_runtime",
    "stop_runtime",
    "ensure_runtime",
    "set_data_dir",
    "get_data_dir",
    "stop_launched_browser",
    "shutdown_browser_resources",
    "write_instance_file",
    "clear_instance_file",
    "cleanup_stale_browser_resources",
]
