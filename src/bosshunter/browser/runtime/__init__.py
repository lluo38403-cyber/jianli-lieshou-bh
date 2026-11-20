"""Built-in BossHunter Browser Runtime management."""

from __future__ import annotations

import os
import re
import shutil
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

_browser_config: dict[str, Any] | None = None
_runtime_process: subprocess.Popen | None = None
_MIN_NODE_MAJOR = 22

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
    ports = [int(port) for port in browser.get("chrome_ports", [9222, 9229, 9333])]
    for port in ports:
        if _chrome_debug_version(port) is not None:
            return True
    # diagnostics.py 在模块层 import 本模块，模块层反向 import 会循环；
    # 调用时两边都已加载完成，函数内 import 安全。
    from bosshunter.browser.diagnostics import resolve_browser_executable

    resolved = resolve_browser_executable(config)
    executable = resolved.get("executable")
    if not executable:
        return False
    port = None
    for candidate in ports:
        if _is_port_available("127.0.0.1", candidate):
            port = candidate
            break
    if port is None:
        return False
    profile_dir = get_browser_profile_dir()
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    start_page = target_url if isinstance(target_url, str) and target_url.startswith(("http://", "https://")) else _AUTOMATION_START_PAGE
    try:
        subprocess.Popen(
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
    deadline = time.time() + 15.0
    while True:
        if _chrome_debug_version(port) is not None:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


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
    first_wait = wait_seconds if (not allow_launch or wait_seconds <= 0) else min(wait_seconds, 5.0)
    if _wait_for_targets(browser, first_wait):
        return True
    if allow_launch and launch_debuggable_browser(browser, target_url=target_url):
        return _wait_for_targets(browser, wait_seconds)
    return False


__all__ = [
    "set_browser_config",
    "get_browser_config",
    "get_runtime_url",
    "get_runtime_script_path",
    "get_browser_profile_dir",
    "launch_debuggable_browser",
    "check_node_available",
    "runtime_health",
    "is_bosshunter_runtime",
    "runtime_targets",
    "start_runtime",
    "ensure_runtime",
]
